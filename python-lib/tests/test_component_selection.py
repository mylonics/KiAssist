"""Tests for the component_selection package and associated MCP tools."""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from typing import Any, Dict

import pytest

from kiassist_utils.component_selection import (
    ComponentCandidate,
    ComponentSelector,
    ComponentSpec,
    SelectionResult,
)
from kiassist_utils.mcp_server import in_process_call

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

FIXTURE_DIR = Path(__file__).parent / "fixtures"
FIXTURE_SYM = FIXTURE_DIR / "test_component_lib.kicad_sym"


def _call(tool: str, **kwargs: Any) -> Dict[str, Any]:
    """Synchronous wrapper around in_process_call."""
    return asyncio.run(in_process_call(tool, kwargs))


def _make_selector_with_lib(lib_path: Path, project_dir: Path) -> ComponentSelector:
    """Return a ComponentSelector whose LibraryDiscovery is pointed at *lib_path*
    via a project-local sym-lib-table.

    Writes a minimal sym-lib-table file into *project_dir* so that
    LibraryDiscovery picks it up as the "TestLib" library.
    """
    table_path = project_dir / "sym-lib-table"
    table_path.write_text(
        '(sym_lib_table\n'
        f'  (lib (name "TestLib")(type "KiCad")(uri "{lib_path}")(options "")(descr "Test")))\n',
        encoding="utf-8",
    )
    return ComponentSelector(project_dir=str(project_dir))


# ===========================================================================
# ComponentSpec
# ===========================================================================


class TestComponentSpec:
    def test_defaults(self):
        spec = ComponentSpec(query="R")
        assert spec.query == "R"
        assert spec.library_filter is None
        assert spec.max_results == 50

    def test_custom_values(self):
        spec = ComponentSpec(query="cap", library_filter="Device", max_results=10)
        assert spec.library_filter == "Device"
        assert spec.max_results == 10


# ===========================================================================
# ComponentCandidate
# ===========================================================================


class TestComponentCandidate:
    def _make(self, **kwargs) -> ComponentCandidate:
        defaults = dict(
            library_name="Device",
            symbol_name="R",
            lib_id="Device:R",
            description="Resistor",
            keywords=["res", "resistor"],
            properties={"Reference": "R", "Value": "R"},
            pin_count=2,
        )
        defaults.update(kwargs)
        return ComponentCandidate(**defaults)

    def test_to_dict_round_trip(self):
        c = self._make()
        d = c.to_dict()
        assert d["library_name"] == "Device"
        assert d["lib_id"] == "Device:R"
        assert d["pin_count"] == 2
        assert "description" in d
        assert "keywords" in d
        assert "properties" in d


# ===========================================================================
# SelectionResult
# ===========================================================================


class TestSelectionResult:
    def test_to_dict(self):
        c = ComponentCandidate(
            library_name="L",
            symbol_name="S",
            lib_id="L:S",
            description="Desc",
            keywords=["kw"],
            properties={},
            pin_count=0,
        )
        result = SelectionResult(
            candidates=[c],
            library_names=["L"],
            total_searched=10,
            query="S",
        )
        d = result.to_dict()
        assert len(d["candidates"]) == 1
        assert d["library_names"] == ["L"]
        assert d["total_searched"] == 10
        assert d["query"] == "S"


# ===========================================================================
# ComponentSelector — with a real library file
# ===========================================================================


class TestComponentSelector:
    def test_list_libraries_returns_test_lib(self, tmp_path: Path):
        selector = _make_selector_with_lib(FIXTURE_SYM, tmp_path)
        libs = selector.list_libraries()
        assert "TestLib" in libs

    def test_search_finds_resistor_by_name(self, tmp_path: Path):
        selector = _make_selector_with_lib(FIXTURE_SYM, tmp_path)
        result = selector.search(ComponentSpec(query="R"))
        assert result.total_searched > 0
        lib_ids = [c.lib_id for c in result.candidates]
        assert "TestLib:R" in lib_ids

    def test_search_finds_by_description(self, tmp_path: Path):
        selector = _make_selector_with_lib(FIXTURE_SYM, tmp_path)
        result = selector.search(ComponentSpec(query="capacitor"))
        lib_ids = [c.lib_id for c in result.candidates]
        assert "TestLib:C" in lib_ids

    def test_search_finds_by_keyword(self, tmp_path: Path):
        selector = _make_selector_with_lib(FIXTURE_SYM, tmp_path)
        result = selector.search(ComponentSpec(query="timer"))
        lib_ids = [c.lib_id for c in result.candidates]
        assert "TestLib:NE555" in lib_ids

    def test_search_case_insensitive(self, tmp_path: Path):
        selector = _make_selector_with_lib(FIXTURE_SYM, tmp_path)
        result = selector.search(ComponentSpec(query="RESISTOR"))
        assert any(c.lib_id == "TestLib:R" for c in result.candidates)

    def test_search_no_results_for_unknown(self, tmp_path: Path):
        selector = _make_selector_with_lib(FIXTURE_SYM, tmp_path)
        result = selector.search(ComponentSpec(query="xyzzy_does_not_exist"))
        assert result.candidates == []

    def test_search_respects_max_results(self, tmp_path: Path):
        selector = _make_selector_with_lib(FIXTURE_SYM, tmp_path)
        # All 3 symbols match the empty-ish single-letter query "r" or similar;
        # set max_results=1 and confirm we get at most 1 result.
        result = selector.search(ComponentSpec(query="r", max_results=1))
        assert len(result.candidates) <= 1

    def test_search_library_filter(self, tmp_path: Path):
        selector = _make_selector_with_lib(FIXTURE_SYM, tmp_path)
        # Filtering on a library that does NOT match the test lib returns empty
        result = selector.search(ComponentSpec(query="R", library_filter="NonExistent"))
        assert result.candidates == []

    def test_get_candidates_returns_full_details(self, tmp_path: Path):
        selector = _make_selector_with_lib(FIXTURE_SYM, tmp_path)
        spec = ComponentSpec(query="NE555")
        candidates = selector.get_candidates("TestLib", spec)
        assert len(candidates) == 1
        c = candidates[0]
        assert c.lib_id == "TestLib:NE555"
        assert c.pin_count == 8
        assert c.description == "Single timer IC, 2.25MHz, DIP-8/SOIC-8"
        assert "timer" in c.keywords

    def test_get_candidates_unknown_library_returns_empty(self, tmp_path: Path):
        selector = _make_selector_with_lib(FIXTURE_SYM, tmp_path)
        candidates = selector.get_candidates(
            "DoesNotExist", ComponentSpec(query="R")
        )
        assert candidates == []

    def test_candidate_properties_populated(self, tmp_path: Path):
        selector = _make_selector_with_lib(FIXTURE_SYM, tmp_path)
        candidates = selector.get_candidates("TestLib", ComponentSpec(query="C"))
        assert candidates
        c = candidates[0]
        assert c.properties.get("Reference") == "C"
        assert c.properties.get("Value") == "C"

    def test_selection_result_has_library_names(self, tmp_path: Path):
        selector = _make_selector_with_lib(FIXTURE_SYM, tmp_path)
        result = selector.search(ComponentSpec(query="R"))
        assert "TestLib" in result.library_names

    def test_no_crash_for_missing_lib_file(self, tmp_path: Path):
        """Selector should skip libraries whose files cannot be loaded."""
        # Point the table at a non-existent file
        table_path = tmp_path / "sym-lib-table"
        table_path.write_text(
            '(sym_lib_table\n'
            '  (lib (name "Ghost")(type "KiCad")(uri "/no/such/file.kicad_sym")'
            '(options "")(descr "")))\n',
            encoding="utf-8",
        )
        selector = ComponentSelector(project_dir=str(tmp_path))
        result = selector.search(ComponentSpec(query="anything"))
        # Should not raise; just return empty candidates
        assert result.candidates == []


