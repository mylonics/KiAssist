"""KiCad library discovery utilities.

Reads KiCad's ``sym-lib-table`` and ``fp-lib-table`` library table files to
discover all available symbol and footprint libraries, resolving library
nicknames to absolute file paths.

Library tables are searched in the following priority order:

1. Project-local table (``<project_dir>/sym-lib-table`` or
   ``<project_dir>/fp-lib-table``).
2. KiCad global user table (``<kicad_config_dir>/sym-lib-table`` or
   ``<kicad_config_dir>/fp-lib-table``).

Typical usage::

    disc = LibraryDiscovery()
    sym_libs = disc.list_symbol_libraries()
    path = disc.resolve_symbol_library("Device")
"""

from __future__ import annotations

import os
import platform
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional

from .sexpr import parse, SExpr
from ._helpers import _find, _find_all

# ---------------------------------------------------------------------------
# Module-local helper utilities
# ---------------------------------------------------------------------------


def _atom(tree: List[SExpr], tag: str, default: str = "") -> str:
    child = _find(tree, tag)
    if child is None or len(child) < 2:
        return default
    return str(child[1])


# ---------------------------------------------------------------------------
# Platform-specific KiCad config directory
# ---------------------------------------------------------------------------


def _find_latest_version_dir(base: Path) -> Optional[Path]:
    """Return the highest versioned kicad sub-directory under *base*.

    Scans for directories whose names look like ``X.Y`` (e.g. ``10.0``,
    ``11.0``) and returns the one with the highest version number.
    Only KiCad 10+ is supported; directories with major version < 10 are
    ignored.  Directories ending in ``.99`` are skipped because KiCad
    uses ``x.99`` for nightly / development builds which may have
    incomplete configurations.
    """
    best: Optional[Path] = None
    best_ver: tuple = (0,)
    if not base.is_dir():
        return None
    for child in base.iterdir():
        if not child.is_dir():
            continue
        parts = child.name.split(".")
        try:
            ver = tuple(int(p) for p in parts)
        except ValueError:
            continue
        # Only support KiCad 10+
        if ver[0] < 10:
            continue
        # Skip nightly/dev builds (e.g. 10.99, 11.99)
        if ver[-1] == 99:
            continue
        if ver > best_ver:
            best_ver = ver
            best = child
    return best


def _kicad_config_dir() -> Path:
    """Return the platform-specific KiCad user configuration directory."""
    system = platform.system()
    if system == "Windows":
        appdata = os.environ.get("APPDATA", "")
        base = Path(appdata) / "kicad"
    elif system == "Darwin":
        base = Path.home() / "Library" / "Preferences" / "kicad"
    else:
        # Linux / other POSIX
        xdg_config = os.environ.get("XDG_CONFIG_HOME", "")
        if xdg_config:
            base = Path(xdg_config) / "kicad"
        else:
            base = Path.home() / ".config" / "kicad"
    latest = _find_latest_version_dir(base)
    return latest if latest else base / "10.0"


def _kicad_install_share_dir() -> Optional[Path]:
    """Attempt to locate the KiCad installation share directory.

    Scans for the highest installed version (e.g. 10.0, 9.0, 8.0) and
    returns ``None`` when KiCad is not installed or the directory cannot
    be determined.
    """
    system = platform.system()
    if system == "Windows":
        prog = os.environ.get("PROGRAMFILES", r"C:\Program Files")
        kicad_base = Path(prog) / "KiCad"
        # Try versioned sub-directory first (e.g. KiCad/10.0/share/kicad)
        ver_dir = _find_latest_version_dir(kicad_base)
        if ver_dir:
            share = ver_dir / "share" / "kicad"
            if share.exists():
                return share
        # Fallback: un-versioned layout
        share = kicad_base / "share" / "kicad"
        if share.exists():
            return share
    elif system == "Darwin":
        candidates = [
            Path("/Applications/KiCad/KiCad.app/Contents/SharedSupport"),
            Path("/usr/local/share/kicad"),
        ]
        for c in candidates:
            if c.exists():
                return c
    else:
        candidates = [
            Path("/usr/share/kicad"),
            Path("/usr/local/share/kicad"),
            Path("/opt/kicad/share/kicad"),
        ]
        for c in candidates:
            if c.exists():
                return c
    return None


