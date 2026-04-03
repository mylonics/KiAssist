"""Tests for the KiCad symbol library (.kicad_sym) parser."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from kiassist_utils.kicad_parser.symbol_lib import (
    Pin,
    SymbolDef,
    SymbolLibrary,
)

# Path to the bundled fixture symbol library
FIXTURE_SYM = Path(__file__).parent / "fixtures" / "test_symbol_lib.kicad_sym"


class TestSymbolLibraryLoad:
    """Tests for SymbolLibrary.load()."""

    def test_load_fixture_file(self):
        """Loads the fixture file without raising."""
        lib = SymbolLibrary.load(FIXTURE_SYM)
        assert lib is not None

    def test_version_parsed(self):
        """File format version is parsed."""
        lib = SymbolLibrary.load(FIXTURE_SYM)
        assert lib.version == 20231120

    def test_generator_parsed(self):
        """Generator string is parsed."""
        lib = SymbolLibrary.load(FIXTURE_SYM)
        assert lib.generator == "kicad_symbol_editor"

    def test_symbols_count(self):
        """Correct number of symbols is loaded."""
        lib = SymbolLibrary.load(FIXTURE_SYM)
        assert len(lib.symbols) == 2

    def test_symbol_names(self):
        """Symbol names are parsed correctly."""
        lib = SymbolLibrary.load(FIXTURE_SYM)
        names = [s.name for s in lib.symbols]
        assert "R" in names
        assert "C" in names

    def test_symbol_properties(self):
        """Symbol properties are parsed."""
        lib = SymbolLibrary.load(FIXTURE_SYM)
        r = lib.find_by_name("R")
        assert r is not None
        prop_keys = [p.key for p in r.properties]
        assert "Reference" in prop_keys
        assert "Value" in prop_keys

    def test_symbol_pins(self):
        """Symbol pins are parsed."""
        lib = SymbolLibrary.load(FIXTURE_SYM)
        r = lib.find_by_name("R")
        assert r is not None
        pins = r.pins()
        assert len(pins) == 2

    def test_pin_numbers(self):
        """Pin numbers are parsed."""
        lib = SymbolLibrary.load(FIXTURE_SYM)
        r = lib.find_by_name("R")
        pin_numbers = {p.number for p in r.pins()}
        assert "1" in pin_numbers
        assert "2" in pin_numbers

    def test_pin_electrical_type(self):
        """Pin electrical type is parsed."""
        lib = SymbolLibrary.load(FIXTURE_SYM)
        r = lib.find_by_name("R")
        for pin in r.pins():
            assert pin.electrical_type == "passive"

    def test_nonexistent_file_raises(self):
        """Loading a nonexistent file raises FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            SymbolLibrary.load("/nonexistent/path.kicad_sym")

    def test_invalid_content_raises(self):
        """Loading invalid content raises ValueError."""
        with tempfile.NamedTemporaryFile(suffix=".kicad_sym", mode="w", delete=False) as f:
            f.write("(not_a_symbol_lib (version 1))")
            name = f.name
        with pytest.raises(ValueError):
            SymbolLibrary.load(name)


class TestSymbolLibrarySave:
    """Tests for SymbolLibrary.save()."""

    def test_save_creates_file(self):
        """save() creates a file at the given path."""
        lib = SymbolLibrary.load(FIXTURE_SYM)
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "out.kicad_sym"
            lib.save(out)
            assert out.exists()

    def test_save_round_trip_symbol_count(self):
        """Symbol count survives a save → reload round-trip."""
        lib = SymbolLibrary.load(FIXTURE_SYM)
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "out.kicad_sym"
            lib.save(out)
            lib2 = SymbolLibrary.load(out)
        assert len(lib2.symbols) == len(lib.symbols)

    def test_save_round_trip_symbol_names(self):
        """Symbol names survive a save → reload round-trip."""
        lib = SymbolLibrary.load(FIXTURE_SYM)
        original_names = sorted(s.name for s in lib.symbols)
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "out.kicad_sym"
            lib.save(out)
            lib2 = SymbolLibrary.load(out)
        reloaded_names = sorted(s.name for s in lib2.symbols)
        assert reloaded_names == original_names

    def test_save_round_trip_pin_count(self):
        """Pin count for each symbol survives save → reload."""
        lib = SymbolLibrary.load(FIXTURE_SYM)
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "out.kicad_sym"
            lib.save(out)
            lib2 = SymbolLibrary.load(out)
        for orig in lib.symbols:
            reloaded = lib2.find_by_name(orig.name)
            assert reloaded is not None
            assert len(reloaded.pins()) == len(orig.pins())


class TestSymbolLibraryFindByName:
    """Tests for SymbolLibrary.find_by_name()."""

    def test_find_existing_symbol(self):
        """Returns the symbol when it exists."""
        lib = SymbolLibrary.load(FIXTURE_SYM)
        sym = lib.find_by_name("R")
        assert sym is not None
        assert sym.name == "R"

    def test_find_nonexistent_returns_none(self):
        """Returns None for a symbol that does not exist."""
        lib = SymbolLibrary.load(FIXTURE_SYM)
        assert lib.find_by_name("DOES_NOT_EXIST") is None


