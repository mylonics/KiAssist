"""IPC Save/Reload Workflow for KiAssist (Phase 5).

This module implements the bridge between AI-driven file edits and live KiCad
instances:

* **Save detection** — determine whether a file is currently open in a running
  KiCad instance and whether it has unsaved changes (5.1).
* **File edit pipeline** — orchestrate the ``save → edit → reload`` sequence
  so that KiCad always reflects the AI's changes (5.3).
* **Concurrency safety** — a cross-platform file-lock context manager that
  prevents concurrent writes, plus automatic rollback on parser errors (5.5).

Typical usage::

    from kiassist_utils.ipc_workflow import SchematicEditPipeline

    async def add_resistor(sch_path: str) -> dict:
        pipeline = SchematicEditPipeline(sch_path)
        return await pipeline.run("schematic_add_symbol", {...})
"""

from __future__ import annotations

import asyncio
import fcntl
import logging
import os
import platform
import shutil
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Optional dependency — kicad_ipc is always available in the same package.
# Import it at module level so tests can patch the name via
# ``kiassist_utils.ipc_workflow.detect_kicad_instances``.
# ---------------------------------------------------------------------------

try:
    from .kicad_ipc import detect_kicad_instances as detect_kicad_instances
except ImportError:  # pragma: no cover
    def detect_kicad_instances():  # type: ignore[misc]
        """Stub used when kicad_ipc is unavailable."""
        return []

# ---------------------------------------------------------------------------
# Lazy reference to MCP in_process_call (same pattern as tool_executor.py).
# Exposed at module level so tests can monkeypatch it:
#   patch("kiassist_utils.ipc_workflow.in_process_call", ...)
# ---------------------------------------------------------------------------

in_process_call: Optional[Any] = None


def _get_in_process_call() -> Any:
    """Return the MCP in_process_call coroutine, importing it on first use."""
    global in_process_call
    if in_process_call is None:
        from .mcp_server import in_process_call as _ipc  # noqa: PLC0415
        in_process_call = _ipc
    return in_process_call


# ---------------------------------------------------------------------------
# 5.1 — Save Detection helpers
# ---------------------------------------------------------------------------

_SYSTEM = platform.system()


def get_file_mtime(path: str | os.PathLike) -> Optional[float]:
    """Return the last-modified timestamp of *path*, or ``None`` if missing.

    Args:
        path: File path to inspect.

    Returns:
        Modification time as a POSIX timestamp (seconds since epoch), or
        ``None`` when the file does not exist.
    """
    try:
        return os.path.getmtime(path)
    except OSError:
        return None


def is_file_open_in_kicad(file_path: str) -> bool:
    """Return ``True`` if *file_path* appears to be open in a running KiCad instance.

    The check is performed by comparing the normalised absolute path of
    *file_path* against the schematic and PCB paths reported by every
    detected KiCad IPC instance.

    Args:
        file_path: Absolute or relative path to a ``.kicad_sch`` or
                   ``.kicad_pcb`` file.

    Returns:
        ``True`` if a running KiCad instance has the file open,
        ``False`` otherwise (including when KiCad is not running or
        ``kicad-python`` is not installed).
    """
    try:
        instances = detect_kicad_instances()
    except Exception:  # noqa: BLE001
        return False

    norm = os.path.normpath(os.path.abspath(file_path))
    for inst in instances:
        for key in ("schematic_path", "pcb_path"):
            inst_path = inst.get(key, "")
            if inst_path and os.path.normpath(os.path.abspath(inst_path)) == norm:
                return True
    return False


def check_file_status(file_path: str) -> Dict[str, Any]:
    """Return a status dict describing the current state of *file_path*.

    Provides:

    * ``exists`` – whether the file exists on disk.
    * ``mtime``  – last-modified POSIX timestamp (``None`` when absent).
    * ``open_in_kicad`` – whether a running KiCad instance has the file open.
    * ``bak_exists`` – whether a ``<file>.bak`` backup exists.

    Args:
        file_path: Path to a KiCad schematic or PCB file.

    Returns:
        Status dictionary.
    """
    path = Path(file_path)
    mtime = get_file_mtime(path)
    return {
        "path": str(path.resolve()),
        "exists": path.exists(),
        "mtime": mtime,
        "open_in_kicad": is_file_open_in_kicad(file_path),
        "bak_exists": Path(str(path) + ".bak").exists(),
    }


# ---------------------------------------------------------------------------
# 5.5 — Concurrency Safety
# ---------------------------------------------------------------------------

# Per-process in-memory lock registry (keyed by normalised path).  Prevents
# two tasks in the *same* process from editing the same file concurrently.
_in_process_locks: Dict[str, asyncio.Lock] = {}


