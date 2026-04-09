"""Tests for :mod:`kiassist_utils.kicad_parser.analyzer`.

Covers symbol library analysis, footprint analysis, auto-fixing, CLI,
edge cases, and round-trip integrity.
"""

from __future__ import annotations

import json
import os
import textwrap
import uuid
from pathlib import Path

import pytest

from kiassist_utils.kicad_parser.analyzer import (
    AnalysisReport,
    Issue,
    IssueCategory,
    LibraryAnalyzer,
    Severity,
    _FootprintChecks,
    _FootprintFixer,
    _SymbolChecks,
    _SymbolFixer,
    main,
)
from kiassist_utils.kicad_parser.footprint import Footprint, FootprintGraphic, Pad
from kiassist_utils.kicad_parser.models import Effects, Position, Property
from kiassist_utils.kicad_parser._helpers import _find
from kiassist_utils.kicad_parser.sexpr import QStr, parse, serialize
from kiassist_utils.kicad_parser.symbol_lib import Pin, SymbolDef, SymbolLibrary, SymbolUnit

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _minimal_symbol_lib_text(
    version: int = 20231120,
    generator: str = "kicad_symbol_editor",
    symbols: str = "",
) -> str:
    """Build a minimal valid .kicad_sym file content."""
    return textwrap.dedent(f"""\
        (kicad_symbol_lib (version {version}) (generator "{generator}")
          {symbols}
        )
    """)


def _make_symbol_text(
    name: str = "R",
    *,
    pin_numbers_hide: bool = True,
    pin_names_offset: float = 0.0,
    extra_props: str = "",
    extra_body: str = "",
    exclude_from_sim: bool = True,
    in_bom: bool = True,
    on_board: bool = True,
) -> str:
    """Build text for a single symbol definition."""
    hide = '(pin_numbers (hide yes))' if pin_numbers_hide else ''
    efs = f"(exclude_from_sim {'yes' if exclude_from_sim else 'no'})" if exclude_from_sim is not None else ""
    ibm = f"(in_bom {'yes' if in_bom else 'no'})" if in_bom is not None else ""
    ob = f"(on_board {'yes' if on_board else 'no'})" if on_board is not None else ""
    return textwrap.dedent(f"""\
        (symbol "{name}"
          {hide}
          (pin_names (offset {pin_names_offset}))
          {efs}
          {ibm}
          {ob}
          (property "Reference" "R" (at 2.032 0 90)
            (effects (font (size 1.27 1.27)))
          )
          (property "Value" "{name}" (at 0 0 90)
            (effects (font (size 1.27 1.27)))
          )
          (property "Footprint" "" (at 0 0 0)
            (effects (font (size 1.27 1.27)) (hide yes))
          )
          (property "Datasheet" "~" (at 0 0 0)
            (effects (font (size 1.27 1.27)) (hide yes))
          )
          {extra_props}
          (symbol "{name}_0_1"
            (rectangle (start -1.016 -2.54) (end 1.016 2.54)
              (stroke (width 0.254) (type default))
              (fill (type none))
            )
          )
          (symbol "{name}_1_1"
            (pin passive line (at 0 3.81 270) (length 1.524)
              (name "~" (effects (font (size 1.27 1.27))))
              (number "1" (effects (font (size 1.27 1.27))))
            )
            (pin passive line (at 0 -3.81 90) (length 1.524)
              (name "~" (effects (font (size 1.27 1.27))))
              (number "2" (effects (font (size 1.27 1.27))))
            )
          )
          {extra_body}
        )
    """)


def _minimal_footprint_text(
    name: str = "R_0402",
    layer: str = "F.Cu",
    pads: str = "",
    graphics: str = "",
    extra: str = "",
) -> str:
    """Build a minimal valid .kicad_mod file content."""
    return textwrap.dedent(f"""\
        (footprint "{name}" (layer "{layer}")
          (descr "Test footprint")
          (tags "test")
          (attr smd)
          (fp_text reference "R**" (at 0 -1.43) (layer "F.SilkS")
            (effects (font (size 1 1) (thickness 0.15)))
          )
          (fp_text value "{name}" (at 0 1.43) (layer "F.Fab")
            (effects (font (size 1 1) (thickness 0.15)))
          )
          (fp_line (start -1.88 -0.98) (end 1.88 -0.98) (layer "F.CrtYd") (stroke (width 0.05) (type solid)))
          (fp_line (start -1.88 0.98) (end 1.88 0.98) (layer "F.CrtYd") (stroke (width 0.05) (type solid)))
          (fp_line (start -0.25 -0.5) (end 0.25 -0.5) (layer "F.Fab") (stroke (width 0.1) (type solid)))
          (fp_line (start 0.25 0.5) (end -0.25 0.5) (layer "F.Fab") (stroke (width 0.1) (type solid)))
          {graphics}
          {pads}
          {extra}
        )
    """)


# ═══════════════════════════════════════════════════════════════════════════
# AnalysisReport tests
# ═══════════════════════════════════════════════════════════════════════════


