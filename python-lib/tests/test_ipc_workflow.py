"""Tests for the Phase 5 IPC save/reload workflow (ipc_workflow.py).

Covers:
* 5.1 — Save detection helpers (check_file_status, is_file_open_in_kicad)
* 5.5 — Concurrency safety (advisory file lock, rollback_from_backup)
* 5.3 — File edit pipeline (SchematicEditPipeline, run_edit_pipeline)
* New MCP tools: kicad_check_file_status, kicad_edit_file_pipeline
* Bug fix: kicad_reload_schematic uses subprocess.run (not os.system) on Linux
"""

from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path
from typing import Any, Dict
from unittest import mock

import pytest

import kiassist_utils.ipc_workflow as wf
from kiassist_utils.ipc_workflow import (
    SchematicEditPipeline,
    check_file_status,
    get_file_mtime,
    is_file_open_in_kicad,
    rollback_from_backup,
    run_edit_pipeline,
)
from kiassist_utils.mcp_server import in_process_call

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

FIXTURE_DIR = Path(__file__).parent / "fixtures"
FIXTURE_SCH = FIXTURE_DIR / "test_schematic.kicad_sch"


def _call(tool: str, **kwargs: Any) -> Dict[str, Any]:
    """Synchronous wrapper around in_process_call."""
    return asyncio.run(in_process_call(tool, kwargs))


@pytest.fixture()
def tmp_sch(tmp_path: Path) -> Path:
    dst = tmp_path / "sch.kicad_sch"
    shutil.copy(FIXTURE_SCH, dst)
    return dst


# ===========================================================================
# 5.1 — Save Detection
# ===========================================================================


class TestGetFileMtime:
    def test_existing_file_returns_float(self, tmp_path: Path):
        f = tmp_path / "test.txt"
        f.write_text("hello")
        mtime = get_file_mtime(f)
        assert isinstance(mtime, float)
        assert mtime > 0

    def test_missing_file_returns_none(self, tmp_path: Path):
        mtime = get_file_mtime(tmp_path / "nonexistent.txt")
        assert mtime is None


class TestIsFileOpenInKicad:
    def test_returns_false_when_no_instances(self, tmp_path: Path):
        f = tmp_path / "test.kicad_sch"
        f.write_text("(kicad_sch)")
        with mock.patch.object(wf, "detect_kicad_instances", return_value=[]):
            assert is_file_open_in_kicad(str(f)) is False

    def test_returns_true_when_schematic_matches(self, tmp_path: Path):
        f = tmp_path / "my.kicad_sch"
        f.write_text("(kicad_sch)")
        instances = [{"schematic_path": str(f), "pcb_path": ""}]
        with mock.patch.object(wf, "detect_kicad_instances", return_value=instances):
            assert is_file_open_in_kicad(str(f)) is True

    def test_returns_true_for_pcb_path(self, tmp_path: Path):
        f = tmp_path / "board.kicad_pcb"
        f.write_text("(kicad_pcb)")
        instances = [{"schematic_path": "", "pcb_path": str(f)}]
        with mock.patch.object(wf, "detect_kicad_instances", return_value=instances):
            assert is_file_open_in_kicad(str(f)) is True

    def test_returns_false_on_detect_exception(self, tmp_path: Path):
        f = tmp_path / "test.kicad_sch"
        f.write_text("(kicad_sch)")
        with mock.patch.object(
            wf, "detect_kicad_instances", side_effect=RuntimeError("connection refused")
        ):
            assert is_file_open_in_kicad(str(f)) is False


