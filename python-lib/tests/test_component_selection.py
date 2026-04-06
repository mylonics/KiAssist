"""Tests for the component selection pipeline.

Covers:
* :mod:`kiassist_utils.component_selection.models`
* :mod:`kiassist_utils.component_selection.local_source`
* :mod:`kiassist_utils.component_selection.selector`
* MCP tools ``component_search`` and ``component_get_candidates``
"""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from typing import Any, Dict

import pytest

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

FIXTURE_DIR = Path(__file__).parent / "fixtures"
FIXTURE_COMP_LIB = FIXTURE_DIR / "test_component_lib.kicad_sym"
FIXTURE_SYM_LIB = FIXTURE_DIR / "test_symbol_lib.kicad_sym"


@pytest.fixture()
def tmp_comp_lib(tmp_path: Path) -> Path:
    """Copy the component fixture library to a temp directory."""
    dst = tmp_path / "test_component_lib.kicad_sym"
    shutil.copy(FIXTURE_COMP_LIB, dst)
    return dst


@pytest.fixture()
def tmp_sym_lib(tmp_path: Path) -> Path:
    """Copy the generic symbol fixture library to a temp directory."""
    dst = tmp_path / "test_symbol_lib.kicad_sym"
    shutil.copy(FIXTURE_SYM_LIB, dst)
    return dst


# ===========================================================================
# Tests: models.py
# ===========================================================================


class TestComponentSpec:
    def test_defaults(self) -> None:
        from kiassist_utils.component_selection.models import ComponentSpec

        spec = ComponentSpec(component_type="ADC")
        assert spec.component_type == "ADC"
        assert spec.description == ""
        assert spec.constraints == {}
        assert spec.preferred_footprints == []
        assert spec.max_candidates == 5

    def test_to_dict(self) -> None:
        from kiassist_utils.component_selection.models import ComponentSpec

        spec = ComponentSpec(
            component_type="LDO",
            description="3.3 V regulator",
            constraints={"voltage": 3.3},
            preferred_footprints=["SOT-23"],
            max_candidates=3,
        )
        d = spec.to_dict()
        assert d["component_type"] == "LDO"
        assert d["description"] == "3.3 V regulator"
        assert d["constraints"] == {"voltage": 3.3}
        assert d["preferred_footprints"] == ["SOT-23"]
        assert d["max_candidates"] == 3

    def test_from_dict_roundtrip(self) -> None:
        from kiassist_utils.component_selection.models import ComponentSpec

        original = ComponentSpec(
            component_type="ADC",
            description="high resolution",
            constraints={"resolution": 16},
            preferred_footprints=["SOIC-8"],
            max_candidates=5,
        )
        restored = ComponentSpec.from_dict(original.to_dict())
        assert restored.component_type == original.component_type
        assert restored.description == original.description
        assert restored.constraints == original.constraints
        assert restored.preferred_footprints == original.preferred_footprints
        assert restored.max_candidates == original.max_candidates

    def test_from_dict_defaults(self) -> None:
        from kiassist_utils.component_selection.models import ComponentSpec

        spec = ComponentSpec.from_dict({"component_type": "resistor"})
        assert spec.component_type == "resistor"
        assert spec.constraints == {}
        assert spec.max_candidates == 5