class TestAnalysisReport:
    """Tests for the AnalysisReport dataclass."""

    def test_empty_report(self):
        r = AnalysisReport(file_path="x.kicad_sym", file_type="symbol_library")
        assert r.is_clean
        assert r.errors == []
        assert r.warnings == []
        assert r.infos == []
        assert r.fixable == []

    def test_accessors(self):
        r = AnalysisReport()
        r.issues = [
            Issue(Severity.ERROR, IssueCategory.PIN, "A", "bad pin"),
            Issue(Severity.WARNING, IssueCategory.PROPERTY, "A", "missing prop", fixable=True),
            Issue(Severity.INFO, IssueCategory.UUID, "B", "info"),
        ]
        assert len(r.errors) == 1
        assert len(r.warnings) == 1
        assert len(r.infos) == 1
        assert len(r.fixable) == 1
        assert not r.is_clean

    def test_by_category(self):
        r = AnalysisReport()
        r.issues = [
            Issue(Severity.ERROR, IssueCategory.PIN, "A", "x"),
            Issue(Severity.WARNING, IssueCategory.PIN, "B", "y"),
            Issue(Severity.INFO, IssueCategory.UUID, "C", "z"),
        ]
        assert len(r.by_category(IssueCategory.PIN)) == 2
        assert len(r.by_category(IssueCategory.UUID)) == 1

    def test_by_entity(self):
        r = AnalysisReport()
        r.issues = [
            Issue(Severity.ERROR, IssueCategory.PIN, "SYM1", "x"),
            Issue(Severity.WARNING, IssueCategory.PIN, "SYM1", "y"),
            Issue(Severity.INFO, IssueCategory.UUID, "SYM2", "z"),
        ]
        assert len(r.by_entity("SYM1")) == 2

    def test_summary(self):
        r = AnalysisReport(file_path="test.kicad_sym", file_type="symbol_library")
        r.issues = [Issue(Severity.ERROR, IssueCategory.PIN, "R", "bad")]
        s = r.summary()
        assert "test.kicad_sym" in s
        assert "E=1" in s

    def test_to_dict(self):
        r = AnalysisReport(file_path="x", file_type="symbol_library")
        r.issues = [Issue(Severity.ERROR, IssueCategory.PIN, "R", "bad", fixable=True, fix_action="fix_it")]
        d = r.to_dict()
        assert d["total"] == 1
        assert d["errors"] == 1
        assert d["fixable"] == 1
        assert d["issues"][0]["fix_action"] == "fix_it"


# ═══════════════════════════════════════════════════════════════════════════
# Symbol library checks
# ═══════════════════════════════════════════════════════════════════════════


class TestSymbolVersionCheck:
    def test_missing_version(self):
        lib = SymbolLibrary(version=0)
        report = AnalysisReport()
        _SymbolChecks.check_version(lib, report)
        assert len(report.errors) == 1
        assert report.errors[0].fix_action == "set_version"

    def test_old_version(self):
        lib = SymbolLibrary(version=20100101)
        report = AnalysisReport()
        _SymbolChecks.check_version(lib, report)
        assert len(report.errors) == 1
        assert "too old" in report.errors[0].message

    def test_outdated_version(self):
        lib = SymbolLibrary(version=20211014)
        report = AnalysisReport()
        _SymbolChecks.check_version(lib, report)
        assert len(report.warnings) == 1

    def test_current_version(self):
        lib = SymbolLibrary(version=20231120)
        report = AnalysisReport()
        _SymbolChecks.check_version(lib, report)
        assert len(report.issues) == 0


class TestSymbolGeneratorCheck:
    def test_missing_generator(self):
        lib = SymbolLibrary(generator="")
        report = AnalysisReport()
        _SymbolChecks.check_generator(lib, report)
        assert len(report.warnings) == 1

    def test_present_generator(self):
        lib = SymbolLibrary(generator="kicad_symbol_editor")
        report = AnalysisReport()
        _SymbolChecks.check_generator(lib, report)
        assert len(report.issues) == 0


class TestSymbolDuplicateNames:
    def test_no_duplicates(self):
        lib = SymbolLibrary(symbols=[SymbolDef(name="A"), SymbolDef(name="B")])
        report = AnalysisReport()
        _SymbolChecks.check_duplicate_symbols(lib, report)
        assert len(report.issues) == 0

    def test_duplicate(self):
        lib = SymbolLibrary(symbols=[SymbolDef(name="A"), SymbolDef(name="A")])
        report = AnalysisReport()
        _SymbolChecks.check_duplicate_symbols(lib, report)
        assert len(report.errors) == 1
        assert "Duplicate" in report.errors[0].message


class TestSymbolNameCheck:
    def test_empty_name(self):
        sym = SymbolDef(name="")
        report = AnalysisReport()
        _SymbolChecks.check_symbol_name(sym, report)
        assert len(report.errors) == 1

    def test_forbidden_chars(self):
        sym = SymbolDef(name='My:Symbol*Bad?"')
        report = AnalysisReport()
        _SymbolChecks.check_symbol_name(sym, report)
        assert len(report.errors) == 1
        assert report.errors[0].fixable

    def test_valid_name(self):
        sym = SymbolDef(name="NE555")
        report = AnalysisReport()
        _SymbolChecks.check_symbol_name(sym, report)
        assert len(report.issues) == 0


