"""KiCad library analyzer and fixer for symbol (.kicad_sym) and footprint (.kicad_mod) files.

Performs deep structural, semantic, and KiCad-10 compatibility checks on
symbol and footprint libraries.  Detected issues are classified by severity
(``error``, ``warning``, ``info``) and many can be auto-fixed in-place.

Typical usage::

    from kiassist_utils.kicad_parser.analyzer import LibraryAnalyzer

    analyzer = LibraryAnalyzer()
    report = analyzer.analyze_symbol_library("Device.kicad_sym")
    print(report.summary())

    # Auto-fix all fixable issues and save:
    fixed = analyzer.fix_symbol_library("Device.kicad_sym", "Device_fixed.kicad_sym")
    print(f"Fixed {fixed} issues")
"""

from __future__ import annotations

import copy
import difflib
import math
import os
import re
import uuid as _uuid_mod
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from .sexpr import QStr, SExpr, parse, serialize
from .models import Effects, Position, Property, KiUUID
from .symbol_lib import Pin, SymbolDef, SymbolLibrary, SymbolUnit
from .footprint import Footprint, Pad, FootprintGraphic
from ._helpers import _find, _find_all, _atom, _parse_position, _parse_effects


# ═══════════════════════════════════════════════════════════════════════════
# Issue types / report model
# ═══════════════════════════════════════════════════════════════════════════


class Severity(str, Enum):
    """Issue severity level."""
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


class IssueCategory(str, Enum):
    """Broad category for an issue."""
    STRUCTURE = "structure"         # File / S-expression structure
    VERSION = "version"             # Version / generator metadata
    PROPERTY = "property"           # Missing / malformed properties
    PIN = "pin"                     # Pin consistency
    GRAPHIC = "graphic"             # Drawing / graphic elements
    PAD = "pad"                     # Footprint pad issues
    LAYER = "layer"                 # Layer assignment
    NAMING = "naming"               # Naming conventions
    UNIT = "unit"                   # Multi-unit consistency
    COURTYARD = "courtyard"         # Courtyard rule compliance
    MODEL = "model"                 # 3-D model attachment
    UUID = "uuid"                   # UUID uniqueness / validity
    ENCODING = "encoding"           # Text / encoding issues
    DEPRECATED = "deprecated"       # Deprecated constructs


@dataclass
class Issue:
    """A single problem detected during analysis.

    Attributes:
        severity:    ``error``, ``warning``, or ``info``.
        category:    Broad classification of the issue.
        entity:      Name of the symbol or footprint in question.
        message:     Human-readable description.
        fixable:     ``True`` when :meth:`LibraryAnalyzer.fix_*` can resolve it.
        fix_action:  Short label for the fix (e.g. ``"add_uuid"``).
        details:     Extra context dict (e.g. expected vs actual values).
    """

    severity: Severity
    category: IssueCategory
    entity: str
    message: str
    fixable: bool = False
    fix_action: str = ""
    details: Dict[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:  # pragma: no cover
        fix = " [FIXABLE]" if self.fixable else ""
        return f"[{self.severity.value.upper()}] {self.category.value}: {self.entity} — {self.message}{fix}"


@dataclass
class AnalysisReport:
    """Container for all issues found during analysis.

    Attributes:
        file_path:  Path of the analysed file.
        file_type:  ``"symbol_library"`` or ``"footprint"``.
        issues:     Ordered list of :class:`Issue` objects.
    """

    file_path: str = ""
    file_type: str = ""
    issues: List[Issue] = field(default_factory=list)

    # ── Convenience accessors ──────────────────────────────────────────

    @property
    def errors(self) -> List[Issue]:
        return [i for i in self.issues if i.severity == Severity.ERROR]

    @property
    def warnings(self) -> List[Issue]:
        return [i for i in self.issues if i.severity == Severity.WARNING]

    @property
    def infos(self) -> List[Issue]:
        return [i for i in self.issues if i.severity == Severity.INFO]

    @property
    def fixable(self) -> List[Issue]:
        return [i for i in self.issues if i.fixable]

    @property
    def is_clean(self) -> bool:
        return len(self.errors) == 0

    def by_category(self, cat: IssueCategory) -> List[Issue]:
        return [i for i in self.issues if i.category == cat]

    def by_entity(self, name: str) -> List[Issue]:
        return [i for i in self.issues if i.entity == name]

    # ── Report output ──────────────────────────────────────────────────

    def summary(self) -> str:
        """Return a compact multi-line summary string."""
        lines = [
            f"Analysis report for: {self.file_path}",
            f"Type: {self.file_type}",
            f"Total issues: {len(self.issues)}  "
            f"(E={len(self.errors)} W={len(self.warnings)} I={len(self.infos)})  "
            f"Fixable: {len(self.fixable)}",
            "",
        ]
        for issue in self.issues:
            lines.append(str(issue))
        return "\n".join(lines)

    def to_dict(self) -> Dict[str, Any]:
        """Serialise to a JSON-friendly dict."""
        return {
            "file_path": self.file_path,
            "file_type": self.file_type,
            "total": len(self.issues),
            "errors": len(self.errors),
            "warnings": len(self.warnings),
            "infos": len(self.infos),
            "fixable": len(self.fixable),
            "issues": [
                {
                    "severity": i.severity.value,
                    "category": i.category.value,
                    "entity": i.entity,
                    "message": i.message,
                    "fixable": i.fixable,
                    "fix_action": i.fix_action,
                    "details": i.details,
                }
                for i in self.issues
            ],
        }


# ═══════════════════════════════════════════════════════════════════════════
# KiCad-10 constants
# ═══════════════════════════════════════════════════════════════════════════

# KiCad 10 format version identifiers
KICAD10_SYMBOL_LIB_VERSION = 20231120
KICAD10_FOOTPRINT_VERSION = 20240108

# Generator names that KiCad 10 recognizes
KICAD10_KNOWN_GENERATORS = {
    "kicad_symbol_editor", "eeschema", "pcbnew",
    "kicad_footprint_editor", "footprint_editor",
}

# Required properties for well-formed symbol definitions
SYMBOL_REQUIRED_PROPERTIES = {"Reference", "Value", "Footprint", "Datasheet"}

# Valid pin electrical types in KiCad 10
VALID_PIN_ELECTRICAL_TYPES = {
    "input", "output", "bidirectional", "tri_state",
    "passive", "free", "unspecified", "power_in",
    "power_out", "open_collector", "open_emitter", "no_connect",
}

# Valid pin graphic styles in KiCad 10
VALID_PIN_GRAPHIC_STYLES = {
    "line", "inverted", "clock", "inverted_clock",
    "input_low", "clock_low", "output_low", "edge_clock_high",
    "non_logic",
}

# Valid pad types
VALID_PAD_TYPES = {"smd", "thru_hole", "connect", "np_thru_hole"}

# Valid pad shapes
VALID_PAD_SHAPES = {"circle", "rect", "roundrect", "oval", "trapezoid", "custom"}

# Valid copper / technical layer names (KiCad 10)
VALID_LAYERS = {
    "F.Cu", "B.Cu", "In1.Cu", "In2.Cu", "In3.Cu", "In4.Cu",
    "In5.Cu", "In6.Cu", "In7.Cu", "In8.Cu",
    "F.Paste", "B.Paste", "F.Mask", "B.Mask",
    "F.SilkS", "B.SilkS", "F.Fab", "B.Fab",
    "F.CrtYd", "B.CrtYd", "Dwgs.User", "Cmts.User",
    "Eco1.User", "Eco2.User", "Edge.Cuts", "Margin",
    "User.1", "User.2", "User.3", "User.4", "User.5",
    "User.6", "User.7", "User.8", "User.9",
    "*.Cu", "*.Mask", "*.Paste", "*.SilkS",
}

# UUID v4 regex
UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)

# Valid expected indent depths per KiCad .kicad_sym structural keyword
# depth 0 = outside everything, depth 1 = inside kicad_symbol_lib
KICAD_SYM_VALID_DEPTHS: Dict[str, Tuple[int, ...]] = {
    "kicad_symbol_lib": (0,),
    "version": (1,),
    "generator": (1,),
    "generator_version": (1,),
    "symbol": (1, 2, 3),  # top-level, sub-symbol, sub-sub
    "property": (2, 3),
    "pin": (3,),
    "pin_names": (2,),
    "pin_numbers": (2,),
    "exclude_from_sim": (2,),
    "in_bom": (2,),
    "on_board": (2,),
    "embedded_fonts": (1,),
}


# ═══════════════════════════════════════════════════════════════════════════
# Raw-text / string-aware structural analysis
# ═══════════════════════════════════════════════════════════════════════════


@dataclass
class _DepthInfo:
    """Parenthesis depth info for a single line."""
    line_number: int     # 1-based
    opens: int           # count of structural '(' on this line
    closes: int          # count of structural ')' on this line
    depth_before: int    # depth at start of line (before any parens)
    depth_after: int     # depth at end of line (after all parens)
    min_depth: int       # minimum depth reached during this line
    text: str            # raw line text


@dataclass
class _RawTextReport:
    """Result of raw-text structural analysis."""
    total_opens: int = 0
    total_closes: int = 0
    imbalance: int = 0           # opens - closes (positive = unclosed, negative = extra closes)
    min_depth: int = 0           # minimum depth reached anywhere
    min_depth_line: int = 0      # line number where minimum depth was reached
    negative_depth_lines: List[int] = field(default_factory=list)  # lines where depth < 0
    line_depths: List[_DepthInfo] = field(default_factory=list)
    is_balanced: bool = True
    is_structurally_sound: bool = True  # balanced AND no negative depth