# ---------------------------------------------------------------------------
# LibraryEntry — one row in a lib table
# ---------------------------------------------------------------------------


@dataclass
class LibraryEntry:
    """One entry in a KiCad library table (sym-lib-table or fp-lib-table).

    Attributes:
        nickname:    Short library name used as prefix (e.g. ``"Device"``).
        uri:         Raw URI from the table, possibly containing KiCad
                     variable substitutions (e.g. ``"${KICAD8_SYMBOL_DIR}/…"``).
        plugin_type: Library plugin type (``"KiCad"``, ``"Legacy"``, ``"SCH_LIB_PLUGIN_CACHE"``).
        options:     Additional options string.
        description: Human-readable description.
    """

    nickname: str = ""
    uri: str = ""
    plugin_type: str = "KiCad"
    options: str = ""
    description: str = ""

    def resolved_path(self, env: Optional[Dict[str, str]] = None) -> Optional[Path]:
        """Resolve *uri* to an absolute :class:`Path`.

        KiCad variable substitutions (``${VAR}``) are expanded using *env*
        if provided, with a best-effort fallback to known KiCad paths.

        Args:
            env: Optional mapping of variable name → directory path.

        Returns:
            Resolved :class:`Path`, or ``None`` if resolution failed.
        """
        uri = self.uri
        if env:
            for var, val in env.items():
                uri = uri.replace(f"${{{var}}}", val)
        # Expand remaining ${…} using _DEFAULT_ENV
        for var, val in _default_env().items():
            uri = uri.replace(f"${{{var}}}", val)
        p = Path(uri)
        return p if p.exists() else None


@lru_cache(maxsize=1)
def _default_env() -> Dict[str, str]:
    """Return a best-effort mapping of common KiCad path variables.

    This includes built-in KiCad directory variables (e.g.
    ``KICAD_SYMBOL_DIR``) **and** any custom path variables the user has
    defined in KiCad's ``kicad_common.json`` (stored under the
    ``environment.vars`` key).
    """
    import json

    env: Dict[str, str] = {}

    # --- Custom user variables from kicad_common.json ---
    try:
        common_json = _kicad_config_dir() / "kicad_common.json"
        if common_json.is_file():
            data = json.loads(common_json.read_text(encoding="utf-8"))
            user_vars = data.get("environment", {}).get("vars", {})
            if isinstance(user_vars, dict):
                env.update(user_vars)
    except Exception:
        pass  # non-fatal – continue with built-in vars

    # --- Built-in KiCad installation directories ---
    share = _kicad_install_share_dir()
    if share:
        sym_dir = str(share / "symbols")
        fp_dir = str(share / "footprints")
        model_dir = str(share / "3dmodels")
        # KiCad 10+ variable names
        for ver in ("", "10", "11", "12"):
            prefix = f"KICAD{ver}" if ver else "KICAD"
            env[f"{prefix}_SYMBOL_DIR"] = sym_dir
            env[f"{prefix}_FOOTPRINT_DIR"] = fp_dir
            env[f"{prefix}_3DMODEL_DIR"] = model_dir
    return env


# ---------------------------------------------------------------------------
# Parse a lib-table file
# ---------------------------------------------------------------------------


def _parse_lib_table(path: Path, _depth: int = 0) -> List[LibraryEntry]:
    """Parse a ``sym-lib-table`` or ``fp-lib-table`` file.

    KiCad 10+ may include ``(type "Table")`` entries whose *uri* points
    to another lib-table file.  These are followed recursively (up to a
    reasonable depth limit).

    Args:
        path: Path to the library table file.

    Returns:
        List of :class:`LibraryEntry` instances.
    """
    if _depth > 5:
        return []  # guard against infinite recursion

    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []

    try:
        tree = parse(text)
    except ValueError:
        return []

    entries: List[LibraryEntry] = []
    for lib_node in _find_all(tree, "lib"):
        entry = LibraryEntry(
            nickname=_atom(lib_node, "name"),
            uri=_atom(lib_node, "uri"),
            plugin_type=_atom(lib_node, "type", "KiCad"),
            options=_atom(lib_node, "options"),
            description=_atom(lib_node, "descr"),
        )
        if not entry.nickname:
            continue
        # KiCad 10+: follow nested table references
        if entry.plugin_type == "Table":
            sub_path = Path(entry.uri)
            if sub_path.is_file():
                entries.extend(_parse_lib_table(sub_path, _depth + 1))
        else:
            entries.append(entry)
    return entries


