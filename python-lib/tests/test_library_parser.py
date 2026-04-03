"""Tests for the KiCad library discovery module (library.py)."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest import mock

import pytest

from kiassist_utils.kicad_parser.library import (
    LibraryDiscovery,
    LibraryEntry,
    _parse_lib_table,
    _kicad_config_dir,
)

# Path to the bundled fixture library tables
FIXTURES_DIR = Path(__file__).parent / "fixtures"
SYM_LIB_TABLE = FIXTURES_DIR / "sym-lib-table"
FP_LIB_TABLE = FIXTURES_DIR / "fp-lib-table"


class TestParseLibTable:
    """Tests for the _parse_lib_table() internal function."""

    def test_parse_sym_lib_table(self):
        """Parses the fixture sym-lib-table and returns entries."""
        entries = _parse_lib_table(SYM_LIB_TABLE)
        assert len(entries) == 1

    def test_parse_fp_lib_table(self):
        """Parses the fixture fp-lib-table and returns entries."""
        entries = _parse_lib_table(FP_LIB_TABLE)
        assert len(entries) == 1

    def test_entry_nickname(self):
        """Library nickname is parsed."""
        entries = _parse_lib_table(SYM_LIB_TABLE)
        assert entries[0].nickname == "TestSymLib"

    def test_entry_uri(self):
        """Library URI is parsed."""
        entries = _parse_lib_table(SYM_LIB_TABLE)
        assert "${KIPRJMOD}" in entries[0].uri

    def test_entry_type(self):
        """Library type is parsed."""
        entries = _parse_lib_table(SYM_LIB_TABLE)
        assert entries[0].plugin_type == "KiCad"

    def test_entry_description(self):
        """Library description is parsed."""
        entries = _parse_lib_table(SYM_LIB_TABLE)
        assert entries[0].description == "Test symbol library"

    def test_nonexistent_file_returns_empty(self):
        """Returns empty list for a file that doesn't exist."""
        entries = _parse_lib_table(Path("/nonexistent/sym-lib-table"))
        assert entries == []

    def test_invalid_content_returns_empty(self):
        """Returns empty list for invalid S-expression content."""
        with tempfile.NamedTemporaryFile(mode="w", suffix="-sym-lib-table", delete=False) as f:
            f.write("this is not valid s-expression ))))")
            name = f.name
        entries = _parse_lib_table(Path(name))
        assert entries == []

    def test_inline_round_trip_table(self):
        """A hand-crafted sym-lib-table parses correctly."""
        content = (
            '(sym_lib_table\n'
            '  (version 7)\n'
            '  (lib (name "MyLib") (type "KiCad") '
            '(uri "/path/to/MyLib.kicad_sym") (options "") (descr "My lib"))\n'
            ')\n'
        )
        with tempfile.NamedTemporaryFile(mode="w", suffix="-sym-lib-table", delete=False) as f:
            f.write(content)
            name = f.name
        entries = _parse_lib_table(Path(name))
        assert len(entries) == 1
        assert entries[0].nickname == "MyLib"
        assert entries[0].uri == "/path/to/MyLib.kicad_sym"


class TestLibraryEntryResolvedPath:
    """Tests for LibraryEntry.resolved_path()."""

    def test_resolves_absolute_path_that_exists(self):
        """An absolute path that exists is returned as-is."""
        with tempfile.NamedTemporaryFile(suffix=".kicad_sym", delete=False) as f:
            name = f.name
        entry = LibraryEntry(nickname="Test", uri=name)
        result = entry.resolved_path()
        assert result is not None
        assert result == Path(name)

    def test_returns_none_for_nonexistent_path(self):
        """Returns None when the resolved path does not exist."""
        entry = LibraryEntry(nickname="Test", uri="/nonexistent/path.kicad_sym")
        assert entry.resolved_path() is None

    def test_custom_env_substitution(self):
        """Custom env dict is used for variable substitution."""
        with tempfile.TemporaryDirectory() as tmpdir:
            sym_file = Path(tmpdir) / "MyLib.kicad_sym"
            sym_file.touch()
            entry = LibraryEntry(nickname="Test", uri="${MY_DIR}/MyLib.kicad_sym")
            result = entry.resolved_path(env={"MY_DIR": tmpdir})
            assert result == sym_file

    def test_returns_none_when_var_not_resolved(self):
        """Returns None when a variable cannot be resolved."""
        entry = LibraryEntry(nickname="Test", uri="${UNKNOWN_VAR}/lib.kicad_sym")
        result = entry.resolved_path(env={})
        assert result is None