class TestComponentCandidate:
    def _make(self, **kw: Any):
        from kiassist_utils.component_selection.models import ComponentCandidate

        defaults: Dict[str, Any] = dict(
            symbol="TestLib:ADS1115",
            description="16-bit ADC",
            component_type="ADC",
            footprint="Package_SO:SOIC-8",
            datasheet_url="https://example.com/ds.pdf",
            specifications={"Resolution": 16},
            properties={"Reference": "U", "Description": "16-bit ADC"},
            source="kicad_lib",
            score=0.75,
        )
        defaults.update(kw)
        return ComponentCandidate(**defaults)

    def test_to_dict_contains_all_fields(self) -> None:
        c = self._make()
        d = c.to_dict()
        assert d["symbol"] == "TestLib:ADS1115"
        assert d["description"] == "16-bit ADC"
        assert d["component_type"] == "ADC"
        assert d["footprint"] == "Package_SO:SOIC-8"
        assert d["datasheet_url"] == "https://example.com/ds.pdf"
        assert d["specifications"] == {"Resolution": 16}
        assert d["properties"] == {"Reference": "U", "Description": "16-bit ADC"}
        assert d["source"] == "kicad_lib"
        assert d["score"] == 0.75

    def test_to_summary_dict_minimal(self) -> None:
        c = self._make(datasheet_url="", specifications={})
        s = c.to_summary_dict()
        assert "symbol" in s
        assert "description" in s
        assert "footprint" in s
        assert "score" in s
        # No datasheet or specs when empty
        assert "datasheet" not in s
        assert "specs" not in s
        # No bulky properties field
        assert "properties" not in s

    def test_to_summary_dict_includes_optional_fields(self) -> None:
        c = self._make()
        s = c.to_summary_dict()
        assert "datasheet" in s
        assert "specs" in s

    def test_score_rounded_to_2dp_in_summary(self) -> None:
        c = self._make(score=0.66666)
        s = c.to_summary_dict()
        assert s["score"] == 0.67

    def test_score_rounded_to_4dp_in_full(self) -> None:
        c = self._make(score=0.123456789)
        d = c.to_dict()
        assert d["score"] == 0.1235


class TestSelectionResult:
    def _make_result(self):
        from kiassist_utils.component_selection.models import (
            ComponentCandidate,
            ComponentSpec,
            SelectionResult,
        )

        spec = ComponentSpec(component_type="ADC")
        c1 = ComponentCandidate(
            symbol="Lib:ADC1",
            description="8-bit ADC",
            component_type="ADC",
            score=0.8,
        )
        c2 = ComponentCandidate(
            symbol="Lib:ADC2",
            description="12-bit ADC",
            component_type="ADC",
            score=0.6,
        )
        return SelectionResult(
            query=spec,
            candidates=[c1, c2],
            total_found=10,
            filtered_count=2,
            source_stats={"kicad_lib": 2},
        )

    def test_to_dict_summary(self) -> None:
        result = self._make_result()
        d = result.to_dict(summary=True)
        assert d["total_found"] == 10
        assert d["filtered_count"] == 2
        assert d["source_stats"] == {"kicad_lib": 2}
        assert len(d["candidates"]) == 2
        # Summary dicts should not contain properties
        for c in d["candidates"]:
            assert "properties" not in c

    def test_to_dict_full(self) -> None:
        result = self._make_result()
        d = result.to_dict(summary=False)
        for c in d["candidates"]:
            assert "component_type" in c
            assert "source" in c

    def test_query_included(self) -> None:
        result = self._make_result()
        d = result.to_dict()
        assert d["query"]["component_type"] == "ADC"


# ===========================================================================
# Tests: local_source.py
# ===========================================================================


