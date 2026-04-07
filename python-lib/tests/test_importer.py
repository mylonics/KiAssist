"""Tests for the symbol/footprint importer package."""

from __future__ import annotations

import io
import os
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Dict

import pytest

# ---------------------------------------------------------------------------
# Module-level imports
# ---------------------------------------------------------------------------
from kiassist_utils.importer.models import FieldSet, ImportedComponent, ImportMethod, ImportResult
from kiassist_utils.importer.field_normalizer import normalize_fields, build_raw_field_dict
from kiassist_utils.importer.zip_importer import (
    import_zip,
    import_zip_bytes,
    _convert_legacy_sym,
    _extract_meta_fields,
)
from kiassist_utils.importer.library_writer import (
    write_symbol_to_library,
    write_footprint_to_library,
    commit_import,
    _safe_sym_name,
    _safe_fp_name,
)

# Fixtures
FIXTURE_DIR = Path(__file__).parent / "fixtures"
FIXTURE_SYM = FIXTURE_DIR / "test_symbol_lib.kicad_sym"
FIXTURE_FP = FIXTURE_DIR / "test_footprint.kicad_mod"


# ===========================================================================
# FieldSet & field normalizer
# ===========================================================================


class TestFieldNormalizer:
    """Tests for normalize_fields() and related helpers."""

    def test_mpn_direct(self):
        fs = normalize_fields({"MPN": "NE555D"})
        assert fs.mpn == "NE555D"

    def test_mpn_fallback_to_value(self):
        """MPN should fall back to Value when not explicitly set."""
        fs = normalize_fields({"Value": "NE555D"})
        assert fs.mpn == "NE555D"

    def test_mpn_explicit_wins_over_value(self):
        fs = normalize_fields({"MPN": "NE555D", "Value": "SomeValue"})
        assert fs.mpn == "NE555D"

    def test_manufacturer_aliases(self):
        for alias in ("Manufacturer", "MF", "mfg", "Mfr"):
            fs = normalize_fields({alias: "Texas Instruments"})
            assert fs.manufacturer == "Texas Instruments", f"Failed for alias {alias!r}"

    def test_digikey_aliases(self):
        for alias in ("DKPN", "Digikey", "dk_pn", "DigiKey Part Number"):
            fs = normalize_fields({alias: "296-8011-5-ND"})
            assert fs.digikey_pn == "296-8011-5-ND", f"Failed for alias {alias!r}"

    def test_mouser_aliases(self):
        for alias in ("MSPN", "Mouser", "mouser_pn"):
            fs = normalize_fields({alias: "595-NE555DR"})
            assert fs.mouser_pn == "595-NE555DR", f"Failed for alias {alias!r}"

    def test_lcsc_aliases(self):
        for alias in ("LCSC", "LCSC_PN", "LCSC Part Number"):
            fs = normalize_fields({alias: "C8082"})
            assert fs.lcsc_pn == "C8082", f"Failed for alias {alias!r}"

    def test_junk_fields_removed(self):
        fs = normalize_fields({"ki_keywords": "timer", "MPN": "NE555"})
        assert "ki_keywords" not in fs.extra

    def test_empty_values_skipped(self):
        fs = normalize_fields({"MPN": "", "Manufacturer": "  "})
        assert fs.mpn == ""
        assert fs.manufacturer == ""

    def test_extra_fields_preserved(self):
        fs = normalize_fields({"MPN": "NE555", "CustomField": "custom_val"})
        assert fs.extra.get("CustomField") == "custom_val"

    def test_to_kicad_properties_contains_mpn(self):
        fs = normalize_fields({"MPN": "NE555", "Manufacturer": "TI"})
        props = fs.to_kicad_properties()
        names = [p["name"] for p in props]
        assert "MPN" in names
        assert "MF" in names

    def test_build_raw_field_dict(self):
        pairs = [("Key1", "Val1"), ("Key1", "Val2")]
        d = build_raw_field_dict(pairs)
        assert d["Key1"] == "Val2"  # last value wins


