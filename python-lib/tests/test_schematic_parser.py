"""Tests for the KiCad schematic (.kicad_sch) parser."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from kiassist_utils.kicad_parser.schematic import (
    Schematic,
    SchematicSymbol,
    Wire,
    Bus,
    Junction,
    NoConnect,
    Label,
    GlobalLabel,
)

# Path to the bundled fixture schematic
FIXTURE_SCH = Path(__file__).parent / "fixtures" / "test_schematic.kicad_sch"


class TestSchematicLoad:
    """Tests for Schematic.load()."""

    def test_load_fixture_file(self):
        """Loads the fixture file without raising."""
        sch = Schematic.load(FIXTURE_SCH)
        assert sch is not None

    def test_version_parsed(self):
        """File format version is parsed as an integer."""
        sch = Schematic.load(FIXTURE_SCH)
        assert sch.version == 20231120

    def test_generator_parsed(self):
        """Generator string is parsed."""
        sch = Schematic.load(FIXTURE_SCH)
        assert sch.generator == "eeschema"

    def test_uuid_parsed(self):
        """Schematic UUID is populated."""
        sch = Schematic.load(FIXTURE_SCH)
        assert sch.uuid.value == "00000000-0000-0000-0000-000000000001"

    def test_paper_parsed(self):
        """Paper size string is parsed."""
        sch = Schematic.load(FIXTURE_SCH)
        assert sch.paper == "A4"

    def test_lib_symbols_parsed(self):
        """Embedded library symbols are parsed."""
        sch = Schematic.load(FIXTURE_SCH)
        assert len(sch.lib_symbols) == 1
        assert sch.lib_symbols[0].name == "Device:R"

    def test_symbols_parsed(self):
        """Placed symbol instances are parsed."""
        sch = Schematic.load(FIXTURE_SCH)
        assert len(sch.symbols) == 2

    def test_symbol_reference_value(self):
        """Symbol reference and value properties are accessible."""
        sch = Schematic.load(FIXTURE_SCH)
        r1 = next(s for s in sch.symbols if s.reference == "R1")
        assert r1.value == "10k"
        assert r1.footprint == "Resistor_SMD:R_0402_1005Metric"

    def test_symbol_lib_id(self):
        """Symbol lib_id is parsed correctly."""
        sch = Schematic.load(FIXTURE_SCH)
        r1 = next(s for s in sch.symbols if s.reference == "R1")
        assert r1.lib_id == "Device:R"

    def test_symbol_position(self):
        """Symbol placement position is parsed."""
        sch = Schematic.load(FIXTURE_SCH)
        r1 = next(s for s in sch.symbols if s.reference == "R1")
        assert r1.position.x == pytest.approx(81.28)
        assert r1.position.y == pytest.approx(50.8)

    def test_wires_parsed(self):
        """Wire segments are parsed."""
        sch = Schematic.load(FIXTURE_SCH)
        assert len(sch.wires) == 2

    def test_wire_coordinates(self):
        """Wire endpoint coordinates are parsed correctly."""
        sch = Schematic.load(FIXTURE_SCH)
        w = sch.wires[0]
        assert len(w.pts) == 2
        pts = list(w.pts)
        assert pts[0].x == pytest.approx(78.74)
        assert pts[0].y == pytest.approx(50.8)

    def test_junction_parsed(self):
        """Junction markers are parsed."""
        sch = Schematic.load(FIXTURE_SCH)
        assert len(sch.junctions) == 1
        assert sch.junctions[0].position.x == pytest.approx(81.28)

    def test_no_connect_parsed(self):
        """No-connect markers are parsed."""
        sch = Schematic.load(FIXTURE_SCH)
        assert len(sch.no_connects) == 1
        assert sch.no_connects[0].position.x == pytest.approx(86.36)

    def test_label_parsed(self):
        """Net labels are parsed."""
        sch = Schematic.load(FIXTURE_SCH)
        assert len(sch.labels) == 1
        assert sch.labels[0].text == "VCC"

    def test_global_label_parsed(self):
        """Global labels are parsed."""
        sch = Schematic.load(FIXTURE_SCH)
        assert len(sch.global_labels) == 1
        assert sch.global_labels[0].text == "GND"
        assert sch.global_labels[0].shape == "power_in"

    def test_nonexistent_file_raises(self):
        """Loading a nonexistent file raises FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            Schematic.load("/nonexistent/path/schematic.kicad_sch")

    def test_invalid_content_raises(self):
        """Loading invalid content raises ValueError."""
        with tempfile.NamedTemporaryFile(suffix=".kicad_sch", mode="w", delete=False) as f:
            f.write("(not_a_schematic (version 1))")
            name = f.name
        with pytest.raises(ValueError):
            Schematic.load(name)