class TestRequiredProperties:
    def test_all_present(self):
        sym = SymbolDef(name="R", properties=[
            Property("Reference", "R"),
            Property("Value", "R"),
            Property("Footprint", ""),
            Property("Datasheet", "~"),
        ])
        report = AnalysisReport()
        _SymbolChecks.check_required_properties(sym, report)
        assert len(report.issues) == 0

    def test_missing_footprint(self):
        sym = SymbolDef(name="R", properties=[
            Property("Reference", "R"),
            Property("Value", "R"),
            Property("Datasheet", "~"),
        ])
        report = AnalysisReport()
        _SymbolChecks.check_required_properties(sym, report)
        assert len(report.errors) == 1
        assert "Footprint" in report.errors[0].message

    def test_missing_all(self):
        sym = SymbolDef(name="R", properties=[])
        report = AnalysisReport()
        _SymbolChecks.check_required_properties(sym, report)
        assert len(report.errors) == 4


class TestPropertyValues:
    def test_empty_reference(self):
        sym = SymbolDef(name="X", properties=[Property("Reference", "")])
        report = AnalysisReport()
        _SymbolChecks.check_property_values(sym, report)
        assert any("Reference" in i.message for i in report.issues)

    def test_duplicate_keys(self):
        sym = SymbolDef(name="X", properties=[
            Property("Value", "a"),
            Property("Value", "b"),
        ])
        report = AnalysisReport()
        _SymbolChecks.check_property_values(sym, report)
        assert any("Duplicate" in i.message for i in report.issues)


class TestPinElectricalTypeCheck:
    def test_valid(self):
        sym = SymbolDef(name="R", units=[
            SymbolUnit(pins=[Pin(electrical_type="passive", number="1")])
        ])
        report = AnalysisReport()
        _SymbolChecks.check_pin_electrical_types(sym, report)
        assert len(report.issues) == 0

    def test_invalid(self):
        sym = SymbolDef(name="R", units=[
            SymbolUnit(pins=[Pin(electrical_type="bogus", number="1")])
        ])
        report = AnalysisReport()
        _SymbolChecks.check_pin_electrical_types(sym, report)
        assert len(report.errors) == 1


class TestPinGraphicStyleCheck:
    def test_valid(self):
        sym = SymbolDef(name="R", units=[
            SymbolUnit(pins=[Pin(graphic_style="line", number="1")])
        ])
        report = AnalysisReport()
        _SymbolChecks.check_pin_graphic_styles(sym, report)
        assert len(report.issues) == 0

    def test_invalid(self):
        sym = SymbolDef(name="R", units=[
            SymbolUnit(pins=[Pin(graphic_style="fancy", number="1")])
        ])
        report = AnalysisReport()
        _SymbolChecks.check_pin_graphic_styles(sym, report)
        assert len(report.errors) == 1


class TestDuplicatePinNumbers:
    def test_no_dups(self):
        sym = SymbolDef(name="R", units=[
            SymbolUnit(pins=[Pin(number="1"), Pin(number="2")])
        ])
        report = AnalysisReport()
        _SymbolChecks.check_duplicate_pin_numbers(sym, report)
        assert len(report.issues) == 0

    def test_dup(self):
        sym = SymbolDef(name="R", units=[
            SymbolUnit(pins=[Pin(number="1"), Pin(number="1")])
        ])
        report = AnalysisReport()
        _SymbolChecks.check_duplicate_pin_numbers(sym, report)
        assert len(report.errors) == 1


class TestDeprecatedPropertyIds:
    """Check detection of (id N) on property nodes."""

    def test_detects_id(self):
        text = _minimal_symbol_lib_text(symbols="""
            (symbol "F1"
              (property "Reference" "F" (id 0) (at 0 0 0)
                (effects (font (size 1.27 1.27)))
              )
              (property "Value" "F1" (id 1) (at 0 0 0)
                (effects (font (size 1.27 1.27)))
              )
              (symbol "F1_1_1"
                (pin passive line (at 0 0 0) (length 2.54)
                  (name "1" (effects (font (size 1.27 1.27))))
                  (number "1" (effects (font (size 1.27 1.27))))
                )
              )
            )
        """)
        tree = parse(text)
        lib = SymbolLibrary._from_tree(tree)
        report = AnalysisReport()
        for sym in lib.symbols:
            _SymbolChecks.check_deprecated_property_ids(sym, report)
        assert len(report.warnings) == 2


class TestDeprecatedBareHide:
    """Check detection of bare hide atom in effects."""

    def test_detects_bare_hide(self):
        text = _minimal_symbol_lib_text(symbols="""
            (symbol "F1"
              (property "Footprint" "" (at 0 0 0)
                (effects (font (size 1.27 1.27) italic) hide)
              )
              (symbol "F1_1_1"
                (pin passive line (at 0 0 0) (length 2.54)
                  (name "1" (effects (font (size 1.27 1.27))))
                  (number "1" (effects (font (size 1.27 1.27))))
                )
              )
            )
        """)
        tree = parse(text)
        lib = SymbolLibrary._from_tree(tree)
        report = AnalysisReport()
        for sym in lib.symbols:
            _SymbolChecks.check_deprecated_bare_hide(sym, report)
        assert len(report.warnings) == 1