# ===========================================================================
# ImportedComponent model
# ===========================================================================


class TestImportedComponent:
    def test_default_construction(self):
        comp = ImportedComponent(name="Test")
        assert comp.name == "Test"
        assert comp.fields.mpn == ""
        assert comp.model_paths == []

    def test_import_method_default(self):
        comp = ImportedComponent(name="X")
        assert comp.import_method == ImportMethod.ZIP


# ===========================================================================
# ZIP importer
# ===========================================================================


def _make_zip(contents: Dict[str, str], tmp_dir: Path) -> Path:
    """Create a zip file in *tmp_dir* with the given filename→text contents."""
    zp = tmp_dir / "test.zip"
    with zipfile.ZipFile(zp, "w") as zf:
        for filename, text in contents.items():
            zf.writestr(filename, text)
    return zp


MINIMAL_SYM = """\
(kicad_symbol_lib (version 20231120) (generator test)
  (symbol "R"
    (in_bom yes) (on_board yes)
    (property "Reference" "R" (at 0 0 0) (effects (font (size 1.27 1.27))))
    (property "Value" "R" (at 0 -2 0) (effects (font (size 1.27 1.27))))
    (property "MPN" "RC0402JR-0710KL" (at 0 0 0) (effects (font (size 1.27 1.27))))
  )
)
"""

MINIMAL_FP = """\
(footprint "R_0402" (layer "F.Cu")
  (fp_text reference "R?" (at 0 -1.5) (layer "F.SilkS")
    (effects (font (size 1 1))))
  (fp_text value "R_0402" (at 0 1.5) (layer "F.Fab")
    (effects (font (size 1 1))))
  (pad "1" smd rect (at -1.0 0) (size 0.9 0.9) (layers "F.Cu"))
  (pad "2" smd rect (at  1.0 0) (size 0.9 0.9) (layers "F.Cu"))
)
"""

MINIMAL_LEGACY_LIB = """\
EESchema-LIBRARY Version 2.4
DEF NE555 U 0 0 Y Y 1 F N
F0 "U" 0 100 50 H V C CNN
F1 "NE555" 0 -100 50 H V C CNN
F2 "" 0 0 50 H I C CNN
F3 "" 0 0 50 H I C CNN
ENDDEF
ENDLIB
"""


class TestZipImporter:
    def test_import_kicad_sym(self, tmp_path):
        zp = _make_zip({"Component.kicad_sym": MINIMAL_SYM}, tmp_path)
        result = import_zip(zp, output_dir=tmp_path / "out")
        assert result.success
        assert result.component is not None
        assert result.component.symbol_sexpr

    def test_import_kicad_mod(self, tmp_path):
        zp = _make_zip({"R_0402.kicad_mod": MINIMAL_FP}, tmp_path)
        result = import_zip(zp, output_dir=tmp_path / "out")
        assert result.success
        assert result.component.footprint_sexpr

    def test_import_sym_and_fp(self, tmp_path):
        zp = _make_zip(
            {"Component.kicad_sym": MINIMAL_SYM, "R_0402.kicad_mod": MINIMAL_FP},
            tmp_path,
        )
        result = import_zip(zp, output_dir=tmp_path / "out")
        assert result.success
        assert result.component.symbol_sexpr
        assert result.component.footprint_sexpr

    def test_3d_models_detected(self, tmp_path):
        zp = _make_zip(
            {
                "Component.kicad_sym": MINIMAL_SYM,
                "R_0402.kicad_mod": MINIMAL_FP,
                "R_0402.step": "SOLID",
                "R_0402.wrl": "#VRML",
            },
            tmp_path,
        )
        result = import_zip(zp, output_dir=tmp_path / "out")
        assert result.success
        assert len(result.component.model_paths) == 2

    def test_metadata_txt_parsed(self, tmp_path):
        meta = "Manufacturer: YAGEO\nMPN: RC0402JR-0710KL\nDatasheet: https://example.com/ds.pdf\n"
        zp = _make_zip(
            {"Component.kicad_sym": MINIMAL_SYM, "metadata.txt": meta},
            tmp_path,
        )
        result = import_zip(zp, output_dir=tmp_path / "out")
        assert result.success
        assert result.component.fields.manufacturer == "YAGEO"

    def test_missing_zip_returns_error(self, tmp_path):
        result = import_zip(tmp_path / "nonexistent.zip")
        assert not result.success
        assert "not found" in result.error.lower()

    def test_invalid_zip_returns_error(self, tmp_path):
        bad = tmp_path / "bad.zip"
        bad.write_text("not a zip")
        result = import_zip(bad)
        assert not result.success

    def test_empty_zip_returns_error(self, tmp_path):
        zp = _make_zip({"README.txt": "hello"}, tmp_path)
        result = import_zip(zp, output_dir=tmp_path / "out")
        assert not result.success
        assert "no .kicad_sym" in result.error.lower()

    def test_import_zip_bytes(self, tmp_path):
        zp = _make_zip({"Component.kicad_sym": MINIMAL_SYM}, tmp_path)
        data = zp.read_bytes()
        result = import_zip_bytes(data, output_dir=tmp_path / "out2")
        assert result.success

    def test_legacy_lib_converted(self, tmp_path):
        zp = _make_zip({"NE555.lib": MINIMAL_LEGACY_LIB}, tmp_path)
        result = import_zip(zp, output_dir=tmp_path / "out")
        assert result.success
        # Converted file should be a kicad_sym
        assert result.component.symbol_sexpr
        assert "kicad_symbol_lib" in result.component.symbol_sexpr