class TestSchematicSave:
    """Tests for Schematic.save()."""

    def test_save_creates_file(self):
        """save() creates a file at the given path."""
        sch = Schematic.load(FIXTURE_SCH)
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "out.kicad_sch"
            sch.save(out)
            assert out.exists()

    def test_save_round_trip_version(self):
        """Version survives a save → reload round-trip."""
        sch = Schematic.load(FIXTURE_SCH)
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "out.kicad_sch"
            sch.save(out)
            sch2 = Schematic.load(out)
        assert sch2.version == sch.version

    def test_save_round_trip_symbols(self):
        """Symbol count survives a save → reload round-trip."""
        sch = Schematic.load(FIXTURE_SCH)
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "out.kicad_sch"
            sch.save(out)
            sch2 = Schematic.load(out)
        assert len(sch2.symbols) == len(sch.symbols)

    def test_save_round_trip_wires(self):
        """Wire count survives a save → reload round-trip."""
        sch = Schematic.load(FIXTURE_SCH)
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "out.kicad_sch"
            sch.save(out)
            sch2 = Schematic.load(out)
        assert len(sch2.wires) == len(sch.wires)

    def test_save_round_trip_references(self):
        """Reference designators survive a save → reload round-trip."""
        sch = Schematic.load(FIXTURE_SCH)
        original_refs = sorted(s.reference for s in sch.symbols)
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "out.kicad_sch"
            sch.save(out)
            sch2 = Schematic.load(out)
        reloaded_refs = sorted(s.reference for s in sch2.symbols)
        assert reloaded_refs == original_refs


class TestSchematicAddWire:
    """Tests for Schematic.add_wire()."""

    def test_add_wire_increases_count(self):
        """add_wire() increases the wire count by 1."""
        sch = Schematic.load(FIXTURE_SCH)
        initial = len(sch.wires)
        sch.add_wire(0.0, 0.0, 10.0, 0.0)
        assert len(sch.wires) == initial + 1

    def test_add_wire_has_correct_endpoints(self):
        """Added wire has the correct start and end coordinates."""
        sch = Schematic()
        wire = sch.add_wire(10.0, 20.0, 30.0, 20.0)
        pts = list(wire.pts)
        assert pts[0].x == pytest.approx(10.0)
        assert pts[0].y == pytest.approx(20.0)
        assert pts[1].x == pytest.approx(30.0)
        assert pts[1].y == pytest.approx(20.0)

    def test_add_wire_gets_uuid(self):
        """Newly added wire has a non-empty UUID."""
        sch = Schematic()
        wire = sch.add_wire(0.0, 0.0, 5.0, 0.0)
        assert bool(wire.uuid)

    def test_add_wire_round_trip(self):
        """Added wire survives a save → reload cycle."""
        sch = Schematic.load(FIXTURE_SCH)
        sch.add_wire(0.0, 0.0, 10.0, 0.0)
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "out.kicad_sch"
            sch.save(out)
            sch2 = Schematic.load(out)
        assert len(sch2.wires) == len(sch.wires)