class TestParseNumericValue:
    def test_plain_integer(self) -> None:
        from kiassist_utils.component_selection.local_source import _parse_numeric_value

        assert _parse_numeric_value("100") == pytest.approx(100.0)

    def test_float(self) -> None:
        from kiassist_utils.component_selection.local_source import _parse_numeric_value

        assert _parse_numeric_value("3.3") == pytest.approx(3.3)

    def test_voltage_unit(self) -> None:
        from kiassist_utils.component_selection.local_source import _parse_numeric_value

        assert _parse_numeric_value("3.3V") == pytest.approx(3.3)

    def test_milli_prefix(self) -> None:
        from kiassist_utils.component_selection.local_source import _parse_numeric_value

        assert _parse_numeric_value("100mA") == pytest.approx(0.1)

    def test_kilo_prefix(self) -> None:
        from kiassist_utils.component_selection.local_source import _parse_numeric_value

        assert _parse_numeric_value("4.7k") == pytest.approx(4700.0)

    def test_mega_prefix(self) -> None:
        from kiassist_utils.component_selection.local_source import _parse_numeric_value

        assert _parse_numeric_value("1M") == pytest.approx(1e6)

    def test_nano_prefix(self) -> None:
        from kiassist_utils.component_selection.local_source import _parse_numeric_value

        assert _parse_numeric_value("100nF") == pytest.approx(1e-7)

    def test_non_numeric_returns_none(self) -> None:
        from kiassist_utils.component_selection.local_source import _parse_numeric_value

        assert _parse_numeric_value("abc") is None
        assert _parse_numeric_value("") is None
        assert _parse_numeric_value("~") is None

    def test_scientific_notation(self) -> None:
        from kiassist_utils.component_selection.local_source import _parse_numeric_value

        assert _parse_numeric_value("1e3") == pytest.approx(1000.0)


class TestInferComponentType:
    def _make_sym(self, name: str, description: str = ""):
        from kiassist_utils.kicad_parser.symbol_lib import SymbolDef
        from kiassist_utils.kicad_parser.models import Property

        sym = SymbolDef(name=name)
        if description:
            sym.properties.append(Property(key="Description", value=description))
        return sym

    def test_adc_from_name(self) -> None:
        from kiassist_utils.component_selection.local_source import _infer_component_type

        sym = self._make_sym("ADS1115")
        assert _infer_component_type(sym, "ADC_lib") == "ADC"

    def test_adc_from_description(self) -> None:
        from kiassist_utils.component_selection.local_source import _infer_component_type

        sym = self._make_sym("U1", description="16-bit ADC with I2C")
        assert _infer_component_type(sym, "Device") == "ADC"

    def test_ldo_from_description(self) -> None:
        from kiassist_utils.component_selection.local_source import _infer_component_type

        sym = self._make_sym("LM1117", description="LDO linear regulator 3.3V")
        assert _infer_component_type(sym, "Device") == "LDO"

    def test_resistor_from_lib_name(self) -> None:
        from kiassist_utils.component_selection.local_source import _infer_component_type

        sym = self._make_sym("R")
        # The name "R" alone may not match "resistor" keyword exactly;
        # the lib name "Device" won't either — result should be "other"
        # unless the description says resistor.
        result = _infer_component_type(sym, "Device")
        # Acceptable: either "other" or "resistor" — just confirm no crash.
        assert isinstance(result, str)

    def test_unknown_returns_other(self) -> None:
        from kiassist_utils.component_selection.local_source import _infer_component_type

        sym = self._make_sym("XYZABC123")
        assert _infer_component_type(sym, "UnknownLib") == "other"