def _get_async_lock(norm_path: str) -> asyncio.Lock:
    """Return (creating if necessary) the async lock for *norm_path*."""
    if norm_path not in _in_process_locks:
        _in_process_locks[norm_path] = asyncio.Lock()
    return _in_process_locks[norm_path]


@contextmanager
def _advisory_file_lock(path: Path) -> Generator[None, None, None]:
    """Acquire an OS-level advisory lock on ``<path>.lock``.

    Uses ``fcntl.flock`` on POSIX and a simple creation-based lock on
    Windows (where ``fcntl`` is unavailable).

    Args:
        path: The file being edited (a sibling ``.lock`` file is created).

    Yields:
        Nothing — the lock is held for the duration of the ``with`` block.
    """
    lock_path = Path(str(path) + ".lock")

    if _SYSTEM != "Windows":
        # POSIX: use fcntl exclusive lock on a dedicated lock file.
        fd = os.open(str(lock_path), os.O_CREAT | os.O_WRONLY, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)
            try:
                lock_path.unlink(missing_ok=True)
            except OSError:
                pass
    else:
        # Windows: fall back to a best-effort creation-based lock.
        # We try to create the lock file exclusively; if it already exists,
        # we poll briefly and raise if we cannot acquire within the timeout.
        deadline = time.monotonic() + 30.0  # 30-second acquisition timeout
        acquired = False
        while time.monotonic() < deadline:
            try:
                fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                os.close(fd)
                acquired = True
                break
            except FileExistsError:
                time.sleep(0.1)
        if not acquired:
            raise TimeoutError(
                f"Could not acquire file lock for {path} within 30 seconds. "
                "Another process may be editing the file."
            )
        try:
            yield
        finally:
            try:
                lock_path.unlink(missing_ok=True)
            except OSError:
                pass


def rollback_from_backup(path: str | os.PathLike) -> bool:
    """Restore *path* from its ``.bak`` backup file.

    Args:
        path: Path to the file to restore.

    Returns:
        ``True`` if the backup was successfully restored, ``False`` if no
        backup existed.
    """
    dest = Path(path)
    bak = Path(str(dest) + ".bak")
    if not bak.exists():
        logger.warning("No backup file found for %s; cannot rollback.", dest)
        return False
    try:
        shutil.copy2(bak, dest)
        logger.info("Rolled back %s from %s.", dest, bak)
        return True
    except OSError as exc:
        logger.error("Rollback failed for %s: %s", dest, exc)
        return False


# ---------------------------------------------------------------------------
# 5.3 — File Edit Pipeline
# ---------------------------------------------------------------------------

# Delay (seconds) between triggering Ctrl+S and starting the file edit.
# Gives KiCad a moment to flush the file to disk.
_SAVE_WAIT_SECONDS = 1.0

# Delay (seconds) between completing the file write and triggering Ctrl+Shift+R.
# Gives the OS a moment to flush the file so KiCad re-reads the new content.
_RELOAD_WAIT_SECONDS = 0.5


