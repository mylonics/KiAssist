"""Tests for :mod:`kiassist_utils.validation` and the validation MCP tools."""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from typing import Any, Dict
from unittest import mock

import pytest

from kiassist_utils import validation
from kiassist_utils.mcp_server import in_process_call

FIXTURE_DIR = Path(__file__).parent / "fixtures"
FIXTURE_SCH = FIXTURE_DIR / "test_schematic.kicad_sch"
FIXTURE_PCB = FIXTURE_DIR / "test_pcb.kicad_pcb"


def _call(tool: str, **kwargs: Any) -> Dict[str, Any]:
    return asyncio.run(in_process_call(tool, kwargs))


@pytest.fixture()
def tmp_sch(tmp_path: Path) -> Path:
    dst = tmp_path / "sch.kicad_sch"
    shutil.copy(FIXTURE_SCH, dst)
    return dst


# ---------------------------------------------------------------------------
# ValidationReport
# ---------------------------------------------------------------------------


class TestValidationReport:
    def test_success_starts_true(self):
        r = validation.ValidationReport(tool="t")
        assert r.success is True
        assert r.to_dict()["error_count"] == 0

    def test_error_flips_success(self):
        r = validation.ValidationReport(tool="t")
        r.add("warning", "w", "warn")
        assert r.success is True
        r.add("error", "e", "boom")
        assert r.success is False
        d = r.to_dict()
        assert d["error_count"] == 1
        assert d["warning_count"] == 1
        assert d["issues"][0]["severity"] == "warning"

    def test_summary_no_issues(self):
        r = validation.ValidationReport(tool="schematic_lint")
        assert "no issues" in r.to_dict()["summary"]


# ---------------------------------------------------------------------------
# schematic_lint (pure Python)
# ---------------------------------------------------------------------------


class TestSchematicLint:
    def test_clean_fixture_passes(self, tmp_sch: Path):
        report = validation.schematic_lint(tmp_sch)
        # Fixture may have warnings (missing footprint on test components)
        # but must not have parse / duplicate errors.
        assert report.tool == "schematic_lint"
        codes = {i.code for i in report.issues if i.severity == "error"}
        assert codes == set(), f"Unexpected errors: {codes}"

    def test_missing_file(self, tmp_path: Path):
        report = validation.schematic_lint(tmp_path / "nope.kicad_sch")
        assert report.success is False
        assert any(i.code == "file_not_found" for i in report.issues)

    def test_duplicate_reference_detected(self, tmp_sch: Path):
        # Add two symbols sharing the same reference.
        _call(
            "schematic_add_symbol",
            path=str(tmp_sch),
            lib_id="Device:R",
            x=10.0,
            y=10.0,
            reference="DUP1",
            value="1k",
        )
        _call(
            "schematic_add_symbol",
            path=str(tmp_sch),
            lib_id="Device:R",
            x=20.0,
            y=20.0,
            reference="DUP1",
            value="2k",
        )
        report = validation.schematic_lint(tmp_sch)
        assert report.success is False
        assert any(i.code == "duplicate_reference" for i in report.issues)

    def test_placeholder_reference_warning(self, tmp_sch: Path):
        _call(
            "schematic_add_symbol",
            path=str(tmp_sch),
            lib_id="Device:R",
            x=30.0,
            y=30.0,
            reference="R?",
            value="100",
        )
        report = validation.schematic_lint(tmp_sch)
        # Errors are still allowed to be empty; we expect at least the warning.
        assert any(
            i.code == "unannotated_reference" and i.severity == "warning"
            for i in report.issues
        )

    def test_parse_error_reported(self, tmp_path: Path):
        bad = tmp_path / "broken.kicad_sch"
        bad.write_text("(this is not a valid schematic", encoding="utf-8")
        report = validation.schematic_lint(bad)
        assert report.success is False
        assert any(i.code == "parse_error" for i in report.issues)


# ---------------------------------------------------------------------------
# kicad-cli wrappers (mocked)
# ---------------------------------------------------------------------------


class TestKicadCliWrappers:
    def test_run_sch_erc_unavailable(self, tmp_sch: Path, monkeypatch):
        monkeypatch.setattr(validation, "_find_kicad_cli", lambda: None)
        report = validation.run_sch_erc(tmp_sch)
        assert report.available is False
        assert report.success is True  # not a hard error
        assert "kicad-cli" in report.summary

    def test_run_pcb_drc_unavailable(self, tmp_path: Path, monkeypatch):
        # Don't need a real PCB file when the tool is missing.
        pcb = tmp_path / "x.kicad_pcb"
        pcb.write_text("(kicad_pcb)")
        monkeypatch.setattr(validation, "_find_kicad_cli", lambda: None)
        report = validation.run_pcb_drc(pcb)
        assert report.available is False

    def test_run_sch_erc_parses_violations(self, tmp_sch: Path, monkeypatch):
        # Pretend kicad-cli exists and writes a JSON report.
        monkeypatch.setattr(validation, "_find_kicad_cli", lambda: "/fake/kicad-cli")

        def fake_run(args, *, timeout=120.0):
            # args ends with [..., '--output', out_path, str(p)]
            out_path = args[args.index("--output") + 1]
            Path(out_path).write_text(
                """{
                  "violations": [
                    {
                      "type": "lib_symbol_issues",
                      "severity": "error",
                      "description": "Symbol R1 has no footprint",
                      "items": [{"pos": {"x": 10, "y": 20}, "uuid": "abc"}]
                    },
                    {
                      "type": "unconnected",
                      "severity": "warning",
                      "description": "Pin not connected",
                      "items": []
                    }
                  ]
                }""",
                encoding="utf-8",
            )
            return 1, "", ""

        monkeypatch.setattr(validation, "_run_kicad_cli", fake_run)
        report = validation.run_sch_erc(tmp_sch)
        assert report.available is True
        assert report.success is False  # one error
        codes = [i.code for i in report.issues]
        assert "lib_symbol_issues" in codes
        assert "unconnected" in codes


# ---------------------------------------------------------------------------
# MCP tool wiring
# ---------------------------------------------------------------------------


class TestValidationMcpTools:
    def test_schematic_lint_tool_returns_ok(self, tmp_sch: Path):
        result = _call("schematic_lint", path=str(tmp_sch))
        assert result["status"] == "ok"
        assert result["data"]["tool"] == "schematic_lint"
        assert "issues" in result["data"]

    def test_schematic_lint_rejects_bad_extension(self, tmp_path: Path):
        f = tmp_path / "x.txt"
        f.write_text("nope")
        result = _call("schematic_lint", path=str(f))
        assert result["status"] == "error"

    def test_schematic_run_erc_unavailable_returns_ok(
        self, tmp_sch: Path, monkeypatch
    ):
        # When kicad-cli is missing the tool must still return status=ok with
        # available=False so the agent can fall back to schematic_lint.
        monkeypatch.setattr(validation, "_find_kicad_cli", lambda: None)
        result = _call("schematic_run_erc", path=str(tmp_sch))
        assert result["status"] == "ok"
        assert result["data"]["available"] is False

    def test_pcb_run_drc_unavailable_returns_ok(self, monkeypatch):
        monkeypatch.setattr(validation, "_find_kicad_cli", lambda: None)
        result = _call("pcb_run_drc", path=str(FIXTURE_PCB))
        assert result["status"] == "ok"
        assert result["data"]["available"] is False