class TestExcludeFromSimCheck:
    def test_present(self):
        text = _minimal_symbol_lib_text(symbols=_make_symbol_text("R"))
        tree = parse(text)
        lib = SymbolLibrary._from_tree(tree)
        report = AnalysisReport()
        for sym in lib.symbols:
            _SymbolChecks.check_exclude_from_sim(sym, report)
        assert len(report.issues) == 0

    def test_missing(self):
        # Build without exclude_from_sim
        text = _minimal_symbol_lib_text(symbols="""
            (symbol "X"
              (property "Reference" "X" (at 0 0 0)
                (effects (font (size 1.27 1.27)))
              )
              (symbol "X_1_1"
                (pin passive line (at 0 0 0) (length 2.54)
                  (name "1" (effects (font (size 1.27 1.27))))
                  (number "1" (effects (font (size 1.27 1.27))))
                )
              )
            )
        """)
        tree = parse(text)
        lib = SymbolLibrary._from_tree(tree)
        report = AnalysisReport()
        for sym in lib.symbols:
            _SymbolChecks.check_exclude_from_sim(sym, report)
        assert len(report.infos) == 1


class TestPinZeroLength:
    def test_zero_length(self):
        sym = SymbolDef(name="R", units=[
            SymbolUnit(pins=[Pin(number="1", length=0.0)])
        ])
        report = AnalysisReport()
        _SymbolChecks.check_pin_zero_length(sym, report)
        assert len(report.infos) == 1

    def test_nonzero(self):
        sym = SymbolDef(name="R", units=[
            SymbolUnit(pins=[Pin(number="1", length=2.54)])
        ])
        report = AnalysisReport()
        _SymbolChecks.check_pin_zero_length(sym, report)
        assert len(report.issues) == 0


class TestSymbolHasUnits:
    def test_empty_units(self):
        sym = SymbolDef(name="R")
        report = AnalysisReport()
        _SymbolChecks.check_symbol_has_units(sym, report)
        assert len(report.warnings) == 1

    def test_has_units(self):
        sym = SymbolDef(name="R", units=[SymbolUnit()])
        report = AnalysisReport()
        _SymbolChecks.check_symbol_has_units(sym, report)
        assert len(report.issues) == 0


# ═══════════════════════════════════════════════════════════════════════════
# Footprint checks
# ═══════════════════════════════════════════════════════════════════════════


class TestFootprintNameCheck:
    def test_empty_name(self):
        fp = Footprint(name="")
        report = AnalysisReport()
        _FootprintChecks.check_name(fp, report)
        assert len(report.errors) == 1

    def test_forbidden_chars(self):
        fp = Footprint(name='Bad*"Name')
        report = AnalysisReport()
        _FootprintChecks.check_name(fp, report)
        assert len(report.errors) == 1

    def test_valid(self):
        fp = Footprint(name="R_0402")
        report = AnalysisReport()
        _FootprintChecks.check_name(fp, report)
        assert len(report.issues) == 0


class TestFootprintLayerCheck:
    def test_unusual_layer(self):
        fp = Footprint(name="X", layer="User.1")
        report = AnalysisReport()
        _FootprintChecks.check_layer(fp, report)
        assert len(report.warnings) == 1

    def test_valid_layers(self):
        for ly in ("F.Cu", "B.Cu"):
            fp = Footprint(name="X", layer=ly)
            r = AnalysisReport()
            _FootprintChecks.check_layer(fp, r)
            assert len(r.issues) == 0


class TestFootprintPadTypes:
    def test_invalid_type(self):
        fp = Footprint(name="X", pads=[Pad(number="1", type="weird")])
        report = AnalysisReport()
        _FootprintChecks.check_pad_types(fp, report)
        assert any(i.category == IssueCategory.PAD for i in report.errors)

    def test_invalid_shape(self):
        fp = Footprint(name="X", pads=[Pad(number="1", shape="hexagon")])
        report = AnalysisReport()
        _FootprintChecks.check_pad_types(fp, report)
        assert any("shape" in i.message for i in report.errors)


class TestFootprintPadSize:
    def test_zero_size(self):
        fp = Footprint(name="X", pads=[Pad(number="1", size=(0, 1))])
        report = AnalysisReport()
        _FootprintChecks.check_pad_size(fp, report)
        assert len(report.errors) == 1


class TestFootprintPadLayers:
    def test_no_layers(self):
        fp = Footprint(name="X", pads=[Pad(number="1", layers=[])])
        report = AnalysisReport()
        _FootprintChecks.check_pad_layers(fp, report)
        assert any(i.severity == Severity.ERROR for i in report.issues)

    def test_unknown_layer(self):
        fp = Footprint(name="X", pads=[Pad(number="1", layers=["Nonsense.Layer"])])
        report = AnalysisReport()
        _FootprintChecks.check_pad_layers(fp, report)
        assert any("unknown layer" in i.message for i in report.warnings)


class TestFootprintThruHoleDrill:
    def test_missing_drill(self):
        fp = Footprint(name="X", pads=[Pad(number="1", type="thru_hole", drill=0)])
        report = AnalysisReport()
        _FootprintChecks.check_thru_hole_drill(fp, report)
        assert len(report.errors) == 1

    def test_has_drill(self):
        fp = Footprint(name="X", pads=[Pad(number="1", type="thru_hole", drill=0.8)])
        report = AnalysisReport()
        _FootprintChecks.check_thru_hole_drill(fp, report)
        assert len(report.issues) == 0