class _RawTextAnalysis:
    """String-aware parenthesis analysis operating on raw file text.

    Handles:
    - Parentheses inside quoted strings are literal, not structural
    - Escape sequences (\\") inside strings
    - Line-by-line depth tracking
    - Detection of negative depth points
    """

    @staticmethod
    def count_parens_string_aware(text: str) -> Tuple[int, int]:
        """Count structural (non-string) open and close parens.

        Returns:
            (opens, closes) — only parentheses outside of quoted strings.
        """
        opens = 0
        closes = 0
        in_string = False
        prev_backslash = False
        for ch in text:
            if in_string:
                if prev_backslash:
                    prev_backslash = False
                    continue
                if ch == '\\':
                    prev_backslash = True
                elif ch == '"':
                    in_string = False
            else:
                if ch == '"':
                    in_string = True
                elif ch == '(':
                    opens += 1
                elif ch == ')':
                    closes += 1
        return opens, closes

    @staticmethod
    def analyze_depth_per_line(text: str) -> _RawTextReport:
        """Perform line-by-line depth analysis of the file.

        Tracks depth through the file respecting string quoting.
        Detects negative depth (extra close parens) and overall imbalance.

        Returns:
            A :class:`_RawTextReport` with full line-by-line depth data.
        """
        report = _RawTextReport()
        lines = text.split('\n')
        depth = 0
        global_min = 0
        global_min_line = 1

        for line_idx, line in enumerate(lines):
            line_num = line_idx + 1
            line_opens = 0
            line_closes = 0
            line_min_depth = depth
            current_depth = depth

            in_string = False
            prev_backslash = False

            for ch in line:
                if in_string:
                    if prev_backslash:
                        prev_backslash = False
                        continue
                    if ch == '\\':
                        prev_backslash = True
                    elif ch == '"':
                        in_string = False
                else:
                    if ch == '"':
                        in_string = True
                    elif ch == '(':
                        line_opens += 1
                        current_depth += 1
                    elif ch == ')':
                        line_closes += 1
                        current_depth -= 1
                        if current_depth < line_min_depth:
                            line_min_depth = current_depth

            info = _DepthInfo(
                line_number=line_num,
                opens=line_opens,
                closes=line_closes,
                depth_before=depth,
                depth_after=current_depth,
                min_depth=line_min_depth,
                text=line,
            )
            report.line_depths.append(info)

            if line_min_depth < 0:
                report.negative_depth_lines.append(line_num)

            if line_min_depth < global_min:
                global_min = line_min_depth
                global_min_line = line_num

            report.total_opens += line_opens
            report.total_closes += line_closes
            depth = current_depth

        report.imbalance = report.total_opens - report.total_closes
        report.min_depth = global_min
        report.min_depth_line = global_min_line
        report.is_balanced = (report.imbalance == 0)
        report.is_structurally_sound = report.is_balanced and global_min >= 0

        return report

    @staticmethod
    def validate_structure(text: str) -> Tuple[bool, str]:
        """Quick validation: balanced and no negative depth.

        Returns:
            (is_valid, message)
        """
        r = _RawTextAnalysis.analyze_depth_per_line(text)
        if r.is_structurally_sound:
            return True, "Structure is valid"
        problems = []
        if r.imbalance > 0:
            problems.append(f"{r.imbalance} unclosed parenthesis(es)")
        elif r.imbalance < 0:
            problems.append(f"{abs(r.imbalance)} extra closing parenthesis(es)")
        if r.negative_depth_lines:
            nl = r.negative_depth_lines[:5]
            problems.append(f"Negative depth at line(s): {nl}")
        return False, "; ".join(problems)

    @staticmethod
    def find_stray_close_candidates(text: str) -> List[Tuple[int, str]]:
        """Find lines that are candidates for stray close-paren removal.

        A candidate is a line where depth goes negative.  We track depth
        through the file and mark lines where a ')' causes the depth to
        drop below zero.

        Returns:
            List of (line_number, line_text) for candidate stray lines.
        """
        r = _RawTextAnalysis.analyze_depth_per_line(text)
        candidates = []
        for info in r.line_depths:
            if info.min_depth < 0:
                candidates.append((info.line_number, info.text))
        return candidates

    @staticmethod
    def iterative_fix_stray_parens(text: str, max_iterations: int = 500) -> Tuple[str, int]:
        """Remove stray closing parentheses one at a time using depth-guided selection.

        Strategy:
        1. Scan for the first line where depth goes negative.
        2. On that line, remove the ')' that caused negative depth.
        3. Re-scan from scratch (depth changes cascade).
        4. Repeat until no negative depth and file is balanced, or max_iterations.

        This handles tip #4 (remove only what's needed), #6 (iterative single-line),
        and implicitly #3 (identify before removing) by only targeting ')' chars
        that actually cause depth violations.

        Returns:
            (fixed_text, removals_count)
        """
        lines = text.split('\n')
        removals = 0

        for _ in range(max_iterations):
            # Pass 1: find first line where depth drops below zero
            depth = 0
            target_line = -1
            target_char_pos = -1
            in_string = False
            prev_backslash = False

            for line_idx, line in enumerate(lines):
                found = False
                in_string = False
                prev_backslash = False
                for char_idx, ch in enumerate(line):
                    if in_string:
                        if prev_backslash:
                            prev_backslash = False
                            continue
                        if ch == '\\':
                            prev_backslash = True
                        elif ch == '"':
                            in_string = False
                    else:
                        if ch == '"':
                            in_string = True
                        elif ch == '(':
                            depth += 1
                        elif ch == ')':
                            depth -= 1
                            if depth < 0:
                                target_line = line_idx
                                target_char_pos = char_idx
                                found = True
                                break
                if found:
                    break

            if target_line < 0:
                # No negative depth found; check balance
                break

            # Remove the offending ')' from that position
            old_line = lines[target_line]
            lines[target_line] = old_line[:target_char_pos] + old_line[target_char_pos + 1:]

            # If the line is now empty/whitespace only, remove it entirely
            if not lines[target_line].strip():
                lines.pop(target_line)

            removals += 1

        # Pass 2: handle unclosed parens (more opens than closes).
        # Find the last line where a lone '(' (with no matching ')') exists
        # and the file overall has opens > closes.
        for _ in range(max_iterations):
            joined = '\n'.join(lines)
            r = _RawTextAnalysis.analyze_depth_per_line(joined)
            if r.imbalance <= 0:
                break

            # Find the deepest point in the file and try to close there,
            # or find a stray open.  Safest: find lines that have opens
            # but after removing one open the imbalance improves.
            # Strategy: scan from end backwards for lines with opens,
            # remove the last '(' if removing it would improve balance.
            removed = False
            for line_idx in range(len(lines) - 1, -1, -1):
                line = lines[line_idx]
                in_string = False
                prev_backslash = False
                last_open_pos = -1
                for ci, ch in enumerate(line):
                    if in_string:
                        if prev_backslash:
                            prev_backslash = False
                            continue
                        if ch == '\\':
                            prev_backslash = True
                        elif ch == '"':
                            in_string = False
                    else:
                        if ch == '"':
                            in_string = True
                        elif ch == '(':
                            last_open_pos = ci

                if last_open_pos >= 0:
                    old_line = lines[line_idx]
                    lines[line_idx] = old_line[:last_open_pos] + old_line[last_open_pos + 1:]
                    if not lines[line_idx].strip():
                        lines.pop(line_idx)
                    removals += 1
                    removed = True
                    break

            if not removed:
                break

        return '\n'.join(lines), removals

    @staticmethod
    def diff_against_bak(
        file_path: str | os.PathLike,
    ) -> Optional[List[str]]:
        """If a .bak file exists, return a unified diff highlighting differences.

        KiCad auto-generates .bak files that are often balanced.  Diffing
        against them can narrow down where corruption was introduced.

        Returns:
            List of diff lines, or None if no .bak exists.
        """
        p = Path(file_path)
        bak = p.with_suffix(p.suffix + ".bak")
        if not bak.exists():
            # Also try just .bak extension
            bak = p.with_suffix(".bak")
            if not bak.exists():
                return None

        try:
            original = p.read_text(encoding="utf-8", errors="replace").splitlines()
            backup = bak.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return None

        diff = list(difflib.unified_diff(
            backup, original,
            fromfile=str(bak.name),
            tofile=str(p.name),
            lineterm="",
        ))
        return diff if diff else None


# ═══════════════════════════════════════════════════════════════════════════
# Structural validation exception
# ═══════════════════════════════════════════════════════════════════════════


class StructuralValidationError(Exception):
    """Raised when a file fails post-fix structural validation.

    This prevents writing structurally broken files (tip #5).
    """
    pass


# ═══════════════════════════════════════════════════════════════════════════
# Symbol library analysis checks
# ═══════════════════════════════════════════════════════════════════════════