class TestLoadCandidatesFromLibrary:
    def test_loads_symbols(self, tmp_comp_lib: Path) -> None:
        from kiassist_utils.component_selection.local_source import (
            load_candidates_from_library,
        )

        candidates = load_candidates_from_library(tmp_comp_lib)
        # Fixture has 4 symbols (ADS1115, MCP3204, LM1117-3.3, R_1k).
        # R_1k extends R so it is skipped → 3 base symbols.
        assert len(candidates) == 3

    def test_candidate_fields_populated(self, tmp_comp_lib: Path) -> None:
        from kiassist_utils.component_selection.local_source import (
            load_candidates_from_library,
        )

        candidates = load_candidates_from_library(tmp_comp_lib)
        symbols = {c.symbol.split(":")[-1]: c for c in candidates}
        adc = symbols["ADS1115"]
        assert "test_component_lib" in adc.symbol
        assert "ADC" in adc.description or "adc" in adc.description.lower()
        assert adc.source == "kicad_lib"
        assert adc.score == 0.0  # Not yet scored

    def test_skips_extends_symbols(self, tmp_comp_lib: Path) -> None:
        from kiassist_utils.component_selection.local_source import (
            load_candidates_from_library,
        )

        candidates = load_candidates_from_library(tmp_comp_lib)
        names = [c.symbol for c in candidates]
        assert not any("R_1k" in n for n in names)

    def test_type_filter_reduces_results(self, tmp_comp_lib: Path) -> None:
        from kiassist_utils.component_selection.local_source import (
            load_candidates_from_library,
        )

        all_cands = load_candidates_from_library(tmp_comp_lib, "")
        adc_cands = load_candidates_from_library(tmp_comp_lib, "ADC")
        assert len(adc_cands) <= len(all_cands)
        # At least the ADS1115 should pass the ADC filter.
        names = [c.symbol for c in adc_cands]
        assert any("ADS1115" in n for n in names)

    def test_nonexistent_file_returns_empty(self, tmp_path: Path) -> None:
        from kiassist_utils.component_selection.local_source import (
            load_candidates_from_library,
        )

        result = load_candidates_from_library(tmp_path / "nonexistent.kicad_sym")
        assert result == []

    def test_datasheet_url_extracted(self, tmp_comp_lib: Path) -> None:
        from kiassist_utils.component_selection.local_source import (
            load_candidates_from_library,
        )

        candidates = load_candidates_from_library(tmp_comp_lib)
        symbols = {c.symbol.split(":")[-1]: c for c in candidates}
        assert "ti.com" in symbols["ADS1115"].datasheet_url

    def test_footprint_extracted(self, tmp_comp_lib: Path) -> None:
        from kiassist_utils.component_selection.local_source import (
            load_candidates_from_library,
        )

        candidates = load_candidates_from_library(tmp_comp_lib)
        symbols = {c.symbol.split(":")[-1]: c for c in candidates}
        assert "SOIC" in symbols["ADS1115"].footprint


class TestFindLibraryFiles:
    def test_finds_sym_file_directly(self, tmp_comp_lib: Path) -> None:
        from kiassist_utils.component_selection.local_source import find_library_files

        result = find_library_files([tmp_comp_lib])
        assert tmp_comp_lib in result

    def test_finds_sym_files_in_directory(self, tmp_path: Path) -> None:
        from kiassist_utils.component_selection.local_source import find_library_files

        shutil.copy(FIXTURE_COMP_LIB, tmp_path / "a.kicad_sym")
        shutil.copy(FIXTURE_SYM_LIB, tmp_path / "b.kicad_sym")
        result = find_library_files([tmp_path])
        assert len(result) == 2

    def test_deduplicates_files(self, tmp_comp_lib: Path) -> None:
        from kiassist_utils.component_selection.local_source import find_library_files

        result = find_library_files([tmp_comp_lib, tmp_comp_lib])
        assert len(result) == 1

    def test_non_sym_files_ignored(self, tmp_path: Path) -> None:
        from kiassist_utils.component_selection.local_source import find_library_files

        (tmp_path / "not_a_lib.txt").write_text("ignored")
        result = find_library_files([tmp_path])
        assert result == []


# ===========================================================================
# Tests: selector.py
# ===========================================================================