class TestFootprintSmdNoDrill:
    def test_smd_with_drill(self):
        fp = Footprint(name="X", pads=[Pad(number="1", type="smd", drill=0.5)])
        report = AnalysisReport()
        _FootprintChecks.check_smd_no_drill(fp, report)
        assert len(report.warnings) == 1

    def test_smd_no_drill(self):
        fp = Footprint(name="X", pads=[Pad(number="1", type="smd", drill=0)])
        report = AnalysisReport()
        _FootprintChecks.check_smd_no_drill(fp, report)
        assert len(report.issues) == 0


class TestFootprintDuplicatePads:
    def test_no_dups(self):
        fp = Footprint(name="X", pads=[Pad(number="1"), Pad(number="2")])
        report = AnalysisReport()
        _FootprintChecks.check_duplicate_pads(fp, report)
        assert len(report.issues) == 0

    def test_dups(self):
        fp = Footprint(name="X", pads=[Pad(number="1"), Pad(number="1")])
        report = AnalysisReport()
        _FootprintChecks.check_duplicate_pads(fp, report)
        assert len(report.warnings) == 1


class TestFootprintNoPads:
    def test_no_pads(self):
        fp = Footprint(name="X", pads=[])
        report = AnalysisReport()
        _FootprintChecks.check_no_pads(fp, report)
        assert len(report.warnings) == 1


class TestFootprintOverlap:
    def test_overlapping(self):
        fp = Footprint(name="X", pads=[
            Pad(number="1", position=Position(1.0, 2.0)),
            Pad(number="2", position=Position(1.0, 2.0)),
        ])
        report = AnalysisReport()
        _FootprintChecks.check_overlapping_pads(fp, report)
        assert len(report.warnings) == 1


class TestFootprintAttributes:
    def test_smd_missing(self):
        fp = Footprint(name="X", attributes=[], pads=[Pad(number="1", type="smd")])
        report = AnalysisReport()
        _FootprintChecks.check_attributes(fp, report)
        assert any("smd" in i.message.lower() for i in report.warnings)


class TestFootprintCourtyard:
    def test_missing_courtyard(self):
        # Footprint with pads but no courtyard lines
        fp = Footprint(name="X", layer="F.Cu", pads=[Pad(number="1")])
        report = AnalysisReport()
        _FootprintChecks.check_courtyard(fp, report)
        assert len(report.warnings) == 1

    def test_has_courtyard(self):
        tree = parse('(fp_line (start -1 -1) (end 1 -1) (layer "F.CrtYd") (stroke (width 0.05) (type solid)))')
        g = FootprintGraphic.from_tree(tree)
        fp = Footprint(name="X", layer="F.Cu", pads=[Pad(number="1")], graphics=[g])
        report = AnalysisReport()
        _FootprintChecks.check_courtyard(fp, report)
        assert len(report.warnings) == 0


class TestFootprintReferenceText:
    def test_missing_ref(self):
        fp = Footprint(name="X")
        report = AnalysisReport()
        _FootprintChecks.check_reference_text(fp, report)
        assert any("reference" in i.message.lower() for i in report.warnings)

    def test_has_ref_via_property(self):
        fp = Footprint(name="X", properties=[Property("Reference", "R**"), Property("Value", "X")])
        report = AnalysisReport()
        _FootprintChecks.check_reference_text(fp, report)
        assert len(report.warnings) == 0


# ═══════════════════════════════════════════════════════════════════════════
# Symbol fixers
# ═══════════════════════════════════════════════════════════════════════════