class TestLegacySymConverter:
    def test_converts_basic_lib(self):
        out = _convert_legacy_sym(MINIMAL_LEGACY_LIB)
        assert "kicad_symbol_lib" in out
        assert "NE555" in out

    def test_empty_lib(self):
        out = _convert_legacy_sym("EESchema-LIBRARY Version 2.4\nENDLIB\n")
        assert "kicad_symbol_lib" in out


class TestMetaFieldExtractor:
    def test_key_value_format(self):
        fields = _extract_meta_fields("Manufacturer: YAGEO\nMPN: RC0402\n")
        assert fields.get("Manufacturer") == "YAGEO"
        assert fields.get("MPN") == "RC0402"

    def test_csv_format(self):
        csv = '"Manufacturer","MPN","Datasheet"\n"YAGEO","RC0402","http://example.com"\n'
        fields = _extract_meta_fields(csv)
        assert fields.get("Manufacturer") == "YAGEO"
        assert fields.get("MPN") == "RC0402"

    def test_empty_input(self):
        assert _extract_meta_fields("") == {}


# ===========================================================================
# Library writer
# ===========================================================================


class TestLibraryWriter:
    def test_safe_sym_name_basic(self):
        assert _safe_sym_name("NE555") == "NE555"

    def test_safe_sym_name_spaces(self):
        assert _safe_sym_name("My Part 1") == "My_Part_1"

    def test_safe_fp_name(self):
        assert _safe_fp_name("R_0402") == "R_0402"

    def test_write_symbol_creates_library(self, tmp_path):
        sym_lib = tmp_path / "parts.kicad_sym"
        comp = ImportedComponent(
            name="NE555",
            symbol_sexpr=MINIMAL_SYM,
        )
        comp.fields = normalize_fields({"MPN": "NE555D", "Manufacturer": "TI"})
        ok, name = write_symbol_to_library(comp, sym_lib)
        assert ok
        assert sym_lib.exists()
        # Load back and verify
        from kiassist_utils.kicad_parser.symbol_lib import SymbolLibrary
        lib = SymbolLibrary.load(sym_lib)
        assert lib.find_by_name(name) is not None

    def test_write_symbol_adds_to_existing(self, tmp_path):
        sym_lib = tmp_path / "parts.kicad_sym"
        shutil.copy(FIXTURE_SYM, sym_lib)

        comp = ImportedComponent(name="NE555", symbol_sexpr=MINIMAL_SYM)
        comp.fields = normalize_fields({"MPN": "NE555D"})
        ok, name = write_symbol_to_library(comp, sym_lib)
        assert ok

        from kiassist_utils.kicad_parser.symbol_lib import SymbolLibrary
        lib = SymbolLibrary.load(sym_lib)
        # Original symbols still present
        assert lib.find_by_name("R") is not None
        assert lib.find_by_name("C") is not None
        # New symbol added
        assert lib.find_by_name(name) is not None

    def test_write_symbol_no_overwrite_renames(self, tmp_path):
        sym_lib = tmp_path / "parts.kicad_sym"
        comp1 = ImportedComponent(name="R", symbol_sexpr=MINIMAL_SYM)
        comp1.fields = normalize_fields({"MPN": "R"})
        write_symbol_to_library(comp1, sym_lib)

        comp2 = ImportedComponent(name="R", symbol_sexpr=MINIMAL_SYM)
        comp2.fields = normalize_fields({"MPN": "R"})
        ok, name = write_symbol_to_library(comp2, sym_lib, overwrite=False)
        assert ok
        assert name == "R_2"

    def test_write_footprint_creates_dir(self, tmp_path):
        fp_dir = tmp_path / "parts.pretty"
        comp = ImportedComponent(name="R_0402", footprint_sexpr=MINIMAL_FP)
        ok, fp_path, models = write_footprint_to_library(comp, fp_dir)
        assert ok
        assert Path(fp_path).exists()
        assert fp_dir.is_dir()

    def test_write_footprint_copies_3d_model(self, tmp_path):
        fp_dir = tmp_path / "parts.pretty"
        models_dir = tmp_path / "3d"
        # Create a fake .step file
        step_file = tmp_path / "R_0402.step"
        step_file.write_text("ISO-10303-21;")

        comp = ImportedComponent(name="R_0402", footprint_sexpr=MINIMAL_FP)
        comp.model_paths = [step_file]

        ok, fp_path, copied = write_footprint_to_library(comp, fp_dir, models_dir=models_dir)
        assert ok
        assert len(copied) == 1
        assert copied[0].exists()

    def test_write_footprint_injects_3d_model_ref(self, tmp_path):
        fp_dir = tmp_path / "parts.pretty"
        models_dir = tmp_path / "3d"
        step_file = tmp_path / "R_0402.step"
        step_file.write_text("ISO-10303-21;")

        comp = ImportedComponent(name="R_0402", footprint_sexpr=MINIMAL_FP)
        comp.model_paths = [step_file]

        ok, fp_path, _ = write_footprint_to_library(comp, fp_dir, models_dir=models_dir)
        assert ok
        content = Path(fp_path).read_text()
        assert "(model" in content

    def test_commit_import_end_to_end(self, tmp_path):
        sym_lib = tmp_path / "parts.kicad_sym"
        fp_dir = tmp_path / "parts.pretty"

        comp = ImportedComponent(
            name="NE555D",
            symbol_sexpr=MINIMAL_SYM,
            footprint_sexpr=MINIMAL_FP,
            import_method=ImportMethod.ZIP,
        )
        comp.fields = normalize_fields({
            "MPN": "NE555D",
            "Manufacturer": "Texas Instruments",
            "LCSC": "C8082",
        })

        result = commit_import(
            comp,
            target_sym_lib=sym_lib,
            target_fp_lib_dir=fp_dir,
        )
        assert result.success
        assert sym_lib.exists()
        assert fp_dir.is_dir()
        assert len(list(fp_dir.glob("*.kicad_mod"))) == 1

    def test_commit_import_preview_only(self, tmp_path):
        """commit_import with no target paths should still return success."""
        comp = ImportedComponent(
            name="NE555D",
            symbol_sexpr=MINIMAL_SYM,
        )
        comp.fields = normalize_fields({"MPN": "NE555D"})
        result = commit_import(comp, target_sym_lib=None, target_fp_lib_dir=None)
        assert result.success