class SchematicEditPipeline:
    """Orchestrate the KiCad keyboard-save → direct-file-edit → keyboard-reload workflow.

    **Architecture note — IPC vs direct file edits:**

    KiCad exposes a native IPC API (via kipy over a Unix socket / named pipe)
    that KiAssist uses **only for detection** (determining which files are
    currently open in a live KiCad instance, via
    :func:`is_file_open_in_kicad`).  The IPC API does *not* expose save or
    reload commands, so those operations fall back to keyboard automation:

    * **Save** — sends Ctrl+S to the focused KiCad window via
      ``kicad_save_schematic`` (ctypes on Windows, xdotool on Linux, osascript
      on macOS).
    * **Reload** — sends Ctrl+Shift+R via ``kicad_reload_schematic``.

    This is a necessary workaround and is intentional per the Phase 5 design.
    The actual *file modifications* are always performed by our custom parsers
    writing directly to disk — never through the KiCad IPC API.

    The pipeline therefore follows this sequence:

    1. **IPC detection** — check if the target file is open in KiCad.
    2. **Keyboard save** (if open) — flush any unsaved KiCad changes to disk.
    3. **Direct file edit** — invoke the requested MCP tool (parser-based).
    4. **Rollback** — restore the ``.bak`` backup if the edit fails.
    5. **Keyboard reload** (if open and edit succeeded) — KiCad re-reads the
       new file from disk.

    Args:
        file_path:        Path to the ``.kicad_sch`` (or ``.kicad_pcb``) file.
        window_title_hint: Forwarded to the save/reload keyboard-automation
                          helpers (used to target a specific KiCad window on
                          platforms that support it).
        save_before_edit: When ``True`` (default), trigger Ctrl+S in KiCad
                          before modifying the file.
        reload_after_edit: When ``True`` (default), trigger Ctrl+Shift+R in
                           KiCad after modifying the file.
        save_wait:        Seconds to wait after triggering save (default 1 s).
        reload_wait:      Seconds to wait before triggering reload (default 0.5 s).
    """

    def __init__(
        self,
        file_path: str,
        *,
        window_title_hint: str = "",
        save_before_edit: bool = True,
        reload_after_edit: bool = True,
        save_wait: float = _SAVE_WAIT_SECONDS,
        reload_wait: float = _RELOAD_WAIT_SECONDS,
    ) -> None:
        self.file_path = file_path
        self.window_title_hint = window_title_hint
        self.save_before_edit = save_before_edit
        self.reload_after_edit = reload_after_edit
        self.save_wait = save_wait
        self.reload_wait = reload_wait

    async def run(
        self,
        tool_name: str,
        tool_args: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Execute the full save → edit → reload pipeline.

        Steps:

        1. If *save_before_edit*, check whether the file is open in KiCad
           and—if so—trigger ``kicad_save_schematic`` then wait briefly for
           KiCad to flush the file.
        2. Acquire an advisory file lock for the duration of the edit.
        3. Invoke *tool_name* via the MCP ``in_process_call`` helper.
        4. If the tool returned an error, restore the ``.bak`` backup (rollback).
        5. Release the lock.
        6. If *reload_after_edit* and the file is still open in KiCad, trigger
           ``kicad_reload_schematic`` after a brief wait.

        Args:
            tool_name: Name of the MCP tool that performs the file edit (e.g.
                       ``"schematic_add_symbol"``).
            tool_args: Arguments forwarded to *tool_name*.

        Returns:
            The raw return value of *tool_name* (typically a dict with
            ``status`` and ``data`` keys).
        """
        ipc = _get_in_process_call()
        path = Path(self.file_path)
        norm = os.path.normpath(os.path.abspath(self.file_path))
        file_open = is_file_open_in_kicad(self.file_path)

        # Step 1 — save KiCad before editing
        save_result: Optional[Dict[str, Any]] = None
        if self.save_before_edit and file_open:
            logger.info("Triggering KiCad save before editing %s.", path.name)
            try:
                save_result = await ipc(
                    "kicad_save_schematic",
                    {"window_title_hint": self.window_title_hint},
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("kicad_save_schematic failed: %s", exc)
            # Wait for KiCad to flush the file to disk
            if self.save_wait > 0:
                await asyncio.sleep(self.save_wait)

        # Step 2 & 3 — acquire lock and run the edit tool
        async_lock = _get_async_lock(norm)
        async with async_lock:
            edit_result: Any = None
            edit_ok = False
            with _advisory_file_lock(path):
                try:
                    edit_result = await ipc(tool_name, tool_args)
                    # A dict result with status == "ok" indicates success;
                    # anything else (non-dict, or status != "ok") is treated
                    # as a failure for rollback purposes.
                    if isinstance(edit_result, dict):
                        edit_ok = edit_result.get("status") == "ok"
                    else:
                        edit_ok = edit_result is not None
                except Exception as exc:  # noqa: BLE001
                    logger.error("Edit tool %r raised: %s", tool_name, exc)
                    edit_result = {"status": "error", "message": str(exc)}

                # Step 4 — rollback on failure
                if not edit_ok:
                    rolled_back = rollback_from_backup(path)
                    if isinstance(edit_result, dict):
                        edit_result["rolled_back"] = rolled_back
                    logger.warning(
                        "Edit tool %r failed for %s; rolled_back=%s.",
                        tool_name,
                        path.name,
                        rolled_back,
                    )

        # Step 6 — reload KiCad after editing
        reload_result: Optional[Dict[str, Any]] = None
        if self.reload_after_edit and file_open and edit_ok:
            if self.reload_wait > 0:
                await asyncio.sleep(self.reload_wait)
            logger.info("Triggering KiCad reload after editing %s.", path.name)
            try:
                reload_result = await ipc(
                    "kicad_reload_schematic",
                    {"window_title_hint": self.window_title_hint},
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("kicad_reload_schematic failed: %s", exc)

        # Annotate the result with pipeline metadata.
        # save_triggered / reload_triggered are True only when the respective
        # keyboard-automation call succeeded (status == "ok"), not merely when
        # the call was attempted.
        def _call_succeeded(r: Optional[Dict[str, Any]]) -> bool:
            return isinstance(r, dict) and r.get("status") == "ok"

        pipeline_meta: Dict[str, Any] = {
            "file_was_open_in_kicad": file_open,
            "save_triggered": _call_succeeded(save_result),
            "reload_triggered": _call_succeeded(reload_result),
        }
        if isinstance(edit_result, dict):
            edit_result["pipeline"] = pipeline_meta
        return edit_result  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Convenience helper: pipeline for a list of sequential edits
# ---------------------------------------------------------------------------


async def run_edit_pipeline(
    file_path: str,
    edits: List[Dict[str, Any]],
    *,
    window_title_hint: str = "",
    save_before_first: bool = True,
    reload_after_last: bool = True,
    save_wait: float = _SAVE_WAIT_SECONDS,
    reload_wait: float = _RELOAD_WAIT_SECONDS,
) -> List[Dict[str, Any]]:
    """Run multiple sequential MCP tool edits on a single file with one save/reload cycle.

    Unlike creating a :class:`SchematicEditPipeline` for each edit (which
    would trigger a save + reload for *every* individual tool call), this
    helper saves once before the batch and reloads once afterwards.

    Args:
        file_path:         Path to the KiCad file being edited.
        edits:             List of dicts, each with ``tool`` (str) and
                           ``args`` (dict) keys.
        window_title_hint: Forwarded to the save/reload helpers.
        save_before_first: Trigger KiCad save before the first edit.
        reload_after_last: Trigger KiCad reload after the last successful edit.
        save_wait:         Seconds to wait after triggering save.
        reload_wait:       Seconds to wait before triggering reload.

    Returns:
        List of result dicts (one per edit), each annotated with a
        ``pipeline`` key containing metadata.
    """
    ipc = _get_in_process_call()
    path = Path(file_path)
    norm = os.path.normpath(os.path.abspath(file_path))
    file_open = is_file_open_in_kicad(file_path)

    # Save before the batch; capture the result to accurately report success.
    save_triggered = False
    if save_before_first and file_open:
        try:
            save_res = await ipc(
                "kicad_save_schematic",
                {"window_title_hint": window_title_hint},
            )
            save_triggered = isinstance(save_res, dict) and save_res.get("status") == "ok"
            if not save_triggered:
                logger.warning("kicad_save_schematic returned non-ok result: %s", save_res)
        except Exception as exc:  # noqa: BLE001
            logger.warning("kicad_save_schematic failed: %s", exc)
        if save_wait > 0:
            await asyncio.sleep(save_wait)

    results: List[Dict[str, Any]] = []
    all_ok = True
    async_lock = _get_async_lock(norm)

    async with async_lock:
        with _advisory_file_lock(path):
            for edit in edits:
                tool_name: str = edit["tool"]
                tool_args: Dict[str, Any] = edit.get("args", {})
                edit_result: Any = None
                edit_ok = False
                try:
                    edit_result = await ipc(tool_name, tool_args)
                    if isinstance(edit_result, dict):
                        edit_ok = edit_result.get("status") == "ok"
                    else:
                        edit_ok = edit_result is not None
                except Exception as exc:  # noqa: BLE001
                    logger.error("Edit tool %r raised: %s", tool_name, exc)
                    edit_result = {"status": "error", "message": str(exc)}

                if not edit_ok:
                    all_ok = False
                    rolled_back = rollback_from_backup(path)
                    if isinstance(edit_result, dict):
                        edit_result["rolled_back"] = rolled_back
                        edit_result["pipeline"] = {
                            "file_was_open_in_kicad": file_open,
                            "save_triggered": save_triggered,
                            "reload_triggered": False,
                        }
                    logger.warning(
                        "Edit tool %r failed for %s; rolled_back=%s. "
                        "Aborting remaining edits in batch.",
                        tool_name,
                        path.name,
                        rolled_back,
                    )
                    results.append(edit_result)
                    break  # Stop on first failure

                meta: Dict[str, Any] = {
                    "file_was_open_in_kicad": file_open,
                    "save_triggered": save_triggered,
                    "reload_triggered": False,
                }
                if isinstance(edit_result, dict):
                    edit_result["pipeline"] = meta
                results.append(edit_result)

    # Reload after the batch; capture result to accurately report success.
    reload_triggered = False
    if reload_after_last and file_open and all_ok:
        if reload_wait > 0:
            await asyncio.sleep(reload_wait)
        try:
            reload_res = await ipc(
                "kicad_reload_schematic",
                {"window_title_hint": window_title_hint},
            )
            reload_triggered = isinstance(reload_res, dict) and reload_res.get("status") == "ok"
            if not reload_triggered:
                logger.warning("kicad_reload_schematic returned non-ok result: %s", reload_res)
        except Exception as exc:  # noqa: BLE001
            logger.warning("kicad_reload_schematic failed: %s", exc)

    # Annotate the last result with reload info
    if results and isinstance(results[-1], dict) and "pipeline" in results[-1]:
        results[-1]["pipeline"]["reload_triggered"] = reload_triggered

    return results