class _SymbolChecks:
    """Collection of check methods for symbol libraries."""

    @staticmethod
    def check_version(lib: SymbolLibrary, report: AnalysisReport) -> None:
        """Check format version is compatible with KiCad 10."""
        if lib.version == 0:
            report.issues.append(Issue(
                severity=Severity.ERROR,
                category=IssueCategory.VERSION,
                entity="<library>",
                message="Missing version field",
                fixable=True,
                fix_action="set_version",
                details={"expected": KICAD10_SYMBOL_LIB_VERSION},
            ))
        elif lib.version < 20211014:
            report.issues.append(Issue(
                severity=Severity.ERROR,
                category=IssueCategory.VERSION,
                entity="<library>",
                message=f"Version {lib.version} is too old for KiCad 10 (minimum 20211014)",
                fixable=True,
                fix_action="upgrade_version",
                details={"current": lib.version, "target": KICAD10_SYMBOL_LIB_VERSION},
            ))
        elif lib.version < KICAD10_SYMBOL_LIB_VERSION:
            report.issues.append(Issue(
                severity=Severity.WARNING,
                category=IssueCategory.VERSION,
                entity="<library>",
                message=f"Version {lib.version} is older than current KiCad 10 format ({KICAD10_SYMBOL_LIB_VERSION})",
                fixable=True,
                fix_action="upgrade_version",
                details={"current": lib.version, "target": KICAD10_SYMBOL_LIB_VERSION},
            ))

    @staticmethod
    def check_generator(lib: SymbolLibrary, report: AnalysisReport) -> None:
        """Check generator metadata."""
        if not lib.generator:
            report.issues.append(Issue(
                severity=Severity.WARNING,
                category=IssueCategory.VERSION,
                entity="<library>",
                message="Missing generator field",
                fixable=True,
                fix_action="set_generator",
            ))

    @staticmethod
    def check_raw_structure(text: str, report: AnalysisReport) -> None:
        """String-aware structural analysis of raw file text.

        Uses depth tracking (not naive paren counting) to detect:
        - Overall imbalance
        - Negative depth (extra close parens mid-file)
        - Stray brackets at specific line numbers
        """
        raw = _RawTextAnalysis.analyze_depth_per_line(text)
        if raw.is_structurally_sound:
            return

        if raw.imbalance > 0:
            report.issues.append(Issue(
                severity=Severity.ERROR,
                category=IssueCategory.STRUCTURE,
                entity="<library>",
                message=f"{raw.imbalance} unclosed parenthesis(es) — file has more '(' than ')'",
                fixable=True,
                fix_action="fix_stray_parens",
                details={"imbalance": raw.imbalance, "total_opens": raw.total_opens,
                         "total_closes": raw.total_closes},
            ))
        elif raw.imbalance < 0:
            report.issues.append(Issue(
                severity=Severity.ERROR,
                category=IssueCategory.STRUCTURE,
                entity="<library>",
                message=f"{abs(raw.imbalance)} extra closing parenthesis(es) — file has more ')' than '('",
                fixable=True,
                fix_action="fix_stray_parens",
                details={"imbalance": raw.imbalance, "total_opens": raw.total_opens,
                         "total_closes": raw.total_closes},
            ))

        if raw.negative_depth_lines:
            lines_preview = raw.negative_depth_lines[:10]
            report.issues.append(Issue(
                severity=Severity.ERROR,
                category=IssueCategory.STRUCTURE,
                entity="<library>",
                message=f"Depth drops below zero at {len(raw.negative_depth_lines)} location(s): lines {lines_preview}",
                fixable=True,
                fix_action="fix_stray_parens",
                details={"negative_depth_lines": raw.negative_depth_lines},
            ))

    @staticmethod
    def check_bak_diff(file_path: str, report: AnalysisReport) -> None:
        """If a .bak file exists and differs, report it for reference."""
        diff = _RawTextAnalysis.diff_against_bak(file_path)
        if diff is not None:
            # Count changed lines
            added = sum(1 for l in diff if l.startswith('+') and not l.startswith('+++'))
            removed = sum(1 for l in diff if l.startswith('-') and not l.startswith('---'))
            report.issues.append(Issue(
                severity=Severity.INFO,
                category=IssueCategory.STRUCTURE,
                entity="<library>",
                message=f"Backup file differs: {added} lines added, {removed} lines removed since .bak",
                fixable=False,
                details={"diff_line_count": len(diff), "added": added, "removed": removed},
            ))

    @staticmethod
    def check_duplicate_symbols(lib: SymbolLibrary, report: AnalysisReport) -> None:
        """Detect duplicate symbol names."""
        seen: Dict[str, int] = {}
        for sym in lib.symbols:
            seen[sym.name] = seen.get(sym.name, 0) + 1
        for name, count in seen.items():
            if count > 1:
                report.issues.append(Issue(
                    severity=Severity.ERROR,
                    category=IssueCategory.NAMING,
                    entity=name,
                    message=f"Duplicate symbol name (appears {count} times)",
                    fixable=False,
                    details={"count": count},
                ))

    @staticmethod
    def check_symbol_name(sym: SymbolDef, report: AnalysisReport) -> None:
        """Check symbol name for invalid characters / conventions."""
        if not sym.name:
            report.issues.append(Issue(
                severity=Severity.ERROR,
                category=IssueCategory.NAMING,
                entity="<unnamed>",
                message="Symbol has an empty name",
                fixable=False,
            ))
            return
        # KiCad 10 forbids certain chars in symbol names
        forbidden = set(':*?"<>|')
        bad = forbidden & set(sym.name)
        if bad:
            report.issues.append(Issue(
                severity=Severity.ERROR,
                category=IssueCategory.NAMING,
                entity=sym.name,
                message=f"Symbol name contains forbidden characters: {bad}",
                fixable=True,
                fix_action="sanitize_name",
                details={"forbidden_chars": list(bad)},
            ))

    @staticmethod
    def check_required_properties(sym: SymbolDef, report: AnalysisReport) -> None:
        """Ensure all KiCad-required properties exist."""
        existing = {p.key for p in sym.properties}
        for req in SYMBOL_REQUIRED_PROPERTIES:
            if req not in existing:
                report.issues.append(Issue(
                    severity=Severity.ERROR,
                    category=IssueCategory.PROPERTY,
                    entity=sym.name,
                    message=f"Missing required property '{req}'",
                    fixable=True,
                    fix_action="add_property",
                    details={"property": req},
                ))

    @staticmethod
    def check_property_values(sym: SymbolDef, report: AnalysisReport) -> None:
        """Validate property values for common issues."""
        for prop in sym.properties:
            # Reference must not be empty
            if prop.key == "Reference" and not prop.value.strip():
                report.issues.append(Issue(
                    severity=Severity.WARNING,
                    category=IssueCategory.PROPERTY,
                    entity=sym.name,
                    message="Reference property is empty",
                    fixable=True,
                    fix_action="set_default_reference",
                    details={"property": "Reference"},
                ))
            # Duplicate property keys
        keys = [p.key for p in sym.properties]
        dup_keys = {k for k in keys if keys.count(k) > 1}
        for k in dup_keys:
            report.issues.append(Issue(
                severity=Severity.WARNING,
                category=IssueCategory.PROPERTY,
                entity=sym.name,
                message=f"Duplicate property key '{k}'",
                fixable=True,
                fix_action="deduplicate_properties",
                details={"property": k},
            ))

    @staticmethod
    def check_deprecated_property_ids(sym: SymbolDef, report: AnalysisReport) -> None:
        """Detect legacy (id N) property syntax deprecated in KiCad 8+."""
        if sym.raw_tree is None:
            return
        for child in sym.raw_tree:
            if isinstance(child, list) and child and child[0] == "property":
                id_node = _find(child, "id")
                if id_node is not None:
                    report.issues.append(Issue(
                        severity=Severity.WARNING,
                        category=IssueCategory.DEPRECATED,
                        entity=sym.name,
                        message=f"Property '{child[1] if len(child) > 1 else '?'}' uses deprecated (id N) syntax",
                        fixable=True,
                        fix_action="remove_property_ids",
                        details={"property": str(child[1]) if len(child) > 1 else ""},
                    ))

    @staticmethod
    def check_deprecated_bare_hide(sym: SymbolDef, report: AnalysisReport) -> None:
        """Detect bare `hide` atoms in effects (deprecated in KiCad 8+, should be (hide yes))."""
        if sym.raw_tree is None:
            return
        for child in sym.raw_tree:
            if isinstance(child, list) and child and child[0] == "property":
                effects = _find(child, "effects")
                if effects and "hide" in effects and _find(effects, "hide") is None:
                    prop_name = str(child[1]) if len(child) > 1 else "?"
                    report.issues.append(Issue(
                        severity=Severity.WARNING,
                        category=IssueCategory.DEPRECATED,
                        entity=sym.name,
                        message=f"Property '{prop_name}' uses deprecated bare 'hide' atom instead of (hide yes)",
                        fixable=True,
                        fix_action="fix_bare_hide",
                        details={"property": prop_name},
                    ))

    @staticmethod
    def check_pin_electrical_types(sym: SymbolDef, report: AnalysisReport) -> None:
        """Validate pin electrical types."""
        for pin in sym.pins():
            if pin.electrical_type not in VALID_PIN_ELECTRICAL_TYPES:
                report.issues.append(Issue(
                    severity=Severity.ERROR,
                    category=IssueCategory.PIN,
                    entity=sym.name,
                    message=f"Pin '{pin.number}' has invalid electrical type '{pin.electrical_type}'",
                    fixable=True,
                    fix_action="fix_pin_type",
                    details={"pin": pin.number, "current": pin.electrical_type, "valid": list(VALID_PIN_ELECTRICAL_TYPES)},
                ))

    @staticmethod
    def check_pin_graphic_styles(sym: SymbolDef, report: AnalysisReport) -> None:
        """Validate pin graphic styles."""
        for pin in sym.pins():
            if pin.graphic_style not in VALID_PIN_GRAPHIC_STYLES:
                report.issues.append(Issue(
                    severity=Severity.ERROR,
                    category=IssueCategory.PIN,
                    entity=sym.name,
                    message=f"Pin '{pin.number}' has invalid graphic style '{pin.graphic_style}'",
                    fixable=True,
                    fix_action="fix_pin_style",
                    details={"pin": pin.number, "current": pin.graphic_style, "valid": list(VALID_PIN_GRAPHIC_STYLES)},
                ))

    @staticmethod
    def check_duplicate_pin_numbers(sym: SymbolDef, report: AnalysisReport) -> None:
        """Detect duplicate pin numbers within the same unit."""
        for unit in sym.units:
            numbers: Dict[str, int] = {}
            for pin in unit.pins:
                numbers[pin.number] = numbers.get(pin.number, 0) + 1
            for num, count in numbers.items():
                if count > 1:
                    report.issues.append(Issue(
                        severity=Severity.ERROR,
                        category=IssueCategory.PIN,
                        entity=sym.name,
                        message=f"Duplicate pin number '{num}' in unit {unit.unit_number} (x{count})",
                        fixable=False,
                        details={"pin": num, "unit": unit.unit_number, "count": count},
                    ))

    @staticmethod
    def check_pin_name_empty(sym: SymbolDef, report: AnalysisReport) -> None:
        """Warn about pins with no name (empty or whitespace)."""
        for pin in sym.pins():
            if not pin.name or pin.name.strip() in ("", "~"):
                continue  # Tilde is KiCad convention for "no name"
            # Pin names which are only whitespace are suspicious
            if not pin.name.strip():
                report.issues.append(Issue(
                    severity=Severity.WARNING,
                    category=IssueCategory.PIN,
                    entity=sym.name,
                    message=f"Pin '{pin.number}' has whitespace-only name",
                    fixable=True,
                    fix_action="fix_pin_name",
                    details={"pin": pin.number},
                ))

    @staticmethod
    def check_pin_zero_length(sym: SymbolDef, report: AnalysisReport) -> None:
        """Warn about pins with zero length."""
        for pin in sym.pins():
            if pin.length == 0.0:
                report.issues.append(Issue(
                    severity=Severity.INFO,
                    category=IssueCategory.PIN,
                    entity=sym.name,
                    message=f"Pin '{pin.number}' has zero length",
                    fixable=False,
                    details={"pin": pin.number},
                ))

    @staticmethod
    def check_unit_naming_convention(sym: SymbolDef, report: AnalysisReport) -> None:
        """Verify sub-symbol names follow the Name_N_M convention."""
        for unit in sym.units:
            if unit.raw_tree and len(unit.raw_tree) > 1:
                unit_name = str(unit.raw_tree[1])
                # Should be {parent_name}_{unit}_{style}
                prefix = sym.name + "_"
                if not unit_name.startswith(prefix):
                    report.issues.append(Issue(
                        severity=Severity.WARNING,
                        category=IssueCategory.UNIT,
                        entity=sym.name,
                        message=f"Unit name '{unit_name}' does not follow '{sym.name}_N_M' convention",
                        fixable=False,
                        details={"unit_name": unit_name, "expected_prefix": prefix},
                    ))

    @staticmethod
    def check_symbol_has_units(sym: SymbolDef, report: AnalysisReport) -> None:
        """Ensure at least one unit exists."""
        if not sym.units:
            report.issues.append(Issue(
                severity=Severity.WARNING,
                category=IssueCategory.UNIT,
                entity=sym.name,
                message="Symbol has no sub-units (at least _0_1 expected for graphics/pins)",
                fixable=False,
            ))

    @staticmethod
    def check_symbol_uuids(sym: SymbolDef, report: AnalysisReport) -> None:
        """Detect missing or invalid UUIDs on pins (KiCad 8+ requires them)."""
        if sym.raw_tree is None:
            return
        uuids_seen: Set[str] = set()
        for sub in _find_all(sym.raw_tree, "symbol"):
            for pin_tree in _find_all(sub, "pin"):
                uuid_node = _find(pin_tree, "uuid")
                if uuid_node is None or len(uuid_node) < 2:
                    # No uuid — older format; fixable
                    pin_num_node = _find(pin_tree, "number")
                    pin_num = str(pin_num_node[1]) if pin_num_node and len(pin_num_node) > 1 else "?"
                    report.issues.append(Issue(
                        severity=Severity.INFO,
                        category=IssueCategory.UUID,
                        entity=sym.name,
                        message=f"Pin '{pin_num}' is missing a UUID (older format)",
                        fixable=True,
                        fix_action="add_pin_uuid",
                        details={"pin": pin_num},
                    ))
                else:
                    uid = str(uuid_node[1])
                    if not UUID_RE.match(uid):
                        report.issues.append(Issue(
                            severity=Severity.ERROR,
                            category=IssueCategory.UUID,
                            entity=sym.name,
                            message=f"Pin UUID '{uid}' is malformed",
                            fixable=True,
                            fix_action="regenerate_uuid",
                            details={"uuid": uid},
                        ))
                    elif uid in uuids_seen:
                        report.issues.append(Issue(
                            severity=Severity.ERROR,
                            category=IssueCategory.UUID,
                            entity=sym.name,
                            message=f"Duplicate UUID '{uid}' on pin",
                            fixable=True,
                            fix_action="regenerate_uuid",
                            details={"uuid": uid},
                        ))
                    else:
                        uuids_seen.add(uid)

    @staticmethod
    def check_exclude_from_sim(sym: SymbolDef, report: AnalysisReport) -> None:
        """KiCad 8+ expects (exclude_from_sim yes/no) at symbol level."""
        if sym.raw_tree is None:
            return
        node = _find(sym.raw_tree, "exclude_from_sim")
        if node is None:
            report.issues.append(Issue(
                severity=Severity.INFO,
                category=IssueCategory.DEPRECATED,
                entity=sym.name,
                message="Missing (exclude_from_sim) field — KiCad 10 expects it explicitly",
                fixable=True,
                fix_action="add_exclude_from_sim",
            ))

    @staticmethod
    def check_in_bom_on_board(sym: SymbolDef, report: AnalysisReport) -> None:
        """Verify (in_bom) and (on_board) fields are present."""
        if sym.raw_tree is None:
            return
        for tag in ("in_bom", "on_board"):
            node = _find(sym.raw_tree, tag)
            if node is None:
                report.issues.append(Issue(
                    severity=Severity.WARNING,
                    category=IssueCategory.PROPERTY,
                    entity=sym.name,
                    message=f"Missing ({tag}) field",
                    fixable=True,
                    fix_action=f"add_{tag}",
                ))