# ===========================================================================
# LCSC importer (availability check only; full import requires network)
# ===========================================================================


class TestLcscImporter:
    def test_is_available_returns_bool(self):
        from kiassist_utils.importer.lcsc_importer import is_available
        assert isinstance(is_available(), bool)

    def test_import_without_package_gives_error(self):
        """When easyeda2kicad is not installed, import_lcsc returns an error result."""
        from kiassist_utils.importer.lcsc_importer import is_available, import_lcsc
        if is_available():
            pytest.skip("easyeda2kicad is installed; skipping unavailability test")
        result = import_lcsc("C14663")
        assert not result.success
        assert "easyeda2kicad" in result.error


# ===========================================================================
# Package-level exports
# ===========================================================================


def test_package_exports():
    import kiassist_utils.importer as imp
    assert callable(imp.normalize_fields)
    assert callable(imp.import_zip)
    assert callable(imp.commit_import)
    assert callable(imp.search_symbols)
    assert callable(imp.search_footprints)
    # AI helpers
    assert callable(imp.suggest_symbol)
    assert callable(imp.map_pins)
    assert callable(imp.generate_symbol)
    assert callable(imp.extract_pins_from_symbol)
    assert callable(imp.apply_pin_mapping)


# ===========================================================================
# ai_symbol helpers
# ===========================================================================