# ---------------------------------------------------------------------------
# LibraryDiscovery
# ---------------------------------------------------------------------------


class LibraryDiscovery:
    """Discovers KiCad symbol and footprint libraries.

    Reads project-local and global library tables and resolves nicknames to
    file paths.  Results are cached in-instance after the first call.

    Args:
        project_dir: Optional path to a KiCad project directory.  When
                     provided, project-local tables are searched first.
    """

    def __init__(self, project_dir: Optional[str | os.PathLike] = None) -> None:
        self._project_dir: Optional[Path] = Path(project_dir) if project_dir else None
        self._sym_entries: Optional[List[LibraryEntry]] = None
        self._fp_entries: Optional[List[LibraryEntry]] = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def list_symbol_libraries(self) -> List[LibraryEntry]:
        """Return all known symbol library entries.

        Returns:
            Merged list of project-local and global symbol libraries.
        """
        if self._sym_entries is None:
            self._sym_entries = self._load_entries("sym-lib-table")
        return list(self._sym_entries)

    def list_footprint_libraries(self) -> List[LibraryEntry]:
        """Return all known footprint library entries.

        Returns:
            Merged list of project-local and global footprint libraries.
        """
        if self._fp_entries is None:
            self._fp_entries = self._load_entries("fp-lib-table")
        return list(self._fp_entries)

    def resolve_symbol_library(self, nickname: str) -> Optional[Path]:
        """Return the resolved file path for symbol library *nickname*.

        Args:
            nickname: Library short name (e.g. ``"Device"``).

        Returns:
            Absolute :class:`Path` to the ``.kicad_sym`` file, or ``None`` if
            not found or the file does not exist.
        """
        env = {"KIPRJMOD": str(self._project_dir)} if self._project_dir else None
        for entry in self.list_symbol_libraries():
            if entry.nickname == nickname:
                return entry.resolved_path(env=env)
        return None

    def resolve_footprint_library(self, nickname: str) -> Optional[Path]:
        """Return the resolved file path for footprint library *nickname*.

        Args:
            nickname: Library short name (e.g. ``"Resistor_SMD"``).

        Returns:
            Absolute :class:`Path` to the ``.pretty`` directory, or ``None``
            if not found or the directory does not exist.
        """
        env = {"KIPRJMOD": str(self._project_dir)} if self._project_dir else None
        for entry in self.list_footprint_libraries():
            if entry.nickname == nickname:
                return entry.resolved_path(env=env)
        return None

    def invalidate_cache(self) -> None:
        """Clear cached library lists so they are re-read on next access."""
        self._sym_entries = None
        self._fp_entries = None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load_entries(self, table_filename: str) -> List[LibraryEntry]:
        """Load library entries from project-local and global tables.

        Project-local entries take precedence: if both tables define a
        nickname, only the project-local entry is returned.

        Args:
            table_filename: File name (``"sym-lib-table"`` or
                            ``"fp-lib-table"``).

        Returns:
            Merged, deduplicated list of :class:`LibraryEntry` instances.
        """
        entries: Dict[str, LibraryEntry] = {}

        # 1. Global user table
        global_table = _kicad_config_dir() / table_filename
        for entry in _parse_lib_table(global_table):
            entries[entry.nickname] = entry

        # 2. Project-local table (overrides global)
        if self._project_dir:
            local_table = self._project_dir / table_filename
            for entry in _parse_lib_table(local_table):
                entries[entry.nickname] = entry

        return list(entries.values())