# ═══════════════════════════════════════════════════════════════════════════
# Footprint analysis checks
# ═══════════════════════════════════════════════════════════════════════════


class _FootprintChecks:
    """Collection of check methods for footprints."""

    @staticmethod
    def check_version(fp: Footprint, report: AnalysisReport) -> None:
        """Check footprint version metadata."""
        if fp.version and fp.version < 20171130:
            report.issues.append(Issue(
                severity=Severity.ERROR,
                category=IssueCategory.VERSION,
                entity=fp.name,
                message=f"Footprint version {fp.version} is too old for KiCad 10",
                fixable=True,
                fix_action="upgrade_fp_version",
                details={"current": fp.version},
            ))

    @staticmethod
    def check_name(fp: Footprint, report: AnalysisReport) -> None:
        """Validate footprint name."""
        if not fp.name:
            report.issues.append(Issue(
                severity=Severity.ERROR,
                category=IssueCategory.NAMING,
                entity="<unnamed>",
                message="Footprint has an empty name",
                fixable=False,
            ))
            return
        forbidden = set('*?"<>|')
        bad = forbidden & set(fp.name)
        if bad:
            report.issues.append(Issue(
                severity=Severity.ERROR,
                category=IssueCategory.NAMING,
                entity=fp.name,
                message=f"Name contains forbidden characters: {bad}",
                fixable=True,
                fix_action="sanitize_fp_name",
                details={"forbidden_chars": list(bad)},
            ))

    @staticmethod
    def check_layer(fp: Footprint, report: AnalysisReport) -> None:
        """Primary layer should be F.Cu or B.Cu."""
        if fp.layer not in ("F.Cu", "B.Cu"):
            report.issues.append(Issue(
                severity=Severity.WARNING,
                category=IssueCategory.LAYER,
                entity=fp.name,
                message=f"Primary layer '{fp.layer}' is unusual (expected F.Cu or B.Cu)",
                fixable=False,
                details={"layer": fp.layer},
            ))

    @staticmethod
    def check_description(fp: Footprint, report: AnalysisReport) -> None:
        """Warn if description is missing."""
        if not fp.description:
            report.issues.append(Issue(
                severity=Severity.WARNING,
                category=IssueCategory.PROPERTY,
                entity=fp.name,
                message="Missing description (descr)",
                fixable=False,
            ))

    @staticmethod
    def check_tags(fp: Footprint, report: AnalysisReport) -> None:
        """Warn if tags are missing."""
        if not fp.tags:
            report.issues.append(Issue(
                severity=Severity.INFO,
                category=IssueCategory.PROPERTY,
                entity=fp.name,
                message="Missing tags",
                fixable=False,
            ))

    @staticmethod
    def check_duplicate_pads(fp: Footprint, report: AnalysisReport) -> None:
        """Detect duplicate pad numbers (except '' for non-numbered pads)."""
        numbers: Dict[str, int] = {}
        for pad in fp.pads:
            if pad.number:  # empty number = mounting pad / fiducial
                numbers[pad.number] = numbers.get(pad.number, 0) + 1
        for num, count in numbers.items():
            if count > 1:
                report.issues.append(Issue(
                    severity=Severity.WARNING,
                    category=IssueCategory.PAD,
                    entity=fp.name,
                    message=f"Duplicate pad number '{num}' (x{count})",
                    fixable=False,
                    details={"pad": num, "count": count},
                ))

    @staticmethod
    def check_pad_types(fp: Footprint, report: AnalysisReport) -> None:
        """Validate pad types and shapes."""
        for pad in fp.pads:
            if pad.type not in VALID_PAD_TYPES:
                report.issues.append(Issue(
                    severity=Severity.ERROR,
                    category=IssueCategory.PAD,
                    entity=fp.name,
                    message=f"Pad '{pad.number}' has invalid type '{pad.type}'",
                    fixable=True,
                    fix_action="fix_pad_type",
                    details={"pad": pad.number, "type": pad.type},
                ))
            if pad.shape not in VALID_PAD_SHAPES:
                report.issues.append(Issue(
                    severity=Severity.ERROR,
                    category=IssueCategory.PAD,
                    entity=fp.name,
                    message=f"Pad '{pad.number}' has invalid shape '{pad.shape}'",
                    fixable=True,
                    fix_action="fix_pad_shape",
                    details={"pad": pad.number, "shape": pad.shape},
                ))

    @staticmethod
    def check_pad_size(fp: Footprint, report: AnalysisReport) -> None:
        """Warn about zero-dimension pads."""
        for pad in fp.pads:
            w, h = pad.size
            if w <= 0 or h <= 0:
                report.issues.append(Issue(
                    severity=Severity.ERROR,
                    category=IssueCategory.PAD,
                    entity=fp.name,
                    message=f"Pad '{pad.number}' has non-positive size ({w}, {h})",
                    fixable=False,
                    details={"pad": pad.number, "size": pad.size},
                ))

    @staticmethod
    def check_pad_layers(fp: Footprint, report: AnalysisReport) -> None:
        """Validate pad layer assignments."""
        for pad in fp.pads:
            if not pad.layers:
                report.issues.append(Issue(
                    severity=Severity.ERROR,
                    category=IssueCategory.LAYER,
                    entity=fp.name,
                    message=f"Pad '{pad.number}' has no layer assignment",
                    fixable=True,
                    fix_action="fix_pad_layers",
                    details={"pad": pad.number, "type": pad.type},
                ))
            for layer in pad.layers:
                if layer not in VALID_LAYERS:
                    report.issues.append(Issue(
                        severity=Severity.WARNING,
                        category=IssueCategory.LAYER,
                        entity=fp.name,
                        message=f"Pad '{pad.number}' references unknown layer '{layer}'",
                        fixable=False,
                        details={"pad": pad.number, "layer": layer},
                    ))

    @staticmethod
    def check_thru_hole_drill(fp: Footprint, report: AnalysisReport) -> None:
        """Thru-hole pads must have a drill diameter > 0."""
        for pad in fp.pads:
            if pad.type == "thru_hole" and pad.drill <= 0:
                report.issues.append(Issue(
                    severity=Severity.ERROR,
                    category=IssueCategory.PAD,
                    entity=fp.name,
                    message=f"Through-hole pad '{pad.number}' has no drill (drill={pad.drill})",
                    fixable=False,
                    details={"pad": pad.number},
                ))

    @staticmethod
    def check_smd_no_drill(fp: Footprint, report: AnalysisReport) -> None:
        """SMD pads should not have a drill diameter."""
        for pad in fp.pads:
            if pad.type == "smd" and pad.drill > 0:
                report.issues.append(Issue(
                    severity=Severity.WARNING,
                    category=IssueCategory.PAD,
                    entity=fp.name,
                    message=f"SMD pad '{pad.number}' has unexpected drill diameter ({pad.drill})",
                    fixable=True,
                    fix_action="remove_smd_drill",
                    details={"pad": pad.number, "drill": pad.drill},
                ))

    @staticmethod
    def check_no_pads(fp: Footprint, report: AnalysisReport) -> None:
        """Warn if footprint has zero pads."""
        if not fp.pads:
            report.issues.append(Issue(
                severity=Severity.WARNING,
                category=IssueCategory.PAD,
                entity=fp.name,
                message="Footprint has no pads",
                fixable=False,
            ))

    @staticmethod
    def check_courtyard(fp: Footprint, report: AnalysisReport) -> None:
        """Check for courtyard graphics."""
        has_front_crtyd = False
        has_back_crtyd = False
        for g in fp.graphics:
            if g.raw_tree:
                layer_node = _find(g.raw_tree, "layer")
                if layer_node and len(layer_node) > 1:
                    ly = str(layer_node[1])
                    if "F.CrtYd" == ly:
                        has_front_crtyd = True
                    elif "B.CrtYd" == ly:
                        has_back_crtyd = True
        is_front = fp.layer == "F.Cu"
        expected = "F.CrtYd" if is_front else "B.CrtYd"
        has_expected = has_front_crtyd if is_front else has_back_crtyd
        if not has_expected and fp.pads:
            report.issues.append(Issue(
                severity=Severity.WARNING,
                category=IssueCategory.COURTYARD,
                entity=fp.name,
                message=f"Missing {expected} courtyard outline",
                fixable=False,
            ))

    @staticmethod
    def check_fab_layer(fp: Footprint, report: AnalysisReport) -> None:
        """Check for fabrication layer outline."""
        has_fab = False
        for g in fp.graphics:
            if g.raw_tree:
                layer_node = _find(g.raw_tree, "layer")
                if layer_node and len(layer_node) > 1:
                    ly = str(layer_node[1])
                    if ly in ("F.Fab", "B.Fab"):
                        has_fab = True
                        break
        if not has_fab and fp.pads:
            report.issues.append(Issue(
                severity=Severity.INFO,
                category=IssueCategory.GRAPHIC,
                entity=fp.name,
                message="No fabrication layer (F.Fab / B.Fab) graphics found",
                fixable=False,
            ))

    @staticmethod
    def check_3d_model(fp: Footprint, report: AnalysisReport) -> None:
        """Inform if 3D model is missing."""
        if not fp.models:
            report.issues.append(Issue(
                severity=Severity.INFO,
                category=IssueCategory.MODEL,
                entity=fp.name,
                message="No 3D model assigned",
                fixable=False,
            ))

    @staticmethod
    def check_pad_uuids(fp: Footprint, report: AnalysisReport) -> None:
        """Verify all pads have unique UUIDs (KiCad 8+ requirement)."""
        uuids_seen: Set[str] = set()
        for pad in fp.pads:
            if pad.raw_tree:
                uuid_node = _find(pad.raw_tree, "uuid")
                if uuid_node is None or len(uuid_node) < 2:
                    report.issues.append(Issue(
                        severity=Severity.INFO,
                        category=IssueCategory.UUID,
                        entity=fp.name,
                        message=f"Pad '{pad.number}' is missing a UUID",
                        fixable=True,
                        fix_action="add_pad_uuid",
                        details={"pad": pad.number},
                    ))
                else:
                    uid = str(uuid_node[1])
                    if not UUID_RE.match(uid):
                        report.issues.append(Issue(
                            severity=Severity.ERROR,
                            category=IssueCategory.UUID,
                            entity=fp.name,
                            message=f"Pad '{pad.number}' has malformed UUID '{uid}'",
                            fixable=True,
                            fix_action="regenerate_pad_uuid",
                        ))
                    elif uid in uuids_seen:
                        report.issues.append(Issue(
                            severity=Severity.ERROR,
                            category=IssueCategory.UUID,
                            entity=fp.name,
                            message=f"Pad '{pad.number}' has duplicate UUID '{uid}'",
                            fixable=True,
                            fix_action="regenerate_pad_uuid",
                        ))
                    else:
                        uuids_seen.add(uid)

    @staticmethod
    def check_overlapping_pads(fp: Footprint, report: AnalysisReport) -> None:
        """Detect pads that occupy the exact same position with same size."""
        for i, a in enumerate(fp.pads):
            for b in fp.pads[i + 1:]:
                if (abs(a.position.x - b.position.x) < 0.001
                        and abs(a.position.y - b.position.y) < 0.001
                        and a.number != b.number):
                    report.issues.append(Issue(
                        severity=Severity.WARNING,
                        category=IssueCategory.PAD,
                        entity=fp.name,
                        message=f"Pads '{a.number}' and '{b.number}' overlap at ({a.position.x}, {a.position.y})",
                        fixable=False,
                        details={"pad_a": a.number, "pad_b": b.number},
                    ))

    @staticmethod
    def check_reference_text(fp: Footprint, report: AnalysisReport) -> None:
        """Ensure reference and value fp_text elements exist."""
        has_ref = False
        has_val = False
        for g in fp.graphics:
            if g.tag == "fp_text" and g.raw_tree and len(g.raw_tree) > 1:
                kind = str(g.raw_tree[1])
                if kind == "reference":
                    has_ref = True
                elif kind == "value":
                    has_val = True
        # Also check properties for KiCad 8+ format
        for p in fp.properties:
            if p.key == "Reference":
                has_ref = True
            elif p.key == "Value":
                has_val = True
        if not has_ref:
            report.issues.append(Issue(
                severity=Severity.WARNING,
                category=IssueCategory.GRAPHIC,
                entity=fp.name,
                message="Missing reference designator text (fp_text reference / property Reference)",
                fixable=False,
            ))
        if not has_val:
            report.issues.append(Issue(
                severity=Severity.WARNING,
                category=IssueCategory.GRAPHIC,
                entity=fp.name,
                message="Missing value text (fp_text value / property Value)",
                fixable=False,
            ))

    @staticmethod
    def check_attributes(fp: Footprint, report: AnalysisReport) -> None:
        """Validate footprint attributes against pad types."""
        is_smd = "smd" in fp.attributes
        is_thru = "through_hole" in fp.attributes
        has_smd_pads = any(p.type == "smd" for p in fp.pads)
        has_thru_pads = any(p.type == "thru_hole" for p in fp.pads)

        if has_smd_pads and not has_thru_pads and not is_smd:
            report.issues.append(Issue(
                severity=Severity.WARNING,
                category=IssueCategory.PROPERTY,
                entity=fp.name,
                message="Footprint has SMD pads but 'smd' attribute is not set",
                fixable=True,
                fix_action="set_smd_attr",
            ))
        if has_thru_pads and not has_smd_pads and not is_thru:
            report.issues.append(Issue(
                severity=Severity.INFO,
                category=IssueCategory.PROPERTY,
                entity=fp.name,
                message="Footprint has thru-hole pads but 'through_hole' attribute is not set",
                fixable=True,
                fix_action="set_thru_attr",
            ))