MINIMAL_SYM_PINS = """\
(kicad_symbol_lib (version 20231120) (generator test)
  (symbol "NE555D"
    (in_bom yes) (on_board yes)
    (property "Reference" "U" (at 0 0 0) (effects (font (size 1.27 1.27))))
    (property "Value" "NE555D" (at 0 -2 0) (effects (font (size 1.27 1.27))))
    (symbol "NE555D_1_1"
      (pin power_in line (at 0 0 0) (length 2.54)
        (name "GND" (effects (font (size 1.27 1.27))))
        (number "1" (effects (font (size 1.27 1.27))))
      )
      (pin output line (at 0 2.54 0) (length 2.54)
        (name "OUT" (effects (font (size 1.27 1.27))))
        (number "3" (effects (font (size 1.27 1.27))))
      )
      (pin power_in line (at 0 5.08 0) (length 2.54)
        (name "VCC" (effects (font (size 1.27 1.27))))
        (number "8" (effects (font (size 1.27 1.27))))
      )
    )
  )
)
"""


class TestAiSymbolHelpers:
    """Tests for the ai_symbol module (no real AI calls)."""

    def _dummy_caller(self, system: str, user: str) -> str:
        """Return a dummy AI response based on content keywords."""
        if '"mapping"' in system:
            return '{"mapping": {"1": "1", "3": "3", "8": "8"}, "notes": "direct match", "warnings": []}'
        if '"library"' in system:
            return '[{"library": "Timer", "name": "NE555", "reason": "Functionally identical 8-pin timer", "confidence": "high"}]'
        if "(symbol" in system:
            return '(symbol "NE555D" (in_bom yes) (on_board yes) (property "Reference" "U" (at 0 0 0) (effects (font (size 1.27 1.27)))) )'
        return "{}"

    def test_extract_pins_from_symbol(self):
        from kiassist_utils.importer.ai_symbol import extract_pins_from_symbol
        pins = extract_pins_from_symbol(MINIMAL_SYM_PINS)
        assert len(pins) == 3
        numbers = {p["number"] for p in pins}
        assert numbers == {"1", "3", "8"}
        names = {p["name"] for p in pins}
        assert "GND" in names
        assert "VCC" in names

    def test_extract_pins_empty(self):
        from kiassist_utils.importer.ai_symbol import extract_pins_from_symbol
        assert extract_pins_from_symbol("") == []
        assert extract_pins_from_symbol("(no pins here)") == []

    def test_suggest_symbol_returns_list(self):
        from kiassist_utils.importer.ai_symbol import suggest_symbol
        suggestions, raw = suggest_symbol(
            mpn="NE555D",
            manufacturer="TI",
            description="Single Timer",
            package="DIP-8",
            pin_summary="8 pins",
            available_libraries=["Timer", "Device"],
            call_ai=self._dummy_caller,
        )
        assert isinstance(suggestions, list)
        assert len(suggestions) == 1
        assert suggestions[0].library == "Timer"
        assert suggestions[0].name == "NE555"
        assert suggestions[0].confidence == "high"

    def test_suggest_symbol_bad_json(self):
        from kiassist_utils.importer.ai_symbol import suggest_symbol
        suggestions, _ = suggest_symbol(
            mpn="X", manufacturer="", description="", package="",
            pin_summary="", available_libraries=[],
            call_ai=lambda s, u: "not valid json",
        )
        assert suggestions == []

    def test_map_pins_returns_mapping(self):
        from kiassist_utils.importer.ai_symbol import map_pins, PinMapping
        imported = [{"number": "1", "name": "GND"}, {"number": "3", "name": "OUT"}, {"number": "8", "name": "VCC"}]
        base = [{"number": "1", "name": "GND"}, {"number": "3", "name": "OUT"}, {"number": "8", "name": "VCC"}]
        result, raw = map_pins(
            mpn="NE555D",
            imported_pins=imported,
            base_symbol_lib="Timer",
            base_symbol_name="NE555",
            base_pins=base,
            call_ai=self._dummy_caller,
        )
        assert isinstance(result, PinMapping)
        assert result.mapping["1"] == "1"
        assert result.mapping["8"] == "8"
        assert result.notes == "direct match"

    def test_map_pins_bad_json_returns_empty(self):
        from kiassist_utils.importer.ai_symbol import map_pins
        result, _ = map_pins(
            mpn="X", imported_pins=[], base_symbol_lib="L", base_symbol_name="S",
            base_pins=[], call_ai=lambda s, u: "garbage",
        )
        assert result.mapping == {}
        assert result.warnings  # should have a warning about parse failure

    def test_apply_pin_mapping_replaces_numbers(self):
        from kiassist_utils.importer.ai_symbol import apply_pin_mapping, PinMapping
        sexpr = '(pin passive line (number "1") (name "A"))(pin passive line (number "2") (name "B"))'
        pm = PinMapping(mapping={"1": "10", "2": "20"})
        result = apply_pin_mapping(sexpr, pm)
        assert '(number "10"' in result
        assert '(number "20"' in result
        assert '(number "1"' not in result

    def test_apply_pin_mapping_skips_null(self):
        from kiassist_utils.importer.ai_symbol import apply_pin_mapping, PinMapping
        sexpr = '(pin passive line (number "3") (name "C"))'
        pm = PinMapping(mapping={"3": None})
        result = apply_pin_mapping(sexpr, pm)
        # Original should be unchanged
        assert '(number "3"' in result

    def test_generate_symbol_extracts_sexpr(self):
        from kiassist_utils.importer.ai_symbol import generate_symbol
        sym, raw = generate_symbol(
            mpn="NE555D", manufacturer="TI", description="Timer",
            package="DIP-8", reference="U", datasheet="~",
            pins=[], call_ai=self._dummy_caller,
        )
        assert sym.startswith("(symbol")

    def test_generate_symbol_no_sexpr_returns_empty(self):
        from kiassist_utils.importer.ai_symbol import generate_symbol
        sym, _ = generate_symbol(
            mpn="X", manufacturer="", description="", package="",
            reference="U", datasheet="",
            pins=[], call_ai=lambda s, u: "No symbol here.",
        )
        assert sym == ""

    def test_strip_fences(self):
        from kiassist_utils.importer.ai_symbol import _strip_fences
        assert _strip_fences("```json\n[]\n```") == "[]"
        assert _strip_fences("plain text") == "plain text"

    def test_parse_suggestions_missing_fields(self):
        from kiassist_utils.importer.ai_symbol import _parse_suggestions
        raw = '[{"library": "Device", "name": "R"}]'  # missing reason/confidence
        sug = _parse_suggestions(raw)
        assert len(sug) == 1
        assert sug[0].reason == ""
        assert sug[0].confidence == "medium"