class TestLibraryDiscovery:
    """Tests for the LibraryDiscovery class."""

    def _make_discovery_with_fixtures(self, project_dir: Path) -> LibraryDiscovery:
        """Create a LibraryDiscovery pointed at a tmp dir with fixture tables."""
        # Copy fixture tables into the project dir
        import shutil
        shutil.copy(SYM_LIB_TABLE, project_dir / "sym-lib-table")
        shutil.copy(FP_LIB_TABLE, project_dir / "fp-lib-table")
        # Patch kicad_config_dir to a non-existent dir so only project-local tables are used
        with mock.patch(
            "kiassist_utils.kicad_parser.library._kicad_config_dir",
            return_value=Path("/nonexistent/kicad/config"),
        ):
            disc = LibraryDiscovery(project_dir=project_dir)
            # Force load while mock is active
            sym = disc.list_symbol_libraries()
            fp = disc.list_footprint_libraries()
        return disc, sym, fp

    def test_list_symbol_libraries_returns_list(self):
        """list_symbol_libraries() returns a list."""
        with tempfile.TemporaryDirectory() as tmpdir:
            disc, sym_libs, _ = self._make_discovery_with_fixtures(Path(tmpdir))
        assert isinstance(sym_libs, list)

    def test_list_symbol_libraries_from_project(self):
        """Project-local sym-lib-table entries are returned."""
        with tempfile.TemporaryDirectory() as tmpdir:
            disc, sym_libs, _ = self._make_discovery_with_fixtures(Path(tmpdir))
        assert any(e.nickname == "TestSymLib" for e in sym_libs)

    def test_list_footprint_libraries_from_project(self):
        """Project-local fp-lib-table entries are returned."""
        with tempfile.TemporaryDirectory() as tmpdir:
            disc, _, fp_libs = self._make_discovery_with_fixtures(Path(tmpdir))
        assert any(e.nickname == "TestFpLib" for e in fp_libs)

    def test_project_local_overrides_global(self):
        """Project-local entry with same nickname replaces the global entry."""
        global_content = (
            '(sym_lib_table\n'
            '  (lib (name "SharedLib") (type "KiCad") '
            '(uri "/global/SharedLib.kicad_sym") (options "") (descr "global"))\n'
            ')\n'
        )
        local_content = (
            '(sym_lib_table\n'
            '  (lib (name "SharedLib") (type "KiCad") '
            '(uri "/local/SharedLib.kicad_sym") (options "") (descr "local"))\n'
            ')\n'
        )
        with tempfile.TemporaryDirectory() as global_dir, \
             tempfile.TemporaryDirectory() as project_dir:
            Path(global_dir, "sym-lib-table").write_text(global_content)
            Path(project_dir, "sym-lib-table").write_text(local_content)
            with mock.patch(
                "kiassist_utils.kicad_parser.library._kicad_config_dir",
                return_value=Path(global_dir),
            ):
                disc = LibraryDiscovery(project_dir=project_dir)
                entries = disc.list_symbol_libraries()
        shared = next(e for e in entries if e.nickname == "SharedLib")
        assert shared.uri == "/local/SharedLib.kicad_sym"

    def test_resolve_nonexistent_nickname_returns_none(self):
        """resolve_symbol_library() returns None for an unknown nickname."""
        with tempfile.TemporaryDirectory() as tmpdir:
            disc, _, _ = self._make_discovery_with_fixtures(Path(tmpdir))
        assert disc.resolve_symbol_library("DOES_NOT_EXIST") is None

    def test_no_project_dir_still_works(self):
        """LibraryDiscovery without a project_dir still returns a list."""
        with mock.patch(
            "kiassist_utils.kicad_parser.library._kicad_config_dir",
            return_value=Path("/nonexistent/kicad/config"),
        ):
            disc = LibraryDiscovery()
            entries = disc.list_symbol_libraries()
        assert isinstance(entries, list)

    def test_invalidate_cache(self):
        """invalidate_cache() clears cached lists."""
        with tempfile.TemporaryDirectory() as tmpdir:
            disc, sym_libs, _ = self._make_discovery_with_fixtures(Path(tmpdir))
            disc.invalidate_cache()
            assert disc._sym_entries is None
            assert disc._fp_entries is None

    def test_kicad_config_dir_returns_path(self):
        """_kicad_config_dir() returns a Path object."""
        result = _kicad_config_dir()
        assert isinstance(result, Path)