class TestCheckFileStatus:
    def test_existing_file_not_open(self, tmp_path: Path):
        f = tmp_path / "sch.kicad_sch"
        f.write_text("(kicad_sch)")
        with mock.patch.object(wf, "detect_kicad_instances", return_value=[]):
            status = check_file_status(str(f))

        assert status["exists"] is True
        assert isinstance(status["mtime"], float)
        assert status["open_in_kicad"] is False
        assert status["bak_exists"] is False
        assert "path" in status

    def test_missing_file(self, tmp_path: Path):
        f = tmp_path / "missing.kicad_sch"
        with mock.patch.object(wf, "detect_kicad_instances", return_value=[]):
            status = check_file_status(str(f))

        assert status["exists"] is False
        assert status["mtime"] is None

    def test_bak_exists_flag(self, tmp_path: Path):
        f = tmp_path / "sch.kicad_sch"
        f.write_text("(kicad_sch)")
        Path(str(f) + ".bak").write_text("(old)")
        with mock.patch.object(wf, "detect_kicad_instances", return_value=[]):
            status = check_file_status(str(f))

        assert status["bak_exists"] is True

    def test_file_open_in_kicad(self, tmp_path: Path):
        f = tmp_path / "sch.kicad_sch"
        f.write_text("(kicad_sch)")
        instances = [{"schematic_path": str(f), "pcb_path": ""}]
        with mock.patch.object(wf, "detect_kicad_instances", return_value=instances):
            status = check_file_status(str(f))

        assert status["open_in_kicad"] is True


# ===========================================================================
# 5.5 — Concurrency Safety
# ===========================================================================


class TestRollbackFromBackup:
    def test_restores_backup(self, tmp_path: Path):
        f = tmp_path / "sch.kicad_sch"
        f.write_text("current content")
        Path(str(f) + ".bak").write_text("backup content")

        assert rollback_from_backup(f) is True
        assert f.read_text() == "backup content"

    def test_returns_false_when_no_backup(self, tmp_path: Path):
        f = tmp_path / "sch.kicad_sch"
        f.write_text("current content")

        assert rollback_from_backup(f) is False
        assert f.read_text() == "current content"

    def test_accepts_str_path(self, tmp_path: Path):
        f = tmp_path / "sch.kicad_sch"
        f.write_text("current")
        Path(str(f) + ".bak").write_text("restored")

        assert rollback_from_backup(str(f)) is True
        assert f.read_text() == "restored"


# ===========================================================================
# 5.3 — SchematicEditPipeline
# ===========================================================================


def _make_fake_ipc(results: dict):
    """Return an async callable that returns pre-defined results by tool name."""

    async def _ipc(tool_name: str, args: dict) -> Any:
        return results.get(tool_name, {"status": "ok", "data": {}})

    return _ipc