# ═══════════════════════════════════════════════════════════════════════════
# Fix implementations
# ═══════════════════════════════════════════════════════════════════════════


class _SymbolFixer:
    """Apply fixes to a symbol library based on detected issues."""

    @staticmethod
    def fix_version(lib: SymbolLibrary, target: int = KICAD10_SYMBOL_LIB_VERSION) -> None:
        """Upgrade the library version to *target*."""
        lib.version = target

    @staticmethod
    def fix_generator(lib: SymbolLibrary) -> None:
        """Set generator to kicad_symbol_editor if missing."""
        if not lib.generator:
            lib.generator = "kicad_symbol_editor"

    @staticmethod
    def add_missing_properties(sym: SymbolDef) -> int:
        """Add any missing required properties with safe defaults. Returns count added."""
        existing = {p.key for p in sym.properties}
        defaults = {
            "Reference": "U",
            "Value": sym.name,
            "Footprint": "",
            "Datasheet": "~",
        }
        count = 0
        for key in SYMBOL_REQUIRED_PROPERTIES:
            if key not in existing:
                sym.properties.append(Property(
                    key=key,
                    value=defaults.get(key, ""),
                    position=Position(0, 0),
                    effects=Effects(hide=(key in ("Footprint", "Datasheet"))),
                ))
                sym.raw_tree = None
                count += 1
        return count

    @staticmethod
    def sanitize_name(sym: SymbolDef) -> bool:
        """Remove forbidden characters from the symbol name."""
        forbidden = set(':*?"<>|')
        new_name = "".join(c for c in sym.name if c not in forbidden)
        if new_name != sym.name:
            sym.name = new_name
            sym.raw_tree = None
            return True
        return False

    @staticmethod
    def fix_pin_type(pin: Pin) -> None:
        """Reset invalid pin type to 'unspecified'."""
        if pin.electrical_type not in VALID_PIN_ELECTRICAL_TYPES:
            pin.electrical_type = "unspecified"

    @staticmethod
    def fix_pin_style(pin: Pin) -> None:
        """Reset invalid pin style to 'line'."""
        if pin.graphic_style not in VALID_PIN_GRAPHIC_STYLES:
            pin.graphic_style = "line"

    @staticmethod
    def deduplicate_properties(sym: SymbolDef) -> int:
        """Remove duplicate property keys, keeping the first occurrence. Returns count removed."""
        seen: Set[str] = set()
        deduped: List[Property] = []
        removed = 0
        for p in sym.properties:
            if p.key in seen:
                removed += 1
            else:
                seen.add(p.key)
                deduped.append(p)
        if removed:
            sym.properties = deduped
            sym.raw_tree = None
        return removed

    @staticmethod
    def fix_deprecated_property_ids(sym: SymbolDef) -> int:
        """Remove (id N) nodes from properties in raw_tree. Returns count fixed."""
        if sym.raw_tree is None:
            return 0
        count = 0
        for i, child in enumerate(sym.raw_tree):
            if isinstance(child, list) and child and child[0] == "property":
                new_child = [item for item in child if not (isinstance(item, list) and item and item[0] == "id")]
                if len(new_child) != len(child):
                    sym.raw_tree[i] = new_child
                    count += 1
        return count

    @staticmethod
    def fix_bare_hide(sym: SymbolDef) -> int:
        """Replace bare 'hide' atoms in effects with (hide yes). Returns count fixed."""
        if sym.raw_tree is None:
            return 0
        count = 0
        for child in sym.raw_tree:
            if isinstance(child, list) and child and child[0] == "property":
                effects = _find(child, "effects")
                if effects and "hide" in effects and _find(effects, "hide") is None:
                    # Replace bare 'hide' with (hide yes)
                    idx = effects.index("hide")
                    effects[idx] = ["hide", "yes"]
                    count += 1
        return count

    @staticmethod
    def add_exclude_from_sim(sym: SymbolDef) -> bool:
        """Add (exclude_from_sim no) to the raw tree."""
        if sym.raw_tree is None:
            return False
        if _find(sym.raw_tree, "exclude_from_sim") is not None:
            return False
        # Insert after pin_names or pin_numbers
        insert_idx = 2  # After tag and name
        for i, item in enumerate(sym.raw_tree[2:], start=2):
            if isinstance(item, list) and item and item[0] in ("pin_names", "pin_numbers"):
                insert_idx = i + 1
        sym.raw_tree.insert(insert_idx, ["exclude_from_sim", "no"])
        return True

    @staticmethod
    def add_in_bom(sym: SymbolDef) -> bool:
        """Add (in_bom yes) if missing."""
        if sym.raw_tree is None:
            return False
        if _find(sym.raw_tree, "in_bom") is not None:
            return False
        insert_idx = 2
        for i, item in enumerate(sym.raw_tree[2:], start=2):
            if isinstance(item, list) and item and item[0] in ("pin_names", "pin_numbers", "exclude_from_sim"):
                insert_idx = i + 1
        sym.raw_tree.insert(insert_idx, ["in_bom", "yes"])
        return True

    @staticmethod
    def add_on_board(sym: SymbolDef) -> bool:
        """Add (on_board yes) if missing."""
        if sym.raw_tree is None:
            return False
        if _find(sym.raw_tree, "on_board") is not None:
            return False
        insert_idx = 2
        for i, item in enumerate(sym.raw_tree[2:], start=2):
            if isinstance(item, list) and item and item[0] in ("pin_names", "pin_numbers", "exclude_from_sim", "in_bom"):
                insert_idx = i + 1
        sym.raw_tree.insert(insert_idx, ["on_board", "yes"])
        return True

    @staticmethod
    def add_pin_uuids(sym: SymbolDef) -> int:
        """Add UUIDs to pins that lack them. Returns count added."""
        if sym.raw_tree is None:
            return 0
        count = 0
        for sub in _find_all(sym.raw_tree, "symbol"):
            for pin_tree in _find_all(sub, "pin"):
                uuid_node = _find(pin_tree, "uuid")
                if uuid_node is None:
                    pin_tree.append(["uuid", QStr(str(_uuid_mod.uuid4()))])
                    count += 1
        return count

    @staticmethod
    def regenerate_duplicate_uuids(sym: SymbolDef) -> int:
        """Re-generate duplicate or malformed UUIDs. Returns count regenerated."""
        if sym.raw_tree is None:
            return 0
        seen: Set[str] = set()
        count = 0
        for sub in _find_all(sym.raw_tree, "symbol"):
            for pin_tree in _find_all(sub, "pin"):
                uuid_node = _find(pin_tree, "uuid")
                if uuid_node and len(uuid_node) >= 2:
                    uid = str(uuid_node[1])
                    if not UUID_RE.match(uid) or uid in seen:
                        uuid_node[1] = QStr(str(_uuid_mod.uuid4()))
                        count += 1
                    else:
                        seen.add(uid)
        return count

    @staticmethod
    def set_default_reference(sym: SymbolDef) -> bool:
        """Set empty Reference to 'U'."""
        for p in sym.properties:
            if p.key == "Reference" and not p.value.strip():
                p.value = "U"
                sym.raw_tree = None
                return True
        return False