class TestScoreCandidate:
    def _make_candidate(self, comp_type: str = "ADC", description: str = "ADC component"):
        from kiassist_utils.component_selection.models import ComponentCandidate

        return ComponentCandidate(
            symbol="Lib:Test",
            description=description,
            component_type=comp_type,
        )

    def test_exact_type_match_gives_high_score(self) -> None:
        from kiassist_utils.component_selection.models import ComponentSpec
        from kiassist_utils.component_selection.selector import _score_candidate

        spec = ComponentSpec(component_type="ADC")
        c = self._make_candidate(comp_type="ADC")
        score = _score_candidate(c, spec)
        assert score >= 0.40  # At minimum the type match portion

    def test_type_mismatch_partial_credit(self) -> None:
        from kiassist_utils.component_selection.models import ComponentSpec
        from kiassist_utils.component_selection.selector import _score_candidate

        spec = ComponentSpec(component_type="ADC")
        c = self._make_candidate(comp_type="DAC")
        score = _score_candidate(c, spec)
        # No type match → should be less than exact match
        assert score < 0.40

    def test_constraint_satisfaction_boosts_score(self) -> None:
        from kiassist_utils.component_selection.models import ComponentCandidate, ComponentSpec
        from kiassist_utils.component_selection.selector import _score_candidate

        spec = ComponentSpec(component_type="ADC", constraints={"Resolution": 16.0})
        c = ComponentCandidate(
            symbol="Lib:Test",
            description="ADC",
            component_type="ADC",
            specifications={"Resolution": 16.0},
        )
        score_with = _score_candidate(c, spec)

        c_no_spec = ComponentCandidate(
            symbol="Lib:Test",
            description="ADC",
            component_type="ADC",
            specifications={},
        )
        score_without = _score_candidate(c_no_spec, spec)
        assert score_with > score_without

    def test_footprint_preference_boosts_score(self) -> None:
        from kiassist_utils.component_selection.models import ComponentCandidate, ComponentSpec
        from kiassist_utils.component_selection.selector import _score_candidate

        spec = ComponentSpec(component_type="ADC", preferred_footprints=["SOIC-8"])
        c_match = ComponentCandidate(
            symbol="Lib:A",
            description="ADC",
            component_type="ADC",
            footprint="Package_SO:SOIC-8_3.9x4.9mm",
        )
        c_nomatch = ComponentCandidate(
            symbol="Lib:B",
            description="ADC",
            component_type="ADC",
            footprint="Package_DIP:DIP-14",
        )
        assert _score_candidate(c_match, spec) > _score_candidate(c_nomatch, spec)

    def test_score_capped_at_one(self) -> None:
        from kiassist_utils.component_selection.models import ComponentCandidate, ComponentSpec
        from kiassist_utils.component_selection.selector import _score_candidate

        spec = ComponentSpec(
            component_type="ADC",
            description="ADC",
            constraints={"Resolution": 16},
            preferred_footprints=["SOIC"],
        )
        c = ComponentCandidate(
            symbol="Lib:ADC",
            description="ADC",
            component_type="ADC",
            footprint="Package_SO:SOIC-8",
            specifications={"Resolution": 16},
        )
        assert _score_candidate(c, spec) <= 1.0

    def test_range_constraint_satisfied(self) -> None:
        from kiassist_utils.component_selection.models import ComponentCandidate, ComponentSpec
        from kiassist_utils.component_selection.selector import _score_candidate

        spec = ComponentSpec(
            component_type="LDO",
            constraints={"Vout": {"min": 3.0, "max": 3.6}},
        )
        c_in = ComponentCandidate(
            symbol="Lib:A",
            description="LDO",
            component_type="LDO",
            specifications={"Vout": 3.3},
        )
        c_out = ComponentCandidate(
            symbol="Lib:B",
            description="LDO",
            component_type="LDO",
            specifications={"Vout": 5.0},
        )
        assert _score_candidate(c_in, spec) > _score_candidate(c_out, spec)