class TestSchematicEditPipeline:
    def test_no_save_no_reload_when_file_not_open(self, tmp_sch: Path):
        """When KiCad is not running, no save/reload should be triggered."""
        calls: list = []

        async def fake_ipc(tool_name: str, args: dict) -> Any:
            calls.append(tool_name)
            return {"status": "ok", "data": {}}

        async def _run():
            pipeline = SchematicEditPipeline(
                str(tmp_sch),
                save_before_edit=True,
                reload_after_edit=True,
                save_wait=0,
                reload_wait=0,
            )
            with mock.patch.object(wf, "is_file_open_in_kicad", return_value=False):
                with mock.patch.object(wf, "in_process_call", fake_ipc):
                    return await pipeline.run("my_edit_tool", {"path": str(tmp_sch)})

        result = asyncio.run(_run())
        assert result["status"] == "ok"
        assert result["pipeline"]["file_was_open_in_kicad"] is False
        assert result["pipeline"]["save_triggered"] is False
        assert result["pipeline"]["reload_triggered"] is False
        assert "kicad_save_schematic" not in calls
        assert "kicad_reload_schematic" not in calls
        assert "my_edit_tool" in calls

    def test_save_and_reload_triggered_when_file_open(self, tmp_sch: Path):
        """When the file is open in KiCad, save and reload should be triggered."""
        calls: list = []

        async def fake_ipc(tool_name: str, args: dict) -> Any:
            calls.append(tool_name)
            return {"status": "ok", "data": {}}

        async def _run():
            pipeline = SchematicEditPipeline(
                str(tmp_sch),
                save_before_edit=True,
                reload_after_edit=True,
                save_wait=0,
                reload_wait=0,
            )
            with mock.patch.object(wf, "is_file_open_in_kicad", return_value=True):
                with mock.patch.object(wf, "in_process_call", fake_ipc):
                    return await pipeline.run("my_edit_tool", {})

        result = asyncio.run(_run())
        assert result["pipeline"]["file_was_open_in_kicad"] is True
        assert result["pipeline"]["save_triggered"] is True
        assert result["pipeline"]["reload_triggered"] is True
        assert calls[0] == "kicad_save_schematic"
        assert calls[-1] == "kicad_reload_schematic"

    def test_rollback_on_error_result(self, tmp_sch: Path):
        """A tool returning status=error triggers rollback from .bak backup."""
        bak = Path(str(tmp_sch) + ".bak")
        original = tmp_sch.read_text(encoding="utf-8")
        bak.write_text(original, encoding="utf-8")

        async def fake_ipc(tool_name: str, args: dict) -> Any:
            return {"status": "error", "message": "something broke"}

        async def _run():
            pipeline = SchematicEditPipeline(
                str(tmp_sch),
                save_before_edit=False,
                reload_after_edit=False,
                save_wait=0,
                reload_wait=0,
            )
            with mock.patch.object(wf, "is_file_open_in_kicad", return_value=False):
                with mock.patch.object(wf, "in_process_call", fake_ipc):
                    return await pipeline.run("failing_tool", {})

        result = asyncio.run(_run())
        assert result["status"] == "error"
        assert result["rolled_back"] is True

    def test_no_reload_when_edit_fails(self, tmp_sch: Path):
        """When the edit fails, reload must NOT be triggered (to avoid reloading bad state)."""
        calls: list = []

        async def fake_ipc(tool_name: str, args: dict) -> Any:
            calls.append(tool_name)
            return {"status": "error", "message": "oops"}

        async def _run():
            pipeline = SchematicEditPipeline(
                str(tmp_sch),
                save_before_edit=False,
                reload_after_edit=True,
                save_wait=0,
                reload_wait=0,
            )
            with mock.patch.object(wf, "is_file_open_in_kicad", return_value=True):
                with mock.patch.object(wf, "in_process_call", fake_ipc):
                    return await pipeline.run("bad_tool", {})

        result = asyncio.run(_run())
        assert result["pipeline"]["reload_triggered"] is False
        assert "kicad_reload_schematic" not in calls

    def test_save_triggered_false_when_save_returns_error(self, tmp_sch: Path):
        """save_triggered must be False when kicad_save_schematic returns status=error."""
        calls: list = []

        async def fake_ipc(tool_name: str, args: dict) -> Any:
            calls.append(tool_name)
            if tool_name == "kicad_save_schematic":
                return {"status": "error", "message": "xdotool not found"}
            return {"status": "ok", "data": {}}

        async def _run():
            pipeline = SchematicEditPipeline(
                str(tmp_sch),
                save_before_edit=True,
                reload_after_edit=True,
                save_wait=0,
                reload_wait=0,
            )
            with mock.patch.object(wf, "is_file_open_in_kicad", return_value=True):
                with mock.patch.object(wf, "in_process_call", fake_ipc):
                    return await pipeline.run("my_edit_tool", {})

        result = asyncio.run(_run())
        # Save was attempted but returned error → save_triggered must be False
        assert "kicad_save_schematic" in calls
        assert result["pipeline"]["save_triggered"] is False
        # The edit itself succeeded
        assert result["status"] == "ok"

    def test_reload_triggered_false_when_reload_returns_error(self, tmp_sch: Path):
        """reload_triggered must be False when kicad_reload_schematic returns status=error."""
        async def fake_ipc(tool_name: str, args: dict) -> Any:
            if tool_name == "kicad_reload_schematic":
                return {"status": "error", "message": "xdotool not found"}
            return {"status": "ok", "data": {}}

        async def _run():
            pipeline = SchematicEditPipeline(
                str(tmp_sch),
                save_before_edit=False,
                reload_after_edit=True,
                save_wait=0,
                reload_wait=0,
            )
            with mock.patch.object(wf, "is_file_open_in_kicad", return_value=True):
                with mock.patch.object(wf, "in_process_call", fake_ipc):
                    return await pipeline.run("my_edit_tool", {})

        result = asyncio.run(_run())
        assert result["pipeline"]["reload_triggered"] is False

    def test_exception_in_tool_results_in_error(self, tmp_sch: Path):
        """An exception raised by the tool returns an error result."""

        async def fake_ipc(tool_name: str, args: dict) -> Any:
            raise RuntimeError("tool exploded")

        async def _run():
            pipeline = SchematicEditPipeline(
                str(tmp_sch),
                save_before_edit=False,
                reload_after_edit=False,
                save_wait=0,
                reload_wait=0,
            )
            with mock.patch.object(wf, "is_file_open_in_kicad", return_value=False):
                with mock.patch.object(wf, "in_process_call", fake_ipc):
                    return await pipeline.run("explode_tool", {})

        result = asyncio.run(_run())
        assert result["status"] == "error"
        assert "tool exploded" in result["message"]