class TestSymbolLibraryAddSymbol:
    """Tests for SymbolLibrary.add_symbol()."""

    def test_add_symbol_increases_count(self):
        """add_symbol() increases the count."""
        lib = SymbolLibrary.load(FIXTURE_SYM)
        new_sym = SymbolDef(name="TestNew")
        lib.add_symbol(new_sym)
        assert lib.find_by_name("TestNew") is not None

    def test_add_duplicate_raises(self):
        """Adding a duplicate name raises ValueError."""
        lib = SymbolLibrary.load(FIXTURE_SYM)
        dup = SymbolDef(name="R")
        with pytest.raises(ValueError):
            lib.add_symbol(dup)

    def test_added_symbol_survives_round_trip(self):
        """Newly added symbol survives a save → reload cycle."""
        lib = SymbolLibrary.load(FIXTURE_SYM)
        lib.add_symbol(SymbolDef(name="MyNewSymbol"))
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "out.kicad_sym"
            lib.save(out)
            lib2 = SymbolLibrary.load(out)
        assert lib2.find_by_name("MyNewSymbol") is not None


class TestSymbolLibraryRemoveSymbol:
    """Tests for SymbolLibrary.remove_symbol()."""

    def test_remove_existing_symbol(self):
        """Removing an existing symbol decreases the count."""
        lib = SymbolLibrary.load(FIXTURE_SYM)
        initial = len(lib.symbols)
        result = lib.remove_symbol("R")
        assert result is True
        assert len(lib.symbols) == initial - 1

    def test_remove_nonexistent_returns_false(self):
        """Removing a symbol that does not exist returns False."""
        lib = SymbolLibrary.load(FIXTURE_SYM)
        assert lib.remove_symbol("NOT_HERE") is False

    def test_removed_symbol_absent_after_round_trip(self):
        """Removed symbol is absent after save → reload."""
        lib = SymbolLibrary.load(FIXTURE_SYM)
        lib.remove_symbol("C")
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "out.kicad_sym"
            lib.save(out)
            lib2 = SymbolLibrary.load(out)
        assert lib2.find_by_name("C") is None


class TestSymbolLibraryModifySymbol:
    """Tests for SymbolLibrary.modify_symbol()."""

    def test_modify_existing_symbol(self):
        """Modifying an existing symbol returns True."""
        lib = SymbolLibrary.load(FIXTURE_SYM)
        result = lib.modify_symbol("R", pin_numbers_hide=True)
        assert result is True
        assert lib.find_by_name("R").pin_numbers_hide is True

    def test_modify_nonexistent_returns_false(self):
        """Modifying a nonexistent symbol returns False."""
        lib = SymbolLibrary.load(FIXTURE_SYM)
        assert lib.modify_symbol("NOT_HERE", pin_numbers_hide=True) is False

    def test_modify_clears_raw_tree(self):
        """modify_symbol() clears raw_tree so new attributes are serialised."""
        lib = SymbolLibrary.load(FIXTURE_SYM)
        lib.modify_symbol("R", pin_numbers_hide=True)
        sym = lib.find_by_name("R")
        assert sym.raw_tree is None


class TestSymbolLibJustifyRoundTrip:
    """Tests for multi-word justify in symbol lib effects."""

    def test_hidden_property_parsed(self):
        """Hidden property effects are parsed correctly."""
        lib = SymbolLibrary.load(FIXTURE_SYM)
        r_sym = lib.find_by_name("R")
        footprint_prop = next(p for p in r_sym.properties if p.key == "Footprint")
        assert footprint_prop.effects is not None
        assert footprint_prop.effects.hide is True

    def test_hidden_property_survives_round_trip_after_modify(self):
        """Hidden property effect survives save → reload after modify clears raw_tree."""
        lib = SymbolLibrary.load(FIXTURE_SYM)
        lib.modify_symbol("R", pin_names_offset=1.016)
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "out.kicad_sym"
            lib.save(out)
            lib2 = SymbolLibrary.load(out)
        r_sym = lib2.find_by_name("R")
        fp_prop = next(p for p in r_sym.properties if p.key == "Footprint")
        assert fp_prop.effects is not None
        assert fp_prop.effects.hide is True

    def test_property_bold_round_trip_after_modify(self):
        """Bold property effect survives after modify clears raw_tree."""
        from kiassist_utils.kicad_parser.models import Effects, Position, Property
        lib = SymbolLibrary.load(FIXTURE_SYM)
        # Add a symbol with a bold property effect
        sym = SymbolDef(name="TestBold")
        sym.properties = [
            Property(
                key="Reference",
                value="T",
                position=Position(0.0, 0.0, 0.0),
                effects=Effects(font_size=(1.27, 1.27), bold=True),
            )
        ]
        lib.add_symbol(sym)
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "out.kicad_sym"
            lib.save(out)
            lib2 = SymbolLibrary.load(out)
        t_sym = lib2.find_by_name("TestBold")
        ref_prop = next(p for p in t_sym.properties if p.key == "Reference")
        assert ref_prop.effects is not None
        assert ref_prop.effects.bold is True

    def test_symbol_lib_generator_version_not_in_fixture(self):
        """generator_version defaults to empty string when absent in file."""
        lib = SymbolLibrary.load(FIXTURE_SYM)
        assert lib.generator_version == ""

    def test_symbol_lib_generator_version_survives_round_trip(self):
        """generator_version is written and re-read when set."""
        lib = SymbolLibrary.load(FIXTURE_SYM)
        lib.generator_version = "8.0"
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "out.kicad_sym"
            lib.save(out)
            lib2 = SymbolLibrary.load(out)
        assert lib2.generator_version == "8.0"