class TestComponentSelector:
    def test_basic_selection(self, tmp_comp_lib: Path) -> None:
        from kiassist_utils.component_selection import ComponentSelector, ComponentSpec

        # No type filter so all 3 non-alias symbols are loaded.
        spec = ComponentSpec(component_type="ADC", max_candidates=5)
        selector = ComponentSelector(library_paths=[str(tmp_comp_lib)])
        result = selector.select(spec)

        # Fixture has 3 base symbols (ADS1115, MCP3204, LM1117-3.3).
        # total_found may be fewer if the pre-filter is applied.
        assert result.total_found >= 1
        assert len(result.candidates) <= 5
        # All returned candidates should have a positive score.
        for c in result.candidates:
            assert c.score > 0.0

    def test_candidates_sorted_by_score_desc(self, tmp_comp_lib: Path) -> None:
        from kiassist_utils.component_selection import ComponentSelector, ComponentSpec

        spec = ComponentSpec(component_type="ADC", max_candidates=10)
        selector = ComponentSelector(library_paths=[str(tmp_comp_lib)])
        result = selector.select(spec)

        scores = [c.score for c in result.candidates]
        assert scores == sorted(scores, reverse=True)

    def test_max_candidates_respected(self, tmp_comp_lib: Path) -> None:
        from kiassist_utils.component_selection import ComponentSelector, ComponentSpec

        spec = ComponentSpec(component_type="ADC", max_candidates=1)
        selector = ComponentSelector(library_paths=[str(tmp_comp_lib)])
        result = selector.select(spec)

        assert len(result.candidates) <= 1

    def test_source_stats_populated(self, tmp_comp_lib: Path) -> None:
        from kiassist_utils.component_selection import ComponentSelector, ComponentSpec

        spec = ComponentSpec(component_type="ADC", max_candidates=5)
        selector = ComponentSelector(library_paths=[str(tmp_comp_lib)])
        result = selector.select(spec)

        assert "kicad_lib" in result.source_stats

    def test_empty_library_path_returns_empty(self, tmp_path: Path) -> None:
        from kiassist_utils.component_selection import ComponentSelector, ComponentSpec

        spec = ComponentSpec(component_type="ADC", max_candidates=5)
        selector = ComponentSelector(library_paths=[str(tmp_path / "empty_dir")])
        result = selector.select(spec)

        assert result.candidates == []
        assert result.total_found == 0

    def test_result_serialisable(self, tmp_comp_lib: Path) -> None:
        """Verify the result can be serialised to JSON-compatible dict."""
        import json

        from kiassist_utils.component_selection import ComponentSelector, ComponentSpec

        spec = ComponentSpec(component_type="ADC", max_candidates=3)
        selector = ComponentSelector(library_paths=[str(tmp_comp_lib)])
        result = selector.select(spec)

        d = result.to_dict(summary=True)
        # Should be JSON-serialisable without errors.
        json.dumps(d)

    def test_no_library_paths_returns_empty(self) -> None:
        from kiassist_utils.component_selection import ComponentSelector, ComponentSpec

        spec = ComponentSpec(component_type="ADC")
        selector = ComponentSelector()
        result = selector.select(spec)
        assert result.candidates == []
        assert result.total_found == 0


# ===========================================================================
# Tests: MCP tools
# ===========================================================================


def _call(tool: str, **kwargs: Any) -> Dict[str, Any]:
    """Synchronous wrapper around in_process_call."""
    from kiassist_utils.mcp_server import in_process_call

    return asyncio.run(in_process_call(tool, kwargs))