# ===========================================================================
# 5.3 — run_edit_pipeline (batch helper)
# ===========================================================================


class TestRunEditPipeline:
    def test_single_successful_edit(self, tmp_sch: Path):
        async def fake_ipc(tool_name: str, args: dict) -> Any:
            return {"status": "ok", "data": {}}

        async def _run():
            with mock.patch.object(wf, "is_file_open_in_kicad", return_value=False):
                with mock.patch.object(wf, "in_process_call", fake_ipc):
                    return await run_edit_pipeline(
                        str(tmp_sch),
                        [{"tool": "my_tool", "args": {}}],
                        save_before_first=False,
                        reload_after_last=False,
                        save_wait=0,
                        reload_wait=0,
                    )

        results = asyncio.run(_run())
        assert len(results) == 1
        assert results[0]["status"] == "ok"
        assert results[0]["pipeline"]["save_triggered"] is False

    def test_batch_stops_on_first_failure(self, tmp_sch: Path):
        """When a tool in a batch fails, subsequent tools are skipped."""
        called: list = []
        bak = Path(str(tmp_sch) + ".bak")
        bak.write_text(tmp_sch.read_text(encoding="utf-8"), encoding="utf-8")

        async def fake_ipc(tool_name: str, args: dict) -> Any:
            called.append(tool_name)
            if tool_name == "fail_tool":
                return {"status": "error", "message": "boom"}
            return {"status": "ok", "data": {}}

        async def _run():
            with mock.patch.object(wf, "is_file_open_in_kicad", return_value=False):
                with mock.patch.object(wf, "in_process_call", fake_ipc):
                    return await run_edit_pipeline(
                        str(tmp_sch),
                        [
                            {"tool": "ok_tool", "args": {}},
                            {"tool": "fail_tool", "args": {}},
                            {"tool": "never_called", "args": {}},
                        ],
                        save_before_first=False,
                        reload_after_last=False,
                        save_wait=0,
                        reload_wait=0,
                    )

        results = asyncio.run(_run())
        assert len(results) == 2  # ok_tool + fail_tool; never_called is skipped
        assert results[1]["status"] == "error"
        assert "pipeline" in results[1], "failed result must include pipeline metadata"
        assert "never_called" not in called

    def test_save_before_and_reload_after(self, tmp_sch: Path):
        """When KiCad is open, save once before batch and reload once after."""
        called: list = []

        async def fake_ipc(tool_name: str, args: dict) -> Any:
            called.append(tool_name)
            return {"status": "ok", "data": {}}

        async def _run():
            with mock.patch.object(wf, "is_file_open_in_kicad", return_value=True):
                with mock.patch.object(wf, "in_process_call", fake_ipc):
                    return await run_edit_pipeline(
                        str(tmp_sch),
                        [
                            {"tool": "edit_a", "args": {}},
                            {"tool": "edit_b", "args": {}},
                        ],
                        save_before_first=True,
                        reload_after_last=True,
                        save_wait=0,
                        reload_wait=0,
                    )

        results = asyncio.run(_run())
        assert len(results) == 2
        assert called[0] == "kicad_save_schematic"
        assert called[-1] == "kicad_reload_schematic"
        assert called.count("kicad_save_schematic") == 1
        assert called.count("kicad_reload_schematic") == 1