class TestSchematicAddSymbol:
    """Tests for Schematic.add_symbol()."""

    def test_add_symbol_increases_count(self):
        """add_symbol() increases the symbol count by 1."""
        sch = Schematic()
        initial = len(sch.symbols)
        sch.add_symbol("Device:C", 100.0, 100.0, reference="C1", value="100n")
        assert len(sch.symbols) == initial + 1

    def test_add_symbol_reference(self):
        """Newly added symbol has the given reference."""
        sch = Schematic()
        sym = sch.add_symbol("Device:C", 0.0, 0.0, reference="C1", value="100n")
        assert sym.reference == "C1"

    def test_add_symbol_value(self):
        """Newly added symbol has the given value."""
        sch = Schematic()
        sym = sch.add_symbol("Device:R", 0.0, 0.0, reference="R3", value="4k7")
        assert sym.value == "4k7"

    def test_add_symbol_position(self):
        """Newly added symbol is at the specified position."""
        sch = Schematic()
        sym = sch.add_symbol("Device:R", 50.0, 75.0, reference="R1")
        assert sym.position.x == pytest.approx(50.0)
        assert sym.position.y == pytest.approx(75.0)

    def test_add_symbol_gets_uuid(self):
        """Newly added symbol has a non-empty UUID."""
        sch = Schematic()
        sym = sch.add_symbol("Device:R", 0.0, 0.0, reference="R1")
        assert bool(sym.uuid)


class TestSchematicRemoveSymbol:
    """Tests for Schematic.remove_symbol()."""

    def test_remove_existing_symbol(self):
        """Removing an existing symbol decreases count by 1."""
        sch = Schematic.load(FIXTURE_SCH)
        initial = len(sch.symbols)
        result = sch.remove_symbol("R1")
        assert result is True
        assert len(sch.symbols) == initial - 1

    def test_remove_nonexistent_symbol(self):
        """Removing a nonexistent reference returns False."""
        sch = Schematic.load(FIXTURE_SCH)
        result = sch.remove_symbol("X99")
        assert result is False

    def test_removed_symbol_gone_after_reload(self):
        """Removed symbol is absent after save → reload."""
        sch = Schematic.load(FIXTURE_SCH)
        sch.remove_symbol("R1")
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "out.kicad_sch"
            sch.save(out)
            sch2 = Schematic.load(out)
        refs = [s.reference for s in sch2.symbols]
        assert "R1" not in refs


class TestSchematicFindSymbols:
    """Tests for Schematic.find_symbols()."""

    def test_find_by_reference(self):
        """find_symbols(reference=…) returns the matching symbol."""
        sch = Schematic.load(FIXTURE_SCH)
        result = sch.find_symbols(reference="R1")
        assert len(result) == 1
        assert result[0].reference == "R1"

    def test_find_by_value(self):
        """find_symbols(value=…) returns all matching symbols."""
        sch = Schematic.load(FIXTURE_SCH)
        result = sch.find_symbols(value="10k")
        assert len(result) == 2

    def test_find_by_lib_id(self):
        """find_symbols(lib_id=…) filters by library identifier."""
        sch = Schematic.load(FIXTURE_SCH)
        result = sch.find_symbols(lib_id="Device:R")
        assert len(result) == 2

    def test_find_combined_filters(self):
        """Multiple filter arguments are ANDed together."""
        sch = Schematic.load(FIXTURE_SCH)
        result = sch.find_symbols(reference="R2", value="10k")
        assert len(result) == 1
        assert result[0].reference == "R2"

    def test_find_returns_empty_for_no_match(self):
        """No match returns an empty list."""
        sch = Schematic.load(FIXTURE_SCH)
        result = sch.find_symbols(reference="U99")
        assert result == []


class TestSchematicAddJunctionAndNoConnect:
    """Tests for add_junction() and add_no_connect()."""

    def test_add_junction(self):
        """add_junction() adds a junction at the given position."""
        sch = Schematic()
        j = sch.add_junction(10.0, 20.0)
        assert len(sch.junctions) == 1
        assert j.position.x == pytest.approx(10.0)
        assert j.position.y == pytest.approx(20.0)

    def test_add_no_connect(self):
        """add_no_connect() adds a no-connect at the given position."""
        sch = Schematic()
        nc = sch.add_no_connect(5.0, 5.0)
        assert len(sch.no_connects) == 1
        assert nc.position.x == pytest.approx(5.0)

    def test_add_label(self):
        """add_label() adds a net label at the given position."""
        sch = Schematic()
        lbl = sch.add_label("NET1", 0.0, 0.0)
        assert len(sch.labels) == 1
        assert lbl.text == "NET1"