class TestSymbolFixer:
    def test_fix_version(self):
        lib = SymbolLibrary(version=20211014)
        _SymbolFixer.fix_version(lib)
        assert lib.version == 20231120

    def test_fix_generator(self):
        lib = SymbolLibrary(generator="")
        _SymbolFixer.fix_generator(lib)
        assert lib.generator == "kicad_symbol_editor"

    def test_add_missing_props(self):
        sym = SymbolDef(name="R", properties=[Property("Reference", "R")])
        count = _SymbolFixer.add_missing_properties(sym)
        assert count == 3  # Value, Footprint, Datasheet
        keys = {p.key for p in sym.properties}
        assert keys == {"Reference", "Value", "Footprint", "Datasheet"}

    def test_sanitize_name(self):
        sym = SymbolDef(name='Bad:Name*"Here')
        assert _SymbolFixer.sanitize_name(sym)
        assert sym.name == "BadNameHere"

    def test_deduplicate_props(self):
        sym = SymbolDef(name="R", properties=[
            Property("Value", "a"), Property("Value", "b"), Property("Ref", "c")
        ])
        removed = _SymbolFixer.deduplicate_properties(sym)
        assert removed == 1
        assert len(sym.properties) == 2

    def test_fix_pin_type(self):
        pin = Pin(electrical_type="bogus")
        _SymbolFixer.fix_pin_type(pin)
        assert pin.electrical_type == "unspecified"

    def test_fix_pin_style(self):
        pin = Pin(graphic_style="fancy")
        _SymbolFixer.fix_pin_style(pin)
        assert pin.graphic_style == "line"

    def test_fix_deprecated_ids(self):
        text = _minimal_symbol_lib_text(symbols="""
            (symbol "F1"
              (property "Reference" "F" (id 0) (at 0 0 0)
                (effects (font (size 1.27 1.27)))
              )
              (symbol "F1_1_1"
                (pin passive line (at 0 0 0) (length 2.54)
                  (name "1" (effects (font (size 1.27 1.27))))
                  (number "1" (effects (font (size 1.27 1.27))))
                )
              )
            )
        """)
        tree = parse(text)
        lib = SymbolLibrary._from_tree(tree)
        count = _SymbolFixer.fix_deprecated_property_ids(lib.symbols[0])
        assert count == 1

    def test_fix_bare_hide(self):
        text = _minimal_symbol_lib_text(symbols="""
            (symbol "F1"
              (property "FP" "" (at 0 0 0)
                (effects (font (size 1.27 1.27) italic) hide)
              )
              (symbol "F1_1_1"
                (pin passive line (at 0 0 0) (length 2.54)
                  (name "1" (effects (font (size 1.27 1.27))))
                  (number "1" (effects (font (size 1.27 1.27))))
                )
              )
            )
        """)
        tree = parse(text)
        lib = SymbolLibrary._from_tree(tree)
        count = _SymbolFixer.fix_bare_hide(lib.symbols[0])
        assert count == 1
        # Verify it was replaced with ["hide", "yes"]
        sym = lib.symbols[0]
        prop = [c for c in sym.raw_tree if isinstance(c, list) and c and c[0] == "property"][0]
        effects = _find(prop, "effects")
        hide_node = _find(effects, "hide")
        assert hide_node is not None
        assert str(hide_node[1]) == "yes"

    def test_add_exclude_from_sim(self):
        text = _minimal_symbol_lib_text(symbols="""
            (symbol "X"
              (pin_names (offset 0))
              (property "Reference" "X" (at 0 0 0)
                (effects (font (size 1.27 1.27)))
              )
              (symbol "X_1_1"
                (pin passive line (at 0 0 0) (length 2.54)
                  (name "1" (effects (font (size 1.27 1.27))))
                  (number "1" (effects (font (size 1.27 1.27))))
                )
              )
            )
        """)
        tree = parse(text)
        lib = SymbolLibrary._from_tree(tree)
        result = _SymbolFixer.add_exclude_from_sim(lib.symbols[0])
        assert result is True
        assert _find(lib.symbols[0].raw_tree, "exclude_from_sim") is not None

    def test_set_default_reference(self):
        sym = SymbolDef(name="X", properties=[Property("Reference", "")])
        result = _SymbolFixer.set_default_reference(sym)
        assert result
        assert sym.properties[0].value == "U"


# ═══════════════════════════════════════════════════════════════════════════
# Footprint fixers
# ═══════════════════════════════════════════════════════════════════════════


class TestFootprintFixer:
    def test_sanitize_name(self):
        fp = Footprint(name='Bad*"FP')
        assert _FootprintFixer.sanitize_name(fp)
        assert fp.name == "BadFP"

    def test_remove_smd_drill(self):
        fp = Footprint(name="X", pads=[
            Pad(number="1", type="smd", drill=0.5),
            Pad(number="2", type="smd", drill=0),
        ])
        count = _FootprintFixer.remove_smd_drill(fp)
        assert count == 1
        assert fp.pads[0].drill == 0.0

    def test_fix_pad_layers(self):
        fp = Footprint(name="X", pads=[Pad(number="1", type="smd", layers=[])])
        count = _FootprintFixer.fix_pad_layers(fp)
        assert count == 1
        assert "F.Cu" in fp.pads[0].layers

    def test_set_smd_attr(self):
        fp = Footprint(name="X", attributes=[])
        assert _FootprintFixer.set_smd_attribute(fp)
        assert "smd" in fp.attributes

    def test_set_thru_attr(self):
        fp = Footprint(name="X", attributes=[])
        assert _FootprintFixer.set_thru_attribute(fp)
        assert "through_hole" in fp.attributes


# ═══════════════════════════════════════════════════════════════════════════
# Integration: Analyze fixtures
# ═══════════════════════════════════════════════════════════════════════════


class TestAnalyzeFixtures:
    """Test the full analyzer against the fixture files."""

    def test_analyze_symbol_fixture(self):
        path = FIXTURES_DIR / "test_symbol_lib.kicad_sym"
        if not path.exists():
            pytest.skip("Fixture not found")
        analyzer = LibraryAnalyzer()
        report = analyzer.analyze_symbol_library(path)
        assert report.file_type == "symbol_library"
        # Should parse without structure errors
        assert not any(
            i.category == IssueCategory.STRUCTURE for i in report.issues
        )

    def test_analyze_footprint_fixture(self):
        path = FIXTURES_DIR / "test_footprint.kicad_mod"
        if not path.exists():
            pytest.skip("Fixture not found")
        analyzer = LibraryAnalyzer()
        report = analyzer.analyze_footprint(path)
        assert report.file_type == "footprint"
        assert not any(
            i.category == IssueCategory.STRUCTURE for i in report.issues
        )

    def test_analyze_old_format_sym(self):
        """Analyze the root-level .kicad_sym which uses older format (v20211014)."""
        path = Path(__file__).parent.parent.parent.parent / "2026-04-08_22-48-19.kicad_sym"
        if not path.exists():
            pytest.skip("Old-format fixture not found")
        analyzer = LibraryAnalyzer()
        report = analyzer.analyze_symbol_library(path)
        # Should detect version warning
        version_issues = report.by_category(IssueCategory.VERSION)
        assert len(version_issues) >= 1
        # Should detect deprecated (id) properties
        deprecated_issues = report.by_category(IssueCategory.DEPRECATED)
        assert len(deprecated_issues) >= 1

    def test_analyze_file_autodetect(self):
        path = FIXTURES_DIR / "test_symbol_lib.kicad_sym"
        if not path.exists():
            pytest.skip("Fixture not found")
        analyzer = LibraryAnalyzer()
        report = analyzer.analyze_file(path)
        assert report.file_type == "symbol_library"