class _FootprintFixer:
    """Apply fixes to a footprint based on detected issues."""

    @staticmethod
    def sanitize_name(fp: Footprint) -> bool:
        """Remove forbidden chars from footprint name."""
        forbidden = set('*?"<>|')
        new_name = "".join(c for c in fp.name if c not in forbidden)
        if new_name != fp.name:
            fp.name = new_name
            return True
        return False

    @staticmethod
    def remove_smd_drill(fp: Footprint) -> int:
        """Remove drill from SMD pads. Returns count fixed."""
        count = 0
        for pad in fp.pads:
            if pad.type == "smd" and pad.drill > 0:
                pad.drill = 0.0
                pad.raw_tree = None
                count += 1
        return count

    @staticmethod
    def fix_pad_layers(fp: Footprint) -> int:
        """Add default layers to pads with no layer assignment. Returns count fixed."""
        count = 0
        for pad in fp.pads:
            if not pad.layers:
                if pad.type in ("thru_hole", "np_thru_hole"):
                    pad.layers = ["*.Cu", "*.Mask"]
                else:
                    pad.layers = ["F.Cu", "F.Paste", "F.Mask"]
                pad.raw_tree = None
                count += 1
        return count

    @staticmethod
    def set_smd_attribute(fp: Footprint) -> bool:
        """Add 'smd' to attributes if missing."""
        if "smd" not in fp.attributes:
            fp.attributes.append("smd")
            return True
        return False

    @staticmethod
    def set_thru_attribute(fp: Footprint) -> bool:
        """Add 'through_hole' to attributes if missing."""
        if "through_hole" not in fp.attributes:
            fp.attributes.append("through_hole")
            return True
        return False

    @staticmethod
    def add_pad_uuids(fp: Footprint) -> int:
        """Add UUIDs to pads that lack them. Returns count added."""
        count = 0
        for pad in fp.pads:
            if pad.raw_tree:
                uuid_node = _find(pad.raw_tree, "uuid")
                if uuid_node is None:
                    pad.raw_tree.append(["uuid", QStr(str(_uuid_mod.uuid4()))])
                    count += 1
        return count

    @staticmethod
    def regenerate_pad_uuids(fp: Footprint) -> int:
        """Fix duplicate / malformed pad UUIDs. Returns count regenerated."""
        seen: Set[str] = set()
        count = 0
        for pad in fp.pads:
            if pad.raw_tree:
                uuid_node = _find(pad.raw_tree, "uuid")
                if uuid_node and len(uuid_node) >= 2:
                    uid = str(uuid_node[1])
                    if not UUID_RE.match(uid) or uid in seen:
                        uuid_node[1] = QStr(str(_uuid_mod.uuid4()))
                        count += 1
                    else:
                        seen.add(uid)
        return count


# ═══════════════════════════════════════════════════════════════════════════
# Main analyzer class
# ═══════════════════════════════════════════════════════════════════════════