# ===========================================================================
# New MCP tools
# ===========================================================================


class TestKiCadCheckFileStatusTool:
    def test_existing_file(self, tmp_sch: Path):
        with mock.patch.object(wf, "detect_kicad_instances", return_value=[]):
            result = _call("kicad_check_file_status", path=str(tmp_sch))

        assert result["status"] == "ok"
        data = result["data"]
        assert data["exists"] is True
        assert isinstance(data["mtime"], float)
        assert data["open_in_kicad"] is False

    def test_missing_file(self, tmp_path: Path):
        with mock.patch.object(wf, "detect_kicad_instances", return_value=[]):
            result = _call("kicad_check_file_status", path=str(tmp_path / "no.kicad_sch"))

        assert result["status"] == "ok"
        assert result["data"]["exists"] is False
        assert result["data"]["mtime"] is None


class TestKiCadEditFilePipelineTool:
    def test_bad_json_args_returns_error(self, tmp_sch: Path):
        result = _call(
            "kicad_edit_file_pipeline",
            file_path=str(tmp_sch),
            tool_name="schematic_add_wire",
            tool_args="not valid json {",
        )
        assert result["status"] == "error"
        assert "JSON" in result["message"]

    def test_successful_pipeline_with_real_schematic_tool(self, tmp_sch: Path):
        """End-to-end: use the pipeline to add a wire to the schematic file."""
        args = json.dumps({
            "path": str(tmp_sch),
            "x1": 0.0,
            "y1": 0.0,
            "x2": 5.0,
            "y2": 0.0,
        })
        with mock.patch.object(wf, "is_file_open_in_kicad", return_value=False):
            result = _call(
                "kicad_edit_file_pipeline",
                file_path=str(tmp_sch),
                tool_name="schematic_add_wire",
                tool_args=args,
                save_before_edit=False,
                reload_after_edit=False,
            )

        assert result["status"] == "ok"
        assert "pipeline" in result
        assert result["pipeline"]["save_triggered"] is False
        assert result["pipeline"]["reload_triggered"] is False

    def test_pipeline_metadata_present(self, tmp_sch: Path):
        """The pipeline key must always be present in the result."""
        args = json.dumps({"path": str(tmp_sch), "x1": 1.0, "y1": 1.0, "x2": 2.0, "y2": 1.0})
        with mock.patch.object(wf, "is_file_open_in_kicad", return_value=False):
            result = _call(
                "kicad_edit_file_pipeline",
                file_path=str(tmp_sch),
                tool_name="schematic_add_wire",
                tool_args=args,
            )

        assert "pipeline" in result
        pipeline = result["pipeline"]
        assert "file_was_open_in_kicad" in pipeline
        assert "save_triggered" in pipeline
        assert "reload_triggered" in pipeline


# ===========================================================================
# Bug fix: kicad_reload_schematic must use subprocess.run (not os.system)
# ===========================================================================


class TestKiCadReloadSchematicUsesSubprocess:
    """Verify that the kicad_reload_schematic fix uses subprocess.run on Linux."""

    def test_linux_uses_subprocess_run_not_os_system(self):
        import kiassist_utils.mcp_server as ms

        with mock.patch.object(ms.platform, "system", return_value="Linux"):
            with mock.patch.object(ms.shutil, "which", return_value="/usr/bin/xdotool"):
                with mock.patch.object(
                    ms.subprocess, "run", return_value=mock.Mock(returncode=0)
                ) as mock_run:
                    with mock.patch.object(ms.os, "system") as mock_os_system:
                        result = _call("kicad_reload_schematic")

        assert mock_run.called, "subprocess.run must be used for xdotool"
        assert not mock_os_system.called, "os.system must NOT be used"
        assert result["status"] == "ok"
        assert result["data"]["method"] == "xdotool"