class TestAnalyzeText:
    """Test analysis from in-memory strings."""

    def test_symbol_lib_text(self):
        text = _minimal_symbol_lib_text(symbols=_make_symbol_text("R"))
        analyzer = LibraryAnalyzer()
        report = analyzer.analyze_symbol_library_text(text)
        assert report.file_type == "symbol_library"
        # Minimal valid lib should have no errors
        assert len(report.errors) == 0

    def test_footprint_text(self):
        text = _minimal_footprint_text(pads="""
            (pad "1" smd roundrect (at -1 0) (size 0.975 0.95) (layers "F.Cu" "F.Paste" "F.Mask") (roundrect_rratio 0.25))
            (pad "2" smd roundrect (at 1 0) (size 0.975 0.95) (layers "F.Cu" "F.Paste" "F.Mask") (roundrect_rratio 0.25))
        """)
        analyzer = LibraryAnalyzer()
        report = analyzer.analyze_footprint_text(text)
        assert report.file_type == "footprint"
        assert len(report.errors) == 0

    def test_parse_error(self):
        analyzer = LibraryAnalyzer()
        report = analyzer.analyze_symbol_library_text("THIS IS NOT S-EXPR")
        assert len(report.errors) >= 1
        assert report.errors[0].category == IssueCategory.STRUCTURE

    def test_footprint_parse_error(self):
        analyzer = LibraryAnalyzer()
        report = analyzer.analyze_footprint_text("GARBAGE DATA")
        assert len(report.errors) >= 1


# ═══════════════════════════════════════════════════════════════════════════
# Integration: Fix and round-trip
# ═══════════════════════════════════════════════════════════════════════════


class TestFixSymbolLibrary:
    def test_fix_old_format(self, tmp_path):
        """Fix the old-format library and verify it round-trips."""
        src = Path(__file__).parent.parent.parent.parent / "2026-04-08_22-48-19.kicad_sym"
        if not src.exists():
            pytest.skip("Old-format fixture not found")
        dst = tmp_path / "fixed.kicad_sym"
        analyzer = LibraryAnalyzer()
        fixed = analyzer.fix_symbol_library(src, dst)
        assert fixed > 0
        assert dst.exists()
        # Re-analyze the fixed file — should have fewer issues
        report_after = analyzer.analyze_symbol_library(dst)
        # Version should now be current
        assert not any(
            i.category == IssueCategory.VERSION and i.severity == Severity.ERROR
            for i in report_after.issues
        )

    def test_fix_in_memory(self):
        text = _minimal_symbol_lib_text(version=20211014, symbols="""
            (symbol "X"
              (property "Reference" "" (at 0 0 0)
                (effects (font (size 1.27 1.27)))
              )
              (symbol "X_1_1"
                (pin bogus_type fancy_style (at 0 0 0) (length 2.54)
                  (name "1" (effects (font (size 1.27 1.27))))
                  (number "1" (effects (font (size 1.27 1.27))))
                )
              )
            )
        """)
        tree = parse(text)
        lib = SymbolLibrary._from_tree(tree)
        analyzer = LibraryAnalyzer()
        fixed = analyzer.fix_symbol_library_object(lib)
        # Should fix version + generator + missing props + empty ref + pin type + pin style + metadata
        assert fixed >= 5


class TestFixFootprint:
    def test_fix_fixture(self, tmp_path):
        src = FIXTURES_DIR / "test_footprint.kicad_mod"
        if not src.exists():
            pytest.skip("Fixture not found")
        dst = tmp_path / "fixed.kicad_mod"
        analyzer = LibraryAnalyzer()
        fixed = analyzer.fix_footprint(src, dst)
        assert dst.exists()
        # Re-analyze
        report_after = analyzer.analyze_footprint(dst)
        assert not any(
            i.category == IssueCategory.STRUCTURE for i in report_after.issues
        )

    def test_fix_in_memory(self):
        fp = Footprint(
            name='Bad*"FP',
            pads=[
                Pad(number="1", type="smd", drill=0.5, layers=[]),
            ],
        )
        analyzer = LibraryAnalyzer()
        fixed = analyzer.fix_footprint_object(fp)
        assert fixed >= 3  # sanitize name + remove drill + fix layers
        assert fp.name == "BadFP"
        assert fp.pads[0].drill == 0.0
        assert "F.Cu" in fp.pads[0].layers