class LibraryAnalyzer:
    """Comprehensive analyzer for KiCad symbol and footprint libraries.

    Usage::

        analyzer = LibraryAnalyzer()

        # Analyze symbol library
        report = analyzer.analyze_symbol_library("mylib.kicad_sym")
        print(report.summary())

        # Fix all fixable issues
        n = analyzer.fix_symbol_library("mylib.kicad_sym", "mylib_fixed.kicad_sym")

        # Analyze footprint
        report = analyzer.analyze_footprint("myfp.kicad_mod")
        print(report.summary())

        # Analyze a directory of footprints
        reports = analyzer.analyze_footprint_directory("MyLib.pretty/")

        # Dry-run mode (report what would be fixed without writing):
        n = analyzer.fix_symbol_library("mylib.kicad_sym", dry_run=True)
    """

    # ── Symbol library analysis ────────────────────────────────────────

    def analyze_symbol_library(self, path: str | os.PathLike) -> AnalysisReport:
        """Run all checks on a symbol library file.

        Performs raw-text structural analysis first (string-aware paren
        depth tracking) then, if parseable, runs semantic checks.

        Args:
            path: Path to a ``.kicad_sym`` file.

        Returns:
            An :class:`AnalysisReport` with all detected issues.
        """
        report = AnalysisReport(file_path=str(path), file_type="symbol_library")

        # --- Pre-parse: raw-text structural analysis ---
        try:
            raw_text = Path(path).read_text(encoding="utf-8", errors="replace")
        except FileNotFoundError:
            report.issues.append(Issue(
                Severity.ERROR, IssueCategory.STRUCTURE, str(path),
                "File not found",
            ))
            return report
        except OSError as exc:
            report.issues.append(Issue(
                Severity.ERROR, IssueCategory.STRUCTURE, str(path),
                f"Cannot read file: {exc}",
            ))
            return report

        # Skip empty files
        if not raw_text.strip():
            report.issues.append(Issue(
                Severity.ERROR, IssueCategory.STRUCTURE, str(path),
                "File is empty",
            ))
            return report

        # String-aware structural checks on raw text
        _SymbolChecks.check_raw_structure(raw_text, report)

        # .bak diff check
        _SymbolChecks.check_bak_diff(str(path), report)

        # --- S-expression parse ---
        try:
            lib = SymbolLibrary.load(path)
        except FileNotFoundError:
            report.issues.append(Issue(
                Severity.ERROR, IssueCategory.STRUCTURE, str(path),
                "File not found",
            ))
            return report
        except (ValueError, Exception) as exc:
            report.issues.append(Issue(
                Severity.ERROR, IssueCategory.STRUCTURE, str(path),
                f"Parse error: {exc}",
            ))
            return report

        # Library-level checks
        _SymbolChecks.check_version(lib, report)
        _SymbolChecks.check_generator(lib, report)
        _SymbolChecks.check_duplicate_symbols(lib, report)

        # Per-symbol checks
        for sym in lib.symbols:
            _SymbolChecks.check_symbol_name(sym, report)
            _SymbolChecks.check_required_properties(sym, report)
            _SymbolChecks.check_property_values(sym, report)
            _SymbolChecks.check_deprecated_property_ids(sym, report)
            _SymbolChecks.check_deprecated_bare_hide(sym, report)
            _SymbolChecks.check_pin_electrical_types(sym, report)
            _SymbolChecks.check_pin_graphic_styles(sym, report)
            _SymbolChecks.check_duplicate_pin_numbers(sym, report)
            _SymbolChecks.check_pin_name_empty(sym, report)
            _SymbolChecks.check_pin_zero_length(sym, report)
            _SymbolChecks.check_unit_naming_convention(sym, report)
            _SymbolChecks.check_symbol_has_units(sym, report)
            _SymbolChecks.check_symbol_uuids(sym, report)
            _SymbolChecks.check_exclude_from_sim(sym, report)
            _SymbolChecks.check_in_bom_on_board(sym, report)

        return report

    def analyze_symbol_library_text(self, text: str, name: str = "<in-memory>") -> AnalysisReport:
        """Analyze a symbol library from a string instead of a file.

        Performs string-aware structural analysis before parsing.

        Args:
            text: Raw ``.kicad_sym`` file content.
            name: Descriptive name for the report.

        Returns:
            An :class:`AnalysisReport`.
        """
        report = AnalysisReport(file_path=name, file_type="symbol_library")

        # Raw-text structural check
        _SymbolChecks.check_raw_structure(text, report)

        try:
            tree = parse(text)
            lib = SymbolLibrary._from_tree(tree)
        except Exception as exc:
            report.issues.append(Issue(
                Severity.ERROR, IssueCategory.STRUCTURE, name,
                f"Parse error: {exc}",
            ))
            return report

        _SymbolChecks.check_version(lib, report)
        _SymbolChecks.check_generator(lib, report)
        _SymbolChecks.check_duplicate_symbols(lib, report)

        for sym in lib.symbols:
            _SymbolChecks.check_symbol_name(sym, report)
            _SymbolChecks.check_required_properties(sym, report)
            _SymbolChecks.check_property_values(sym, report)
            _SymbolChecks.check_deprecated_property_ids(sym, report)
            _SymbolChecks.check_deprecated_bare_hide(sym, report)
            _SymbolChecks.check_pin_electrical_types(sym, report)
            _SymbolChecks.check_pin_graphic_styles(sym, report)
            _SymbolChecks.check_duplicate_pin_numbers(sym, report)
            _SymbolChecks.check_pin_name_empty(sym, report)
            _SymbolChecks.check_pin_zero_length(sym, report)
            _SymbolChecks.check_unit_naming_convention(sym, report)
            _SymbolChecks.check_symbol_has_units(sym, report)
            _SymbolChecks.check_symbol_uuids(sym, report)
            _SymbolChecks.check_exclude_from_sim(sym, report)
            _SymbolChecks.check_in_bom_on_board(sym, report)

        return report

    # ── Symbol library fixing ──────────────────────────────────────────

    def fix_symbol_library(
        self,
        input_path: str | os.PathLike,
        output_path: Optional[str | os.PathLike] = None,
        *,
        dry_run: bool = False,
    ) -> int:
        """Analyze and auto-fix a symbol library, saving the result.

        Performs two-phase fixing:
        1. **Raw-text phase** — string-aware iterative stray-paren removal
           (before S-expression parsing) so that structurally broken files
           can be repaired.
        2. **Semantic phase** — standard S-expression-level fixes (versions,
           properties, UUIDs, etc.).

        Post-fix validation ensures final depth == 0 and min depth >= 0.
        If validation fails the file is **not** written and a
        :class:`StructuralValidationError` is raised (tip #5).

        Args:
            input_path:  Source ``.kicad_sym`` file.
            output_path: Destination path (defaults to overwriting input).
            dry_run:     If True, compute fixes but do not write any files.

        Returns:
            Number of issues fixed.
        """
        if output_path is None:
            output_path = input_path

        # Phase 1: raw-text structural repair
        raw_text = Path(input_path).read_text(encoding="utf-8", errors="replace")
        raw_fixed, raw_fix_count = _RawTextAnalysis.iterative_fix_stray_parens(raw_text)

        # If raw-text fixes were applied, re-read from the fixed text
        if raw_fix_count > 0:
            # Validate the raw-fixed text before proceeding
            valid, msg = _RawTextAnalysis.validate_structure(raw_fixed)
            if not valid:
                raise StructuralValidationError(
                    f"Raw-text repair did not produce a valid structure: {msg}"
                )
            lib = SymbolLibrary._from_tree(parse(raw_fixed))
        else:
            lib = SymbolLibrary.load(input_path)

        # Phase 2: semantic fixes
        semantic_fixed = self._apply_symbol_fixes(lib)
        total_fixed = raw_fix_count + semantic_fixed

        if not dry_run:
            # Serialize and validate output before writing
            output_text = serialize(lib._to_tree())
            valid, msg = _RawTextAnalysis.validate_structure(output_text)
            if not valid:
                raise StructuralValidationError(
                    f"Post-fix validation failed — refusing to save: {msg}"
                )
            Path(output_path).write_text(output_text, encoding="utf-8")

        return total_fixed

    def fix_symbol_library_object(self, lib: SymbolLibrary) -> int:
        """Fix an in-memory SymbolLibrary object. Returns count of fixes applied."""
        return self._apply_symbol_fixes(lib)

    def _apply_symbol_fixes(self, lib: SymbolLibrary) -> int:
        """Run all fixers on a library. Returns total fixes."""
        fixed = 0

        # Library-level fixes
        if lib.version < KICAD10_SYMBOL_LIB_VERSION:
            _SymbolFixer.fix_version(lib)
            fixed += 1

        if not lib.generator:
            _SymbolFixer.fix_generator(lib)
            fixed += 1

        # Per-symbol fixes
        for sym in lib.symbols:
            fixed += _SymbolFixer.add_missing_properties(sym)
            if _SymbolFixer.sanitize_name(sym):
                fixed += 1
            fixed += _SymbolFixer.deduplicate_properties(sym)
            fixed += _SymbolFixer.fix_deprecated_property_ids(sym)
            fixed += _SymbolFixer.fix_bare_hide(sym)
            if _SymbolFixer.set_default_reference(sym):
                fixed += 1

            # Pin fixes
            for unit in sym.units:
                for pin in unit.pins:
                    if pin.electrical_type not in VALID_PIN_ELECTRICAL_TYPES:
                        _SymbolFixer.fix_pin_type(pin)
                        unit.raw_tree = None
                        fixed += 1
                    if pin.graphic_style not in VALID_PIN_GRAPHIC_STYLES:
                        _SymbolFixer.fix_pin_style(pin)
                        unit.raw_tree = None
                        fixed += 1

            # UUID fixes
            fixed += _SymbolFixer.add_pin_uuids(sym)
            fixed += _SymbolFixer.regenerate_duplicate_uuids(sym)

            # Metadata fields
            if _SymbolFixer.add_exclude_from_sim(sym):
                fixed += 1
            if _SymbolFixer.add_in_bom(sym):
                fixed += 1
            if _SymbolFixer.add_on_board(sym):
                fixed += 1

        return fixed

    # ── Footprint analysis ─────────────────────────────────────────────

    def analyze_footprint(self, path: str | os.PathLike) -> AnalysisReport:
        """Run all checks on a single footprint file.

        Args:
            path: Path to a ``.kicad_mod`` file.

        Returns:
            An :class:`AnalysisReport`.
        """
        report = AnalysisReport(file_path=str(path), file_type="footprint")
        try:
            fp = Footprint.load(path)
        except FileNotFoundError:
            report.issues.append(Issue(
                Severity.ERROR, IssueCategory.STRUCTURE, str(path),
                "File not found",
            ))
            return report
        except (ValueError, Exception) as exc:
            report.issues.append(Issue(
                Severity.ERROR, IssueCategory.STRUCTURE, str(path),
                f"Parse error: {exc}",
            ))
            return report

        self._run_footprint_checks(fp, report)
        return report

    def analyze_footprint_text(self, text: str, name: str = "<in-memory>") -> AnalysisReport:
        """Analyze a footprint from a string.

        Args:
            text: Raw ``.kicad_mod`` file content.
            name: Descriptive name for the report.

        Returns:
            An :class:`AnalysisReport`.
        """
        report = AnalysisReport(file_path=name, file_type="footprint")
        try:
            tree = parse(text)
            fp = Footprint._from_tree(tree)
        except Exception as exc:
            report.issues.append(Issue(
                Severity.ERROR, IssueCategory.STRUCTURE, name,
                f"Parse error: {exc}",
            ))
            return report

        self._run_footprint_checks(fp, report)
        return report

    def analyze_footprint_directory(self, directory: str | os.PathLike) -> List[AnalysisReport]:
        """Analyze all ``.kicad_mod`` files in a directory (e.g. a ``.pretty`` folder).

        Args:
            directory: Path to a directory containing footprint files.

        Returns:
            A list of :class:`AnalysisReport`, one per file.
        """
        reports: List[AnalysisReport] = []
        dirpath = Path(directory)
        if not dirpath.is_dir():
            r = AnalysisReport(file_path=str(directory), file_type="footprint_directory")
            r.issues.append(Issue(
                Severity.ERROR, IssueCategory.STRUCTURE, str(directory),
                "Not a directory",
            ))
            return [r]

        for fp_file in sorted(dirpath.glob("*.kicad_mod")):
            reports.append(self.analyze_footprint(fp_file))
        return reports

    def _run_footprint_checks(self, fp: Footprint, report: AnalysisReport) -> None:
        """Execute all footprint checks."""
        _FootprintChecks.check_version(fp, report)
        _FootprintChecks.check_name(fp, report)
        _FootprintChecks.check_layer(fp, report)
        _FootprintChecks.check_description(fp, report)
        _FootprintChecks.check_tags(fp, report)
        _FootprintChecks.check_duplicate_pads(fp, report)
        _FootprintChecks.check_pad_types(fp, report)
        _FootprintChecks.check_pad_size(fp, report)
        _FootprintChecks.check_pad_layers(fp, report)
        _FootprintChecks.check_thru_hole_drill(fp, report)
        _FootprintChecks.check_smd_no_drill(fp, report)
        _FootprintChecks.check_no_pads(fp, report)
        _FootprintChecks.check_courtyard(fp, report)
        _FootprintChecks.check_fab_layer(fp, report)
        _FootprintChecks.check_3d_model(fp, report)
        _FootprintChecks.check_pad_uuids(fp, report)
        _FootprintChecks.check_overlapping_pads(fp, report)
        _FootprintChecks.check_reference_text(fp, report)
        _FootprintChecks.check_attributes(fp, report)

    # ── Footprint fixing ───────────────────────────────────────────────

    def fix_footprint(
        self,
        input_path: str | os.PathLike,
        output_path: Optional[str | os.PathLike] = None,
    ) -> int:
        """Analyze and auto-fix a footprint file.

        Args:
            input_path:  Source ``.kicad_mod`` file.
            output_path: Destination path (defaults to overwriting input).

        Returns:
            Number of issues fixed.
        """
        if output_path is None:
            output_path = input_path

        fp = Footprint.load(input_path)
        fixed = self._apply_footprint_fixes(fp)
        fp.save(output_path)
        return fixed

    def fix_footprint_object(self, fp: Footprint) -> int:
        """Fix an in-memory Footprint object. Returns count of fixes applied."""
        return self._apply_footprint_fixes(fp)

    def fix_footprint_directory(
        self,
        directory: str | os.PathLike,
        output_directory: Optional[str | os.PathLike] = None,
    ) -> Dict[str, int]:
        """Fix all footprints in a directory.

        Args:
            directory:        Source ``.pretty`` directory.
            output_directory: Destination directory (defaults to in-place).

        Returns:
            Dict mapping filename to number of fixes applied.
        """
        dirpath = Path(directory)
        outdir = Path(output_directory) if output_directory else dirpath
        outdir.mkdir(parents=True, exist_ok=True)

        results: Dict[str, int] = {}
        for fp_file in sorted(dirpath.glob("*.kicad_mod")):
            out_file = outdir / fp_file.name
            results[fp_file.name] = self.fix_footprint(fp_file, out_file)
        return results

    def _apply_footprint_fixes(self, fp: Footprint) -> int:
        """Run all fixers on a footprint. Returns total fixes."""
        fixed = 0

        if _FootprintFixer.sanitize_name(fp):
            fixed += 1

        fixed += _FootprintFixer.remove_smd_drill(fp)
        fixed += _FootprintFixer.fix_pad_layers(fp)
        fixed += _FootprintFixer.add_pad_uuids(fp)
        fixed += _FootprintFixer.regenerate_pad_uuids(fp)

        # Attribute consistency
        has_smd = any(p.type == "smd" for p in fp.pads)
        has_thru = any(p.type == "thru_hole" for p in fp.pads)
        if has_smd and not has_thru and "smd" not in fp.attributes:
            if _FootprintFixer.set_smd_attribute(fp):
                fixed += 1
        if has_thru and not has_smd and "through_hole" not in fp.attributes:
            if _FootprintFixer.set_thru_attribute(fp):
                fixed += 1

        return fixed

    # ── Combined convenience helpers ───────────────────────────────────

    def analyze_file(self, path: str | os.PathLike) -> AnalysisReport:
        """Auto-detect file type and analyze.

        Args:
            path: Path to any supported KiCad library file.

        Returns:
            An :class:`AnalysisReport`.
        """
        p = Path(path)
        if p.suffix == ".kicad_sym":
            return self.analyze_symbol_library(path)
        elif p.suffix == ".kicad_mod":
            return self.analyze_footprint(path)
        else:
            report = AnalysisReport(file_path=str(path), file_type="unknown")
            report.issues.append(Issue(
                Severity.ERROR, IssueCategory.STRUCTURE, str(path),
                f"Unsupported file type: {p.suffix}",
            ))
            return report

    def fix_file(
        self,
        input_path: str | os.PathLike,
        output_path: Optional[str | os.PathLike] = None,
    ) -> int:
        """Auto-detect file type, fix, and save.

        Args:
            input_path:  Source file.
            output_path: Destination file (defaults to overwriting input).

        Returns:
            Number of issues fixed.
        """
        p = Path(input_path)
        if p.suffix == ".kicad_sym":
            return self.fix_symbol_library(input_path, output_path)
        elif p.suffix == ".kicad_mod":
            return self.fix_footprint(input_path, output_path)
        else:
            raise ValueError(f"Unsupported file type: {p.suffix}")


