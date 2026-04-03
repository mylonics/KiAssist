"""Tests for the KiCad schematic (.kicad_sch) parser."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from kiassist_utils.kicad_parser.schematic import (
    Schematic,
    SchematicSymbol,
    TitleBlock,
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


class TestLabelEffectsRoundTrip:
    """Tests for Label / GlobalLabel / HierarchicalLabel effects serialisation."""

    def test_label_effects_in_to_tree(self):
        """Label.to_tree() includes an effects node."""
        from kiassist_utils.kicad_parser.schematic import Label
        from kiassist_utils.kicad_parser.models import Effects
        lbl = Label(text="VCC")
        lbl.effects = Effects(font_size=(1.27, 1.27), bold=False, italic=False)
        tree = lbl.to_tree()
        tags = [item[0] for item in tree if isinstance(item, list) and item]
        assert "effects" in tags

    def test_label_effects_survive_round_trip(self):
        """Label effects survive a save → reload cycle."""
        sch = Schematic.load(FIXTURE_SCH)
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "out.kicad_sch"
            sch.save(out)
            sch2 = Schematic.load(out)
        assert len(sch2.labels) == len(sch.labels)
        # Labels should be parseable without errors after reload
        for lbl in sch2.labels:
            assert lbl.text != ""

    def test_global_label_effects_in_to_tree(self):
        """GlobalLabel.to_tree() includes an effects node."""
        from kiassist_utils.kicad_parser.schematic import GlobalLabel
        gl = GlobalLabel(text="VCC", shape="input")
        tree = gl.to_tree()
        tags = [item[0] for item in tree if isinstance(item, list) and item]
        assert "effects" in tags

    def test_hierarchical_label_effects_in_to_tree(self):
        """HierarchicalLabel.to_tree() includes an effects node."""
        from kiassist_utils.kicad_parser.schematic import HierarchicalLabel
        hl = HierarchicalLabel(text="CLK", shape="output")
        tree = hl.to_tree()
        tags = [item[0] for item in tree if isinstance(item, list) and item]
        assert "effects" in tags

    def test_label_bold_effects_round_trip(self):
        """Bold label effects survive serialization."""
        from kiassist_utils.kicad_parser.schematic import Label
        from kiassist_utils.kicad_parser.models import Effects, Position
        from kiassist_utils.kicad_parser.sexpr import parse, serialize
        lbl = Label(text="BOLD_NET", position=Position(10.0, 20.0, 0.0))
        lbl.effects = Effects(font_size=(1.27, 1.27), bold=True)
        tree = lbl.to_tree()
        text = serialize(tree)
        parsed = parse(text)
        lbl2 = Label.from_tree(parsed)
        assert lbl2.effects.bold is True
        assert lbl2.text == "BOLD_NET"

    def test_label_hide_effects_round_trip(self):
        """Hidden label effects survive serialization."""
        from kiassist_utils.kicad_parser.schematic import Label
        from kiassist_utils.kicad_parser.models import Effects, Position
        from kiassist_utils.kicad_parser.sexpr import parse, serialize
        lbl = Label(text="HIDDEN_NET", position=Position(5.0, 5.0, 0.0))
        lbl.effects = Effects(font_size=(1.27, 1.27), hide=True)
        tree = lbl.to_tree()
        text = serialize(tree)
        parsed = parse(text)
        lbl2 = Label.from_tree(parsed)
        assert lbl2.effects.hide is True


class TestSchematicTitleBlock:
    """Tests for TitleBlock parsing and round-trip."""

    def test_title_block_parsed(self):
        """title_block attribute is populated when present in the file."""
        sch = Schematic.load(FIXTURE_SCH)
        assert sch.title_block is not None

    def test_title_block_title(self):
        """Title is parsed."""
        sch = Schematic.load(FIXTURE_SCH)
        assert sch.title_block.title == "Test Schematic"

    def test_title_block_date(self):
        """Date is parsed."""
        sch = Schematic.load(FIXTURE_SCH)
        assert sch.title_block.date == "2026-04-03"

    def test_title_block_revision(self):
        """Revision is parsed."""
        sch = Schematic.load(FIXTURE_SCH)
        assert sch.title_block.revision == "1.0"

    def test_title_block_company(self):
        """Company is parsed."""
        sch = Schematic.load(FIXTURE_SCH)
        assert sch.title_block.company == "KiAssist"

    def test_title_block_comments(self):
        """Comments dict is parsed."""
        sch = Schematic.load(FIXTURE_SCH)
        assert sch.title_block.comments.get(1) == "Top-level comment"

    def test_title_block_round_trip_title(self):
        """Title survives a save → reload round-trip."""
        sch = Schematic.load(FIXTURE_SCH)
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "out.kicad_sch"
            sch.save(out)
            sch2 = Schematic.load(out)
        assert sch2.title_block is not None
        assert sch2.title_block.title == "Test Schematic"

    def test_no_title_block_when_absent(self):
        """title_block is None when not present in the file."""
        sch = Schematic()
        assert sch.title_block is None

    def test_title_block_not_in_extra(self):
        """title_block content does not appear in _extra."""
        sch = Schematic.load(FIXTURE_SCH)
        tags = [item[0] for item in sch._extra if isinstance(item, list) and item]
        assert "title_block" not in tags

    def test_title_block_to_tree_round_trip(self):
        """TitleBlock.to_tree() round-trips correctly."""
        tb = TitleBlock(
            title="My Project",
            date="2026-01-01",
            revision="2.0",
            company="ACME",
            comments={1: "first comment"},
        )
        from kiassist_utils.kicad_parser.sexpr import parse, serialize
        tree = tb.to_tree()
        text = serialize(tree)
        parsed = parse(text)
        tb2 = TitleBlock.from_tree(parsed)
        assert tb2.title == "My Project"
        assert tb2.revision == "2.0"
        assert tb2.comments[1] == "first comment"


class TestSchematicPinUUIDs:
    """Tests for SchematicSymbol.pin_uuids."""

    def test_pin_uuids_parsed(self):
        """pin_uuids is populated for symbols that have pin uuid entries."""
        sch = Schematic.load(FIXTURE_SCH)
        r1 = next(s for s in sch.symbols if s.reference == "R1")
        assert len(r1.pin_uuids) == 2

    def test_pin_uuid_values(self):
        """Pin UUIDs have the expected values from the fixture."""
        sch = Schematic.load(FIXTURE_SCH)
        r1 = next(s for s in sch.symbols if s.reference == "R1")
        assert r1.pin_uuids["1"].value == "00000000-0000-0000-0000-000000000061"
        assert r1.pin_uuids["2"].value == "00000000-0000-0000-0000-000000000062"

    def test_pin_uuid_round_trip(self):
        """pin_uuids survive a save → reload round-trip (raw_tree preserves them)."""
        sch = Schematic.load(FIXTURE_SCH)
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "out.kicad_sch"
            sch.save(out)
            sch2 = Schematic.load(out)
        r1 = next(s for s in sch2.symbols if s.reference == "R1")
        assert r1.pin_uuids["1"].value == "00000000-0000-0000-0000-000000000061"


class TestGetConnectedNets:
    """Tests for Schematic.get_connected_nets() wire topology."""

    def _build_sch(self) -> Schematic:
        """Build a minimal schematic with wires, a label, and a symbol."""
        sch = Schematic()
        # Wire from (0,0) to (10,0)
        sch.add_wire(0.0, 0.0, 10.0, 0.0)
        # Label "VCC" attached at the left end (0,0)
        sch.add_label("VCC", 0.0, 0.0)
        return sch

    def test_returns_dict(self):
        """get_connected_nets() returns a dict."""
        sch = Schematic.load(FIXTURE_SCH)
        result = sch.get_connected_nets()
        assert isinstance(result, dict)

    def test_label_nets_present(self):
        """Labels appear as keys in the result."""
        sch = Schematic.load(FIXTURE_SCH)
        result = sch.get_connected_nets()
        assert "VCC" in result
        assert "GND" in result

    def test_unconnected_label_has_empty_pins(self):
        """A label with no wires or pins still appears with an empty list."""
        sch = Schematic()
        sch.add_label("FLOATING", 100.0, 100.0)
        result = sch.get_connected_nets()
        assert "FLOATING" in result
        assert result["FLOATING"] == []

    def test_wire_topology_connects_label_to_pin(self):
        """A label connected by wire to a pin endpoint is mapped correctly."""
        sch = self._build_sch()
        # Add a lib symbol that has a pin at (0, 3.81) relative to origin.
        # We embed a minimal lib_symbols entry so get_pin_positions works.
        from kiassist_utils.kicad_parser.sexpr import parse
        lib_sym_text = (
            '(symbol "TestLib:LED" '
            '  (symbol "LED_1_1"'
            '    (pin passive line (at 0.0 0.0 270) (length 0)'
            '      (name "A" (effects (font (size 1.27 1.27))))'
            '      (number "1" (effects (font (size 1.27 1.27))))'
            '    )'
            '  )'
            ')'
        )
        from kiassist_utils.kicad_parser.schematic import LibSymbol
        lib_sym = LibSymbol.from_tree(parse(lib_sym_text))
        sch.lib_symbols.append(lib_sym)
        # Place symbol so pin "1" lands exactly at (0.0, 0.0)
        from kiassist_utils.kicad_parser.schematic import SchematicSymbol
        from kiassist_utils.kicad_parser.models import Position, Property
        sym = SchematicSymbol()
        sym.lib_id = "TestLib:LED"
        sym.position = Position(0.0, 0.0, 0.0)
        sym.properties = [
            Property("Reference", "D1", Position(0, 0)),
            Property("Value", "LED", Position(0, 0)),
        ]
        sch.symbols.append(sym)
        result = sch.get_connected_nets()
        # D1 pin 1 is at (0,0) which is the same as the VCC label — should be connected
        assert "VCC" in result
        assert "D1:1" in result["VCC"]


class TestJustifyMultiWord:
    """Tests for multi-word (justify ...) parsing and round-tripping."""

    def test_justify_right_bottom_parsed(self):
        """(justify right bottom) is parsed as 'right bottom'."""
        sch = Schematic.load(FIXTURE_SCH)
        vcc = next(lbl for lbl in sch.labels if lbl.text == "VCC")
        assert vcc.effects.justify == "right bottom"

    def test_justify_left_parsed(self):
        """(justify left) is parsed as 'left'."""
        sch = Schematic.load(FIXTURE_SCH)
        gnd = next(gl for gl in sch.global_labels if gl.text == "GND")
        assert gnd.effects.justify == "left"

    def test_justify_right_bottom_survives_round_trip(self):
        """Multi-word justify survives save → reload."""
        sch = Schematic.load(FIXTURE_SCH)
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "out.kicad_sch"
            sch.save(out)
            sch2 = Schematic.load(out)
        vcc = next(lbl for lbl in sch2.labels if lbl.text == "VCC")
        assert vcc.effects.justify == "right bottom"

    def test_justify_serialized_as_multiple_tokens(self):
        """Multi-word justify is serialized with separate tokens."""
        from kiassist_utils.kicad_parser.schematic import Label
        from kiassist_utils.kicad_parser.models import Effects
        from kiassist_utils.kicad_parser.sexpr import serialize
        lbl = Label(text="TEST")
        lbl.effects = Effects(font_size=(1.27, 1.27), justify="right bottom")
        text = serialize(lbl.to_tree())
        assert "(justify right bottom)" in text

    def test_justify_mirror_round_trip(self):
        """(justify left mirror) round-trips correctly."""
        from kiassist_utils.kicad_parser.schematic import Label
        from kiassist_utils.kicad_parser.models import Effects, Position
        from kiassist_utils.kicad_parser.sexpr import parse, serialize
        lbl = Label(text="MIRROR_NET", position=Position(10.0, 10.0, 0.0))
        lbl.effects = Effects(font_size=(1.27, 1.27), justify="left mirror")
        text = serialize(lbl.to_tree())
        lbl2 = Label.from_tree(parse(text))
        assert lbl2.effects.justify == "left mirror"


class TestGeneratorVersion:
    """Tests for generator_version preservation."""

    def test_generator_version_parsed(self):
        """generator_version field is parsed from the fixture."""
        sch = Schematic.load(FIXTURE_SCH)
        assert sch.generator_version == "8.0"

    def test_generator_version_survives_round_trip(self):
        """generator_version survives save → reload."""
        sch = Schematic.load(FIXTURE_SCH)
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "out.kicad_sch"
            sch.save(out)
            sch2 = Schematic.load(out)
        assert sch2.generator_version == "8.0"

    def test_missing_generator_version_not_written(self):
        """Empty generator_version is not written to the file."""
        sch = Schematic()
        sch.generator = "kiassist"
        sch.version = 20231120
        tree = sch._to_tree()
        tags = [item[0] for item in tree if isinstance(item, list) and item]
        assert "generator_version" not in tags


class TestExcludeFromSim:
    """Tests for SchematicSymbol.exclude_from_sim field."""

    def test_exclude_from_sim_parsed(self):
        """exclude_from_sim is parsed from symbols in the fixture."""
        sch = Schematic.load(FIXTURE_SCH)
        r1 = next(s for s in sch.symbols if s.reference == "R1")
        assert r1.exclude_from_sim is False

    def test_exclude_from_sim_default_false(self):
        """exclude_from_sim defaults to False for new symbols."""
        sym = SchematicSymbol()
        assert sym.exclude_from_sim is False

    def test_exclude_from_sim_serialized(self):
        """exclude_from_sim is emitted when raw_tree is None."""
        from kiassist_utils.kicad_parser.sexpr import serialize
        sym = SchematicSymbol()
        sym.lib_id = "Device:R"
        sym.exclude_from_sim = False
        text = serialize(sym.to_tree())
        assert "exclude_from_sim" in text

    def test_exclude_from_sim_true_serialized(self):
        """exclude_from_sim yes is emitted when True."""
        from kiassist_utils.kicad_parser.sexpr import serialize
        sym = SchematicSymbol()
        sym.exclude_from_sim = True
        text = serialize(sym.to_tree())
        assert "exclude_from_sim" in text
        assert "yes" in text


class TestSheetPinPreservation:
    """Tests for Sheet._extra preserving sheet_pin elements."""

    def test_sheet_parsed_from_fixture(self):
        """The fixture sheet is parsed without errors."""
        sch = Schematic.load(FIXTURE_SCH)
        assert len(sch.sheets) == 1

    def test_sheet_pin_preserved_in_extra(self):
        """sheet_pin elements are captured in Sheet._extra."""
        sch = Schematic.load(FIXTURE_SCH)
        sheet = sch.sheets[0]
        pin_items = [item for item in sheet._extra
                     if isinstance(item, list) and item and item[0] == "pin"]
        assert len(pin_items) == 1

    def test_sheet_pin_survives_round_trip(self):
        """sheet_pin elements survive save → reload."""
        sch = Schematic.load(FIXTURE_SCH)
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "out.kicad_sch"
            sch.save(out)
            sch2 = Schematic.load(out)
        sheet = sch2.sheets[0]
        pin_items = [item for item in sheet._extra
                     if isinstance(item, list) and item and item[0] == "pin"]
        assert len(pin_items) == 1

    def test_sheet_properties_parsed(self):
        """Sheet properties (Sheetname, Sheetfile) are parsed."""
        sch = Schematic.load(FIXTURE_SCH)
        sheet = sch.sheets[0]
        assert any(p.key == "Sheetname" for p in sheet.properties)
        assert any(p.key == "Sheetfile" for p in sheet.properties)

    def test_sheet_property_effects_round_trip(self):
        """Sheet property effects (justify, hide) survive round-trip."""
        sch = Schematic.load(FIXTURE_SCH)
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "out.kicad_sch"
            sch.save(out)
            sch2 = Schematic.load(out)
        sheet = sch2.sheets[0]
        sheetfile_prop = next(p for p in sheet.properties if p.key == "Sheetfile")
        assert sheetfile_prop.effects is not None
        assert sheetfile_prop.effects.hide is True


class TestPropertyEffectsRoundTrip:
    """Tests for property effects serialization (hide, justify, bold) in symbols."""

    def test_symbol_hidden_property_stays_hidden_after_add(self):
        """A newly added symbol with a hidden property serializes the hide flag."""
        from kiassist_utils.kicad_parser.models import Effects, Property, Position
        from kiassist_utils.kicad_parser.sexpr import parse, serialize
        sch = Schematic()
        sym = sch.add_symbol("Device:R", 50.0, 50.0, "R99", "10k")
        # Set a hidden Footprint property
        sym.properties.append(Property(
            key="Footprint",
            value="Resistor_SMD:R_0402",
            position=Position(50.0, 50.0, 0.0),
            effects=Effects(font_size=(1.27, 1.27), hide=True),
        ))
        sym.raw_tree = None  # Force re-serialization
        text = serialize(sym.to_tree())
        # Reload the symbol
        parsed = parse(text)
        from kiassist_utils.kicad_parser.schematic import SchematicSymbol
        sym2 = SchematicSymbol.from_tree(parsed)
        fp_prop = next(p for p in sym2.properties if p.key == "Footprint")
        assert fp_prop.effects is not None
        assert fp_prop.effects.hide is True
