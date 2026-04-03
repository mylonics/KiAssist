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

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _find(tree: List[SExpr], tag: str) -> Optional[List[SExpr]]:
    for item in tree:
        if isinstance(item, list) and item and item[0] == tag:
            return item
    return None


def _find_all(tree: List[SExpr], tag: str) -> List[List[SExpr]]:
    return [item for item in tree if isinstance(item, list) and item and item[0] == tag]


def _atom(tree: List[SExpr], tag: str, default: str = "") -> str:
    child = _find(tree, tag)
    if child is None or len(child) < 2:
        return default
    return str(child[1])


# ---------------------------------------------------------------------------
# Platform-specific KiCad config directory
# ---------------------------------------------------------------------------


def _kicad_config_dir() -> Path:
    """Return the platform-specific KiCad user configuration directory."""
    system = platform.system()
    if system == "Windows":
        appdata = os.environ.get("APPDATA", "")
        return Path(appdata) / "kicad" / "8.0"
    if system == "Darwin":
        return Path.home() / "Library" / "Preferences" / "kicad" / "8.0"
    # Linux / other POSIX
    xdg_config = os.environ.get("XDG_CONFIG_HOME", "")
    if xdg_config:
        base = Path(xdg_config)
    else:
        base = Path.home() / ".config"
    return base / "kicad" / "8.0"


def _kicad_install_share_dir() -> Optional[Path]:
    """Attempt to locate the KiCad installation share directory.

    Returns ``None`` when KiCad is not installed or the directory cannot
    be determined.
    """
    system = platform.system()
    candidates: List[Path] = []
    if system == "Windows":
        prog = os.environ.get("PROGRAMFILES", r"C:\Program Files")
        candidates = [
            Path(prog) / "KiCad" / "8.0" / "share" / "kicad",
            Path(prog) / "KiCad" / "share" / "kicad",
        ]
    elif system == "Darwin":
        candidates = [
            Path("/Applications/KiCad/KiCad.app/Contents/SharedSupport"),
            Path("/usr/local/share/kicad"),
        ]
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
    """Return a best-effort mapping of common KiCad path variables."""
    share = _kicad_install_share_dir()
    env: Dict[str, str] = {}
    if share:
        env["KICAD8_SYMBOL_DIR"] = str(share / "symbols")
        env["KICAD8_FOOTPRINT_DIR"] = str(share / "footprints")
        env["KICAD7_SYMBOL_DIR"] = str(share / "symbols")
        env["KICAD7_FOOTPRINT_DIR"] = str(share / "footprints")
        env["KICAD6_SYMBOL_DIR"] = str(share / "symbols")
        env["KICAD6_FOOTPRINT_DIR"] = str(share / "footprints")
        env["KICAD_SYMBOL_DIR"] = str(share / "symbols")
        env["KICAD_FOOTPRINT_DIR"] = str(share / "footprints")
    return env


# ---------------------------------------------------------------------------
# Parse a lib-table file
# ---------------------------------------------------------------------------


def _parse_lib_table(path: Path) -> List[LibraryEntry]:
    """Parse a ``sym-lib-table`` or ``fp-lib-table`` file.

    Args:
        path: Path to the library table file.

    Returns:
        List of :class:`LibraryEntry` instances.
    """
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
        if entry.nickname:
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