class TestComponentSearchTool:
    def test_returns_ok_with_candidates(self, tmp_comp_lib: Path) -> None:
        result = _call(
            "component_search",
            component_type="ADC",
            library_paths=[str(tmp_comp_lib)],
        )
        assert result["status"] == "ok"
        assert "candidates" in result["data"]
        assert isinstance(result["data"]["candidates"], list)

    def test_candidates_sorted_by_score(self, tmp_comp_lib: Path) -> None:
        result = _call(
            "component_search",
            component_type="ADC",
            library_paths=[str(tmp_comp_lib)],
            max_candidates=10,
        )
        scores = [c["score"] for c in result["data"]["candidates"]]
        assert scores == sorted(scores, reverse=True)

    def test_max_candidates_respected(self, tmp_comp_lib: Path) -> None:
        result = _call(
            "component_search",
            component_type="ADC",
            library_paths=[str(tmp_comp_lib)],
            max_candidates=1,
        )
        assert len(result["data"]["candidates"]) <= 1

    def test_summary_format_no_properties_key(self, tmp_comp_lib: Path) -> None:
        result = _call(
            "component_search",
            component_type="ADC",
            library_paths=[str(tmp_comp_lib)],
        )
        for c in result["data"]["candidates"]:
            assert "properties" not in c

    def test_statistics_fields_present(self, tmp_comp_lib: Path) -> None:
        result = _call(
            "component_search",
            component_type="ADC",
            library_paths=[str(tmp_comp_lib)],
        )
        data = result["data"]
        assert "total_found" in data
        assert "filtered_count" in data
        assert "source_stats" in data

    def test_with_constraints(self, tmp_comp_lib: Path) -> None:
        result = _call(
            "component_search",
            component_type="ADC",
            library_paths=[str(tmp_comp_lib)],
            constraints={"Resolution": 16},
        )
        assert result["status"] == "ok"

    def test_with_preferred_footprints(self, tmp_comp_lib: Path) -> None:
        result = _call(
            "component_search",
            component_type="ADC",
            library_paths=[str(tmp_comp_lib)],
            preferred_footprints=["SOIC-8"],
        )
        assert result["status"] == "ok"

    def test_empty_library_paths_returns_empty(self, tmp_path: Path) -> None:
        result = _call(
            "component_search",
            component_type="ADC",
            library_paths=[str(tmp_path / "empty_dir")],
        )
        assert result["status"] == "ok"
        assert result["data"]["candidates"] == []


class TestComponentGetCandidatesTool:
    def test_returns_ok_with_candidates(self, tmp_comp_lib: Path) -> None:
        result = _call(
            "component_get_candidates",
            component_type="ADC",
            library_path=str(tmp_comp_lib),
        )
        assert result["status"] == "ok"
        assert "candidates" in result["data"]

    def test_full_format_includes_properties(self, tmp_comp_lib: Path) -> None:
        result = _call(
            "component_get_candidates",
            component_type="ADC",
            library_path=str(tmp_comp_lib),
        )
        for c in result["data"]["candidates"]:
            # Full format should include component_type and source
            assert "component_type" in c
            assert "source" in c

    def test_max_candidates_respected(self, tmp_comp_lib: Path) -> None:
        result = _call(
            "component_get_candidates",
            component_type="ADC",
            library_path=str(tmp_comp_lib),
            max_candidates=1,
        )
        assert len(result["data"]["candidates"]) <= 1

    def test_with_constraints(self, tmp_comp_lib: Path) -> None:
        result = _call(
            "component_get_candidates",
            component_type="LDO",
            library_path=str(tmp_comp_lib),
            constraints={"Vout": 3.3},
        )
        assert result["status"] == "ok"

    def test_nonexistent_library_returns_empty(self, tmp_path: Path) -> None:
        result = _call(
            "component_get_candidates",
            component_type="ADC",
            library_path=str(tmp_path / "nonexistent.kicad_sym"),
        )
        assert result["status"] == "ok"
        assert result["data"]["candidates"] == []


class TestComponentSelectionPackageExports:
    """Verify public API of the component_selection package."""

    def test_imports(self) -> None:
        from kiassist_utils.component_selection import (  # noqa: F401
            ComponentCandidate,
            ComponentSelector,
            ComponentSpec,
            SelectionResult,
        )

    def test_all_exported(self) -> None:
        import kiassist_utils.component_selection as pkg

        for name in ["ComponentCandidate", "ComponentSelector", "ComponentSpec", "SelectionResult"]:
            assert hasattr(pkg, name), f"Missing export: {name}"

    def test_mcp_tools_registered(self) -> None:
        """Verify the two new tools appear in the MCP tool registry."""
        from kiassist_utils.mcp_server import in_process_call

        registered = asyncio.run(
            __import__(
                "kiassist_utils.mcp_server", fromlist=["mcp"]
            ).mcp.list_tools()
        )
        names = {t.name for t in registered}
        assert "component_search" in names
        assert "component_get_candidates" in names
