"""KiCad library discovery and resolution.

Discovers KiCad's installed symbol and footprint libraries by reading
sym-lib-table and fp-lib-table files. Resolves library nicknames to file paths.
"""

import os
import platform
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .sexpr import parse, find_all, get_value, SExpr


@dataclass
class LibraryEntry:
    """A single library entry from a lib-table file.

    Attributes:
        name: Library nickname used in references.
        type: Library type (e.g., "KiCad", "Legacy").
        uri: Library URI/path. May contain env vars like ${KICAD8_SYMBOL_DIR}.
        options: Library options string.
        descr: Library description.
    """
    name: str = ""
    type: str = "KiCad"
    uri: str = ""
    options: str = ""
    descr: str = ""


class LibraryDiscovery:
    """Discover and resolve KiCad libraries.

    Searches project-local and global library tables, resolves library
    nicknames to file paths, and caches library metadata for fast lookups.
    """

    def __init__(self):
        self._symbol_libs: Dict[str, LibraryEntry] = {}
        self._footprint_libs: Dict[str, LibraryEntry] = {}
        self._env_vars: Dict[str, str] = {}
        self._loaded = False

    def discover(self, project_dir: Optional[str] = None) -> None:
        """Discover all available libraries.

        Loads both global and project-local library tables.

        Args:
            project_dir: Optional project directory to search for local tables.
        """
        self._symbol_libs.clear()
        self._footprint_libs.clear()
        self._detect_env_vars()

        # Load global tables
        global_sym_table = self._find_global_sym_lib_table()
        if global_sym_table:
            self._load_lib_table(global_sym_table, self._symbol_libs)

        global_fp_table = self._find_global_fp_lib_table()
        if global_fp_table:
            self._load_lib_table(global_fp_table, self._footprint_libs)

        # Load project-local tables
        if project_dir:
            project_path = Path(project_dir)
            local_sym_table = project_path / "sym-lib-table"
            if local_sym_table.exists():
                self._load_lib_table(str(local_sym_table), self._symbol_libs)

            local_fp_table = project_path / "fp-lib-table"
            if local_fp_table.exists():
                self._load_lib_table(str(local_fp_table), self._footprint_libs)

        self._loaded = True

    @property
    def symbol_libraries(self) -> Dict[str, LibraryEntry]:
        """Get all discovered symbol libraries, keyed by nickname."""
        return dict(self._symbol_libs)

    @property
    def footprint_libraries(self) -> Dict[str, LibraryEntry]:
        """Get all discovered footprint libraries, keyed by nickname."""
        return dict(self._footprint_libs)

    def resolve_symbol_library(self, nickname: str) -> Optional[str]:
        """Resolve a symbol library nickname to a file path.

        Args:
            nickname: Library nickname (e.g., "Device").

        Returns:
            Resolved file path, or None if not found.
        """
        entry = self._symbol_libs.get(nickname)
        if entry is None:
            return None
        return self._resolve_uri(entry.uri)

    def resolve_footprint_library(self, nickname: str) -> Optional[str]:
        """Resolve a footprint library nickname to a directory path.

        Args:
            nickname: Library nickname (e.g., "Resistor_SMD").

        Returns:
            Resolved directory path, or None if not found.
        """
        entry = self._footprint_libs.get(nickname)
        if entry is None:
            return None
        return self._resolve_uri(entry.uri)

    def list_symbol_libraries(self) -> List[Dict[str, str]]:
        """List all discovered symbol libraries.

        Returns:
            List of dictionaries with library info (name, path, description).
        """
        results = []
        for name, entry in sorted(self._symbol_libs.items()):
            resolved = self._resolve_uri(entry.uri)
            results.append({
                "name": name,
                "path": resolved or entry.uri,
                "description": entry.descr,
                "type": entry.type,
            })
        return results

    def list_footprint_libraries(self) -> List[Dict[str, str]]:
        """List all discovered footprint libraries.

        Returns:
            List of dictionaries with library info (name, path, description).
        """
        results = []
        for name, entry in sorted(self._footprint_libs.items()):
            resolved = self._resolve_uri(entry.uri)
            results.append({
                "name": name,
                "path": resolved or entry.uri,
                "description": entry.descr,
                "type": entry.type,
            })
        return results

    def _detect_env_vars(self) -> None:
        """Detect KiCad environment variables for path resolution."""
        self._env_vars.clear()

        # Standard KiCad env variables
        kicad_env_names = [
            "KICAD8_SYMBOL_DIR", "KICAD8_FOOTPRINT_DIR",
            "KICAD8_3DMODEL_DIR", "KICAD8_TEMPLATE_DIR",
            "KICAD7_SYMBOL_DIR", "KICAD7_FOOTPRINT_DIR",
            "KICAD7_3DMODEL_DIR", "KICAD7_TEMPLATE_DIR",
            "KICAD6_SYMBOL_DIR", "KICAD6_FOOTPRINT_DIR",
            "KICAD6_3DMODEL_DIR", "KICAD6_TEMPLATE_DIR",
            "KICAD_SYMBOL_DIR", "KICAD_FOOTPRINT_DIR",
            "KICAD_3DMODEL_DIR", "KICAD_TEMPLATE_DIR",
            "KICAD_USER_DIR",
            "KIPRJMOD",
        ]

        for name in kicad_env_names:
            val = os.environ.get(name)
            if val:
                self._env_vars[name] = val

        # Try to detect default KiCad install paths
        system = platform.system()
        if system == "Linux":
            self._detect_linux_paths()
        elif system == "Darwin":
            self._detect_macos_paths()
        elif system == "Windows":
            self._detect_windows_paths()

    def _detect_linux_paths(self) -> None:
        """Detect KiCad library paths on Linux."""
        search_dirs = [
            "/usr/share/kicad",
            "/usr/local/share/kicad",
            str(Path.home() / ".local" / "share" / "kicad"),
            # Flatpak
            str(Path.home() / ".var" / "app" / "org.kicad.KiCad" /
                "data" / "kicad"),
        ]
        # Try versioned paths (8, 7, 6)
        for ver in ["8", "7", "6"]:
            search_dirs.extend([
                f"/usr/share/kicad/{ver}.0/symbols",
                f"/usr/share/kicad/symbols",
            ])

        self._populate_defaults_from_dirs(search_dirs)

    def _detect_macos_paths(self) -> None:
        """Detect KiCad library paths on macOS."""
        search_dirs = [
            "/Applications/KiCad/KiCad.app/Contents/SharedSupport",
            str(Path.home() / "Library" / "Application Support" / "kicad"),
        ]
        self._populate_defaults_from_dirs(search_dirs)

    def _detect_windows_paths(self) -> None:
        """Detect KiCad library paths on Windows."""
        program_files = os.environ.get("ProgramFiles", "C:\\Program Files")
        search_dirs = [
            os.path.join(program_files, "KiCad", "share", "kicad"),
            os.path.join(program_files, "KiCad", "8.0", "share", "kicad"),
            os.path.join(program_files, "KiCad", "7.0", "share", "kicad"),
        ]

        appdata = os.environ.get("APPDATA", "")
        if appdata:
            search_dirs.append(os.path.join(appdata, "kicad"))

        self._populate_defaults_from_dirs(search_dirs)

    def _populate_defaults_from_dirs(self, search_dirs: List[str]) -> None:
        """Set default env vars from discovered KiCad install directories."""
        for base_dir in search_dirs:
            base = Path(base_dir)
            if not base.exists():
                continue

            sym_dir = base / "symbols"
            if sym_dir.exists():
                for prefix in ["KICAD8", "KICAD7", "KICAD6", "KICAD"]:
                    key = f"{prefix}_SYMBOL_DIR"
                    if key not in self._env_vars:
                        self._env_vars[key] = str(sym_dir)

            fp_dir = base / "footprints"
            if fp_dir.exists():
                for prefix in ["KICAD8", "KICAD7", "KICAD6", "KICAD"]:
                    key = f"{prefix}_FOOTPRINT_DIR"
                    if key not in self._env_vars:
                        self._env_vars[key] = str(fp_dir)

            model_dir = base / "3dmodels"
            if model_dir.exists():
                for prefix in ["KICAD8", "KICAD7", "KICAD6", "KICAD"]:
                    key = f"{prefix}_3DMODEL_DIR"
                    if key not in self._env_vars:
                        self._env_vars[key] = str(model_dir)

    def _find_global_sym_lib_table(self) -> Optional[str]:
        """Find the global symbol library table file."""
        return self._find_global_lib_table("sym-lib-table")

    def _find_global_fp_lib_table(self) -> Optional[str]:
        """Find the global footprint library table file."""
        return self._find_global_lib_table("fp-lib-table")

    def _find_global_lib_table(self, filename: str) -> Optional[str]:
        """Find a global library table file."""
        system = platform.system()
        candidates = []

        if system == "Linux":
            config_dir = (Path.home() / ".config" / "kicad")
            candidates.extend([
                config_dir / "8.0" / filename,
                config_dir / "7.0" / filename,
                config_dir / "6.0" / filename,
                config_dir / filename,
            ])
            # Flatpak
            flatpak_dir = (Path.home() / ".var" / "app" /
                           "org.kicad.KiCad" / "config" / "kicad")
            candidates.extend([
                flatpak_dir / "8.0" / filename,
                flatpak_dir / "7.0" / filename,
                flatpak_dir / filename,
            ])
        elif system == "Darwin":
            lib_prefs = Path.home() / "Library" / "Preferences" / "kicad"
            candidates.extend([
                lib_prefs / "8.0" / filename,
                lib_prefs / "7.0" / filename,
                lib_prefs / filename,
            ])
        elif system == "Windows":
            appdata = os.environ.get("APPDATA", "")
            if appdata:
                kicad_config = Path(appdata) / "kicad"
                candidates.extend([
                    kicad_config / "8.0" / filename,
                    kicad_config / "7.0" / filename,
                    kicad_config / filename,
                ])

        for candidate in candidates:
            if candidate.exists():
                return str(candidate)
        return None

    def _load_lib_table(self, path: str, target: Dict[str, LibraryEntry]) -> None:
        """Parse a lib-table file and add entries to the target dict."""
        try:
            with open(path, 'r', encoding='utf-8') as f:
                text = f.read()
            tree = parse(text)
        except Exception:
            return

        for lib_expr in find_all(tree, "lib"):
            entry = LibraryEntry()
            name_val = get_value(lib_expr, "name")
            if name_val is not None:
                entry.name = str(name_val)
            type_val = get_value(lib_expr, "type")
            if type_val is not None:
                entry.type = str(type_val)
            uri_val = get_value(lib_expr, "uri")
            if uri_val is not None:
                entry.uri = str(uri_val)
            options_val = get_value(lib_expr, "options")
            if options_val is not None:
                entry.options = str(options_val)
            descr_val = get_value(lib_expr, "descr")
            if descr_val is not None:
                entry.descr = str(descr_val)
            if entry.name:
                target[entry.name] = entry

    def _resolve_uri(self, uri: str) -> Optional[str]:
        """Resolve a library URI, expanding environment variables.

        Args:
            uri: URI potentially containing ${VAR} references.

        Returns:
            Resolved path string, or None if variables can't be resolved.
        """
        if not uri:
            return None

        result = uri
        # Expand ${VAR} patterns
        import re
        pattern = re.compile(r'\$\{([^}]+)\}')

        def replace_var(match):
            var_name = match.group(1)
            # Check our detected vars first, then environment
            val = self._env_vars.get(var_name)
            if val is None:
                val = os.environ.get(var_name)
            if val is not None:
                return val
            return match.group(0)  # Leave unresolved

        result = pattern.sub(replace_var, result)

        # Normalize path separators
        result = str(Path(result))

        return result