# ===========================================================================
# MCP tools — component_search & component_get_candidates
# ===========================================================================


class TestMCPComponentSearch:
    def _make_table(self, tmp_path: Path) -> Path:
        """Write a sym-lib-table that points at our fixture library."""
        table_path = tmp_path / "sym-lib-table"
        table_path.write_text(
            '(sym_lib_table\n'
            f'  (lib (name "TestLib")(type "KiCad")(uri "{FIXTURE_SYM}")'
            '(options "")(descr "Test")))\n',
            encoding="utf-8",
        )
        return tmp_path

    def test_component_search_returns_ok(self, tmp_path: Path):
        project_dir = self._make_table(tmp_path)
        result = _call(
            "component_search",
            query="R",
            project_dir=str(project_dir),
        )
        assert result["status"] == "ok"
        data = result["data"]
        assert "candidates" in data
        assert "total_searched" in data
        assert data["query"] == "R"

    def test_component_search_finds_resistor(self, tmp_path: Path):
        project_dir = self._make_table(tmp_path)
        result = _call(
            "component_search",
            query="resistor",
            project_dir=str(project_dir),
        )
        assert result["status"] == "ok"
        lib_ids = [c["lib_id"] for c in result["data"]["candidates"]]
        assert "TestLib:R" in lib_ids

    def test_component_search_library_filter(self, tmp_path: Path):
        project_dir = self._make_table(tmp_path)
        result = _call(
            "component_search",
            query="R",
            project_dir=str(project_dir),
            library_filter="TestLib",
        )
        assert result["status"] == "ok"
        for c in result["data"]["candidates"]:
            assert c["library_name"] == "TestLib"

    def test_component_search_max_results(self, tmp_path: Path):
        project_dir = self._make_table(tmp_path)
        result = _call(
            "component_search",
            query="r",
            project_dir=str(project_dir),
            max_results=1,
        )
        assert result["status"] == "ok"
        assert len(result["data"]["candidates"]) <= 1

    def test_component_get_candidates_returns_ok(self, tmp_path: Path):
        project_dir = self._make_table(tmp_path)
        result = _call(
            "component_get_candidates",
            library_name="TestLib",
            query="NE555",
            project_dir=str(project_dir),
        )
        assert result["status"] == "ok"
        data = result["data"]
        assert data["library_name"] == "TestLib"
        assert "candidates" in data

    def test_component_get_candidates_pin_count(self, tmp_path: Path):
        project_dir = self._make_table(tmp_path)
        result = _call(
            "component_get_candidates",
            library_name="TestLib",
            query="NE555",
            project_dir=str(project_dir),
        )
        candidates = result["data"]["candidates"]
        assert len(candidates) == 1
        assert candidates[0]["pin_count"] == 8

    def test_component_get_candidates_unknown_library(self, tmp_path: Path):
        project_dir = self._make_table(tmp_path)
        result = _call(
            "component_get_candidates",
            library_name="NoSuchLib",
            query="R",
            project_dir=str(project_dir),
        )
        assert result["status"] == "ok"
        assert result["data"]["candidates"] == []

    def test_component_search_no_project_dir(self):
        """component_search with no project_dir should not crash."""
        result = _call("component_search", query="R")
        assert result["status"] == "ok"
        # No libraries resolvable without project dir / KiCad installation,
        # so candidates may be empty — just verify no error is raised.
        assert "candidates" in result["data"]
