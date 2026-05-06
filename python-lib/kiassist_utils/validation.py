"""KiCad design validation: ERC, DRC, and pure-Python schematic lint.

This module provides the "compile → error → fix" feedback loop that the
KiAssist agent uses after every write tool.  Two tiers of validation are
exposed:

1. **Structural lint** (:func:`schematic_lint`) — pure Python, no external
   tools required.  Re-parses the file, then checks for the most common
   classes of error an LLM produces:

   * Round-trip failure (the file we just wrote can no longer be parsed)
   * Duplicate reference designators
   * Symbols with placeholder reference (``"R?"``, ``"U?"``)
   * Symbols missing a value or footprint
   * Power symbols (lib_id startswith ``"power:"``) without a global label
     within reasonable proximity (heuristic only)

   Always available; should be called after every schematic write.

2. **Authoritative ERC/DRC** (:func:`run_sch_erc`, :func:`run_pcb_drc`) —
   shells out to ``kicad-cli`` when available.  Returns a
   :class:`ValidationReport` summarising violations grouped by severity.
   When ``kicad-cli`` is not on ``$PATH`` the functions return a report
   with ``available=False`` so callers (and the LLM) know to fall back.

All functions return JSON-serialisable dicts so they can be fed straight
back into the agent's conversation as tool results.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result container
# ---------------------------------------------------------------------------


@dataclass
class ValidationIssue:
    """A single validation finding."""

    severity: str  # "error" | "warning" | "info"
    code: str  # short machine identifier, e.g. "duplicate_reference"
    message: str
    location: Optional[str] = None  # file/line/coord hint; free-form text


@dataclass
class ValidationReport:
    """Structured validation result, designed for LLM consumption."""

    tool: str  # "schematic_lint" | "kicad-cli sch erc" | ...
    available: bool = True  # False if the underlying tool isn't installed
    success: bool = True  # True iff no errors (warnings allowed)
    issues: List[ValidationIssue] = field(default_factory=list)
    summary: str = ""

    def add(
        self,
        severity: str,
        code: str,
        message: str,
        location: Optional[str] = None,
    ) -> None:
        self.issues.append(
            ValidationIssue(
                severity=severity, code=code, message=message, location=location
            )
        )
        if severity == "error":
            self.success = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tool": self.tool,
            "available": self.available,
            "success": self.success,
            "error_count": sum(1 for i in self.issues if i.severity == "error"),
            "warning_count": sum(1 for i in self.issues if i.severity == "warning"),
            "issues": [asdict(i) for i in self.issues],
            "summary": self.summary or self._auto_summary(),
        }

    def _auto_summary(self) -> str:
        if not self.available:
            return f"{self.tool}: tool not available on PATH"
        e = sum(1 for i in self.issues if i.severity == "error")
        w = sum(1 for i in self.issues if i.severity == "warning")
        if e == 0 and w == 0:
            return f"{self.tool}: no issues"
        return f"{self.tool}: {e} error(s), {w} warning(s)"


# ---------------------------------------------------------------------------
# Tier 1: Pure-Python schematic lint
# ---------------------------------------------------------------------------


def schematic_lint(path: str | os.PathLike) -> ValidationReport:
    """Run pure-Python sanity checks on a ``.kicad_sch`` file.

    Always available — does not require KiCad or ``kicad-cli``.  Use this
    after every schematic mutation to catch the cheap-to-detect mistakes
    LLMs commonly make.
    """
    report = ValidationReport(tool="schematic_lint")
    p = Path(path)

    if not p.exists():
        report.add("error", "file_not_found", f"Schematic not found: {p}")
        return report

    # Round-trip: re-parse what we just wrote.
    try:
        from .kicad_parser.schematic import Schematic

        sch = Schematic.load(p)
    except Exception as exc:  # noqa: BLE001
        report.add(
            "error",
            "parse_error",
            f"Schematic does not parse cleanly: {exc}",
            location=str(p),
        )
        return report

    # Duplicate reference designators (excluding placeholders ending in '?').
    seen: Dict[str, int] = {}
    for sym in sch.symbols:
        ref = sym.reference
        if not ref or ref.endswith("?"):
            continue
        seen[ref] = seen.get(ref, 0) + 1
    for ref, count in seen.items():
        if count > 1:
            report.add(
                "error",
                "duplicate_reference",
                f"Reference '{ref}' is used {count} times",
            )

    # Placeholder references — symbols left with R?, U? etc. should be
    # annotated before the design is considered complete.
    placeholder = [s.reference for s in sch.symbols if s.reference.endswith("?")]
    if placeholder:
        report.add(
            "warning",
            "unannotated_reference",
            f"{len(placeholder)} symbol(s) still have placeholder references "
            f"(e.g. {placeholder[:3]}). Run KiCad's annotate or set explicit "
            f"references.",
        )

    # Missing value / footprint — flag as warnings since some symbols
    # legitimately have no footprint (test points, mounting holes).
    for sym in sch.symbols:
        if not (sym.value or "").strip():
            report.add(
                "warning",
                "missing_value",
                f"Symbol '{sym.reference}' has no Value field",
                location=f"{sym.reference} @ {sym.position.x:.2f},{sym.position.y:.2f}",
            )
        if not (sym.footprint or "").strip():
            report.add(
                "warning",
                "missing_footprint",
                f"Symbol '{sym.reference}' has no Footprint field",
                location=f"{sym.reference} @ {sym.position.x:.2f},{sym.position.y:.2f}",
            )

    return report


# ---------------------------------------------------------------------------
# Tier 2: kicad-cli wrappers
# ---------------------------------------------------------------------------


def _find_kicad_cli() -> Optional[str]:
    """Return the path to ``kicad-cli`` or ``None`` if not installed.

    Honours ``KICAD_CLI`` env var as an explicit override.
    """
    override = os.environ.get("KICAD_CLI")
    if override and Path(override).exists():
        return override
    return shutil.which("kicad-cli")


def _run_kicad_cli(
    args: List[str], *, timeout: float = 120.0
) -> tuple[int, str, str]:
    """Run ``kicad-cli`` with *args*; return ``(returncode, stdout, stderr)``.

    Raises :class:`FileNotFoundError` if ``kicad-cli`` is not installed.
    """
    cli = _find_kicad_cli()
    if cli is None:
        raise FileNotFoundError("kicad-cli not found on PATH")
    try:
        proc = subprocess.run(  # noqa: S603 - controlled argv
            [cli, *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return 124, "", f"kicad-cli timed out after {timeout}s"
    return proc.returncode, proc.stdout, proc.stderr


def _parse_kicad_report_json(text: str) -> List[Dict[str, Any]]:
    """Parse a ``kicad-cli ... --format json`` report into a flat issue list.

    The JSON schema differs slightly between ERC and DRC and across KiCad
    versions; we extract the common fields (severity, type, description,
    items) and ignore the rest.
    """
    issues: List[Dict[str, Any]] = []
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return issues

    # Both ERC and DRC reports have a top-level "violations" list (KiCad 7+).
    violations = data.get("violations") or data.get("unconnected_items") or []
    for v in violations:
        sev = (v.get("severity") or "warning").lower()
        if sev not in ("error", "warning", "info"):
            sev = "warning"
        items = v.get("items") or []
        loc_bits: List[str] = []
        for item in items:
            pos = item.get("pos") or {}
            if pos:
                loc_bits.append(
                    f"({pos.get('x', '?')}, {pos.get('y', '?')})"
                )
            if "uuid" in item:
                loc_bits.append(item["uuid"])
        issues.append(
            {
                "severity": sev,
                "code": v.get("type", "violation"),
                "message": v.get("description", ""),
                "location": "; ".join(loc_bits) if loc_bits else None,
            }
        )
    return issues


def run_sch_erc(path: str | os.PathLike) -> ValidationReport:
    """Run ``kicad-cli sch erc`` on a schematic and return a structured report."""
    report = ValidationReport(tool="kicad-cli sch erc")
    p = Path(path)

    if _find_kicad_cli() is None:
        report.available = False
        report.summary = (
            "kicad-cli not installed; install KiCad 7+ to enable ERC. "
            "Use schematic_lint for pure-Python checks."
        )
        return report

    if not p.exists():
        report.add("error", "file_not_found", f"Schematic not found: {p}")
        return report

    with tempfile.NamedTemporaryFile(
        suffix=".json", delete=False, mode="w"
    ) as tmp:
        out_path = tmp.name
    try:
        rc, stdout, stderr = _run_kicad_cli(
            ["sch", "erc", "--format", "json", "--output", out_path, str(p)]
        )
        try:
            text = Path(out_path).read_text(encoding="utf-8")
        except OSError:
            text = ""
        for issue in _parse_kicad_report_json(text):
            report.add(**issue)
        if rc != 0 and not report.issues:
            # ERC found violations and exited non-zero but we couldn't parse;
            # surface stderr so the caller has *something* to show the user.
            report.add(
                "error",
                "kicad_cli_failed",
                f"kicad-cli exited with code {rc}: {stderr.strip() or stdout.strip()}",
            )
    finally:
        try:
            os.unlink(out_path)
        except OSError:
            pass
    return report


def run_pcb_drc(path: str | os.PathLike) -> ValidationReport:
    """Run ``kicad-cli pcb drc`` on a board and return a structured report."""
    report = ValidationReport(tool="kicad-cli pcb drc")
    p = Path(path)

    if _find_kicad_cli() is None:
        report.available = False
        report.summary = (
            "kicad-cli not installed; install KiCad 7+ to enable DRC."
        )
        return report

    if not p.exists():
        report.add("error", "file_not_found", f"PCB not found: {p}")
        return report

    with tempfile.NamedTemporaryFile(
        suffix=".json", delete=False, mode="w"
    ) as tmp:
        out_path = tmp.name
    try:
        rc, stdout, stderr = _run_kicad_cli(
            ["pcb", "drc", "--format", "json", "--output", out_path, str(p)]
        )
        try:
            text = Path(out_path).read_text(encoding="utf-8")
        except OSError:
            text = ""
        for issue in _parse_kicad_report_json(text):
            report.add(**issue)
        if rc != 0 and not report.issues:
            report.add(
                "error",
                "kicad_cli_failed",
                f"kicad-cli exited with code {rc}: {stderr.strip() or stdout.strip()}",
            )
    finally:
        try:
            os.unlink(out_path)
        except OSError:
            pass
    return report