# ═══════════════════════════════════════════════════════════════════════════
# CLI entry point
# ═══════════════════════════════════════════════════════════════════════════


def main(argv: Optional[List[str]] = None) -> int:
    """Command-line interface for the library analyzer.

    Usage::

        python -m kiassist_utils.kicad_parser.analyzer [--fix] [--output DIR] FILES...

    Returns:
        0 if no errors found, 1 otherwise.
    """
    import argparse
    import json
    import sys

    parser = argparse.ArgumentParser(
        prog="kicad-library-analyzer",
        description="Analyze and fix KiCad symbol/footprint libraries for KiCad 10 compatibility.",
    )
    parser.add_argument("files", nargs="+", help="Paths to .kicad_sym or .kicad_mod files, or .pretty directories")
    parser.add_argument("--fix", action="store_true", help="Apply auto-fixes to all fixable issues")
    parser.add_argument("--output", "-o", default=None, help="Output directory for fixed files (default: in-place)")
    parser.add_argument("--json", action="store_true", help="Output report as JSON")
    parser.add_argument("--quiet", "-q", action="store_true", help="Only show errors")

    args = parser.parse_args(argv)
    analyzer = LibraryAnalyzer()
    all_reports: List[AnalysisReport] = []
    total_fixed = 0
    has_errors = False

    for file_path in args.files:
        p = Path(file_path)

        if p.is_dir():
            # Treat as footprint directory
            if args.fix:
                results = analyzer.fix_footprint_directory(p, args.output)
                total_fixed += sum(results.values())
            reports = analyzer.analyze_footprint_directory(p)
            all_reports.extend(reports)
        elif p.suffix == ".kicad_sym":
            if args.fix:
                out = Path(args.output) / p.name if args.output else None
                if args.output:
                    Path(args.output).mkdir(parents=True, exist_ok=True)
                total_fixed += analyzer.fix_symbol_library(p, out)
            all_reports.append(analyzer.analyze_symbol_library(p))
        elif p.suffix == ".kicad_mod":
            if args.fix:
                out = Path(args.output) / p.name if args.output else None
                if args.output:
                    Path(args.output).mkdir(parents=True, exist_ok=True)
                total_fixed += analyzer.fix_footprint(p, out)
            all_reports.append(analyzer.analyze_footprint(p))
        else:
            r = AnalysisReport(file_path=str(p), file_type="unknown")
            r.issues.append(Issue(Severity.ERROR, IssueCategory.STRUCTURE, str(p), f"Unsupported: {p.suffix}"))
            all_reports.append(r)

    # Output
    if args.json:
        data = [r.to_dict() for r in all_reports]
        if args.fix:
            print(json.dumps({"total_fixed": total_fixed, "reports": data}, indent=2))
        else:
            print(json.dumps(data, indent=2))
    else:
        for report in all_reports:
            if args.quiet:
                for issue in report.errors:
                    print(issue)
            else:
                print(report.summary())
                print()

        if args.fix:
            print(f"Total fixes applied: {total_fixed}")

    for report in all_reports:
        if report.errors:
            has_errors = True
            break

    return 1 if has_errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