class TestFixFile:
    def test_unsupported_type(self, tmp_path):
        bad = tmp_path / "test.txt"
        bad.write_text("hello")
        analyzer = LibraryAnalyzer()
        with pytest.raises(ValueError, match="Unsupported"):
            analyzer.fix_file(bad)

    def test_analyze_unsupported(self, tmp_path):
        bad = tmp_path / "test.json"
        bad.write_text("{}")
        analyzer = LibraryAnalyzer()
        report = analyzer.analyze_file(bad)
        assert len(report.errors) == 1


# ═══════════════════════════════════════════════════════════════════════════
# Footprint directory analysis
# ═══════════════════════════════════════════════════════════════════════════


class TestFootprintDirectory:
    def test_not_a_directory(self, tmp_path):
        analyzer = LibraryAnalyzer()
        reports = analyzer.analyze_footprint_directory(tmp_path / "nonexistent")
        assert len(reports) == 1
        assert reports[0].errors

    def test_empty_directory(self, tmp_path):
        analyzer = LibraryAnalyzer()
        reports = analyzer.analyze_footprint_directory(tmp_path)
        assert reports == []

    def test_directory_with_footprint(self, tmp_path):
        fp_text = _minimal_footprint_text(pads="""
            (pad "1" smd rect (at 0 0) (size 1 1) (layers "F.Cu" "F.Paste" "F.Mask"))
        """)
        (tmp_path / "test.kicad_mod").write_text(fp_text, encoding="utf-8")
        analyzer = LibraryAnalyzer()
        reports = analyzer.analyze_footprint_directory(tmp_path)
        assert len(reports) == 1
        assert reports[0].file_type == "footprint"

    def test_fix_directory(self, tmp_path):
        fp_text = _minimal_footprint_text(name='Bad*FP', pads="""
            (pad "1" smd rect (at 0 0) (size 1 1) (layers "F.Cu" "F.Paste" "F.Mask"))
        """)
        out_dir = tmp_path / "output"
        (tmp_path / "test.kicad_mod").write_text(fp_text, encoding="utf-8")
        analyzer = LibraryAnalyzer()
        results = analyzer.fix_footprint_directory(tmp_path, out_dir)
        assert "test.kicad_mod" in results
        assert (out_dir / "test.kicad_mod").exists()


# ═══════════════════════════════════════════════════════════════════════════
# File not found / invalid
# ═══════════════════════════════════════════════════════════════════════════


class TestFileErrors:
    def test_symbol_not_found(self):
        analyzer = LibraryAnalyzer()
        report = analyzer.analyze_symbol_library("/nonexistent/file.kicad_sym")
        assert len(report.errors) == 1
        assert "not found" in report.errors[0].message.lower()

    def test_footprint_not_found(self):
        analyzer = LibraryAnalyzer()
        report = analyzer.analyze_footprint("/nonexistent/file.kicad_mod")
        assert len(report.errors) == 1


# ═══════════════════════════════════════════════════════════════════════════
# CLI tests
# ═══════════════════════════════════════════════════════════════════════════


class TestCLI:
    def test_analyze_symbol(self, capsys):
        path = FIXTURES_DIR / "test_symbol_lib.kicad_sym"
        if not path.exists():
            pytest.skip("Fixture not found")
        ret = main([str(path)])
        captured = capsys.readouterr()
        assert "Analysis report" in captured.out

    def test_analyze_json(self, capsys):
        path = FIXTURES_DIR / "test_symbol_lib.kicad_sym"
        if not path.exists():
            pytest.skip("Fixture not found")
        ret = main([str(path), "--json"])
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert isinstance(data, list)
        assert "total" in data[0]

    def test_analyze_quiet(self, capsys):
        path = FIXTURES_DIR / "test_symbol_lib.kicad_sym"
        if not path.exists():
            pytest.skip("Fixture not found")
        ret = main([str(path), "--quiet"])
        captured = capsys.readouterr()
        # Quiet mode should not print summary line
        assert "Analysis report" not in captured.out

    def test_fix_cli(self, tmp_path, capsys):
        src = FIXTURES_DIR / "test_symbol_lib.kicad_sym"
        if not src.exists():
            pytest.skip("Fixture not found")
        out = str(tmp_path)
        ret = main([str(src), "--fix", "--output", out])
        captured = capsys.readouterr()
        assert "Total fixes" in captured.out

    def test_unsupported_file(self, tmp_path, capsys):
        bad = tmp_path / "test.txt"
        bad.write_text("hello")
        ret = main([str(bad)])
        assert ret == 1

    def test_footprint_fixture(self, capsys):
        path = FIXTURES_DIR / "test_footprint.kicad_mod"
        if not path.exists():
            pytest.skip("Fixture not found")
        ret = main([str(path)])
        captured = capsys.readouterr()
        assert "Analysis report" in captured.out

    def test_directory_analysis(self, tmp_path, capsys):
        fp_text = _minimal_footprint_text(pads="""
            (pad "1" smd rect (at 0 0) (size 1 1) (layers "F.Cu" "F.Paste" "F.Mask"))
        """)
        (tmp_path / "a.kicad_mod").write_text(fp_text, encoding="utf-8")
        ret = main([str(tmp_path)])
        captured = capsys.readouterr()
        assert "Analysis report" in captured.out
