"""Tests for the KiCad PCB (.kicad_pcb) model (pcb.py)."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from kiassist_utils.kicad_parser.pcb import PCBBoard, PCBNet, PCBFootprint, PCBTrack, PCBVia

# Path to the bundled fixture PCB file
FIXTURE_PCB = Path(__file__).parent / "fixtures" / "test_pcb.kicad_pcb"


class TestPCBBoardLoad:
    """Tests for PCBBoard.load()."""

    def test_load_fixture_file(self):
        """Loads the fixture file without raising."""
        board = PCBBoard.load(FIXTURE_PCB)
        assert board is not None

    def test_version_parsed(self):
        """File format version is parsed."""
        board = PCBBoard.load(FIXTURE_PCB)
        assert board.version == 20231120

    def test_generator_parsed(self):
        """Generator string is parsed."""
        board = PCBBoard.load(FIXTURE_PCB)
        assert board.generator == "pcbnew"

    def test_nets_parsed(self):
        """Nets are parsed."""
        board = PCBBoard.load(FIXTURE_PCB)
        assert len(board.nets) >= 2

    def test_net_names(self):
        """Net names are parsed correctly."""
        board = PCBBoard.load(FIXTURE_PCB)
        names = {n.name for n in board.nets}
        assert "VCC" in names
        assert "GND" in names

    def test_net_numbers(self):
        """Net numbers are parsed correctly."""
        board = PCBBoard.load(FIXTURE_PCB)
        vcc = board.get_net("VCC")
        assert vcc is not None
        assert vcc.number == 1

    def test_footprints_parsed(self):
        """Footprints are parsed."""
        board = PCBBoard.load(FIXTURE_PCB)
        assert len(board.footprints) == 1

    def test_footprint_reference(self):
        """Footprint reference is parsed."""
        board = PCBBoard.load(FIXTURE_PCB)
        assert board.footprints[0].reference == "R1"

    def test_footprint_value(self):
        """Footprint value is parsed."""
        board = PCBBoard.load(FIXTURE_PCB)
        assert board.footprints[0].value == "10k"

    def test_footprint_layer(self):
        """Footprint primary layer is parsed."""
        board = PCBBoard.load(FIXTURE_PCB)
        assert board.footprints[0].layer == "F.Cu"

    def test_footprint_position(self):
        """Footprint position is parsed."""
        board = PCBBoard.load(FIXTURE_PCB)
        fp = board.footprints[0]
        assert fp.position.x == pytest.approx(100.0)
        assert fp.position.y == pytest.approx(100.0)

    def test_tracks_parsed(self):
        """Track segments are parsed."""
        board = PCBBoard.load(FIXTURE_PCB)
        assert len(board.tracks) == 1

    def test_track_layer(self):
        """Track layer is parsed."""
        board = PCBBoard.load(FIXTURE_PCB)
        assert board.tracks[0].layer == "F.Cu"

    def test_track_width(self):
        """Track width is parsed."""
        board = PCBBoard.load(FIXTURE_PCB)
        assert board.tracks[0].width == pytest.approx(0.25)

    def test_nonexistent_file_raises(self):
        """Loading a nonexistent file raises FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            PCBBoard.load("/nonexistent/path.kicad_pcb")

    def test_invalid_content_raises(self):
        """Loading invalid content raises ValueError."""
        with tempfile.NamedTemporaryFile(suffix=".kicad_pcb", mode="w", delete=False) as f:
            f.write("(not_a_pcb (version 1))")
            name = f.name
        with pytest.raises(ValueError):
            PCBBoard.load(name)


class TestPCBBoardSave:
    """Tests for PCBBoard.save() (round-trip)."""

    def test_save_creates_file(self):
        """save() creates a file at the given path."""
        board = PCBBoard.load(FIXTURE_PCB)
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "out.kicad_pcb"
            board.save(out)
            assert out.exists()

    def test_save_round_trip_nets(self):
        """Net count survives a save → reload round-trip."""
        board = PCBBoard.load(FIXTURE_PCB)
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "out.kicad_pcb"
            board.save(out)
            board2 = PCBBoard.load(out)
        assert len(board2.nets) == len(board.nets)

    def test_save_round_trip_footprints(self):
        """Footprint count survives a save → reload round-trip."""
        board = PCBBoard.load(FIXTURE_PCB)
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "out.kicad_pcb"
            board.save(out)
            board2 = PCBBoard.load(out)
        assert len(board2.footprints) == len(board.footprints)

    def test_save_round_trip_tracks(self):
        """Track count survives a save → reload round-trip."""
        board = PCBBoard.load(FIXTURE_PCB)
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "out.kicad_pcb"
            board.save(out)
            board2 = PCBBoard.load(out)
        assert len(board2.tracks) == len(board.tracks)

    def test_save_without_raw_tree_raises(self):
        """save() raises RuntimeError when _raw_tree is None."""
        board = PCBBoard()
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "out.kicad_pcb"
            with pytest.raises(RuntimeError):
                board.save(out)


class TestPCBBoardAccessors:
    """Tests for read-only accessor methods."""

    def test_get_net_found(self):
        """get_net() returns the correct PCBNet."""
        board = PCBBoard.load(FIXTURE_PCB)
        net = board.get_net("VCC")
        assert net is not None
        assert net.name == "VCC"
        assert net.number == 1

    def test_get_net_not_found(self):
        """get_net() returns None for an unknown net."""
        board = PCBBoard.load(FIXTURE_PCB)
        assert board.get_net("NONEXISTENT") is None

    def test_get_footprint_found(self):
        """get_footprint() returns the correct footprint."""
        board = PCBBoard.load(FIXTURE_PCB)
        fp = board.get_footprint("R1")
        assert fp is not None
        assert fp.reference == "R1"

    def test_get_footprint_not_found(self):
        """get_footprint() returns None for an unknown reference."""
        board = PCBBoard.load(FIXTURE_PCB)
        assert board.get_footprint("U99") is None

    def test_get_layer_stackup(self):
        """get_layer_stackup() returns a list of copper layer names."""
        board = PCBBoard.load(FIXTURE_PCB)
        stackup = board.get_layer_stackup()
        assert isinstance(stackup, list)
        assert "F.Cu" in stackup

    def test_get_layer_stackup_no_duplicates(self):
        """get_layer_stackup() does not return duplicate layer names."""
        board = PCBBoard.load(FIXTURE_PCB)
        stackup = board.get_layer_stackup()
        assert len(stackup) == len(set(stackup))


class TestPCBBoardNew:
    """Tests for PCBBoard.new() factory and fresh board editing."""

    def test_new_returns_board(self):
        """new() returns a PCBBoard with a raw tree."""
        board = PCBBoard.new()
        assert board is not None
        assert board._raw_tree is not None

    def test_new_has_unconnected_net(self):
        """new() board has the unconnected net (0, '')."""
        board = PCBBoard.new()
        assert len(board.nets) >= 1
        assert board.nets[0].number == 0

    def test_new_can_save(self):
        """A newly created board can be saved without errors."""
        board = PCBBoard.new()
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "out.kicad_pcb"
            board.save(out)
            assert out.exists()

    def test_new_save_load_round_trip(self):
        """A newly created board saves and reloads correctly."""
        board = PCBBoard.new()
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "out.kicad_pcb"
            board.save(out)
            board2 = PCBBoard.load(out)
        assert board2.version == 20231120


class TestPCBBoardEditNet:
    """Tests for PCBBoard.add_net()."""

    def test_add_net_increases_count(self):
        """add_net() adds a new net to the board."""
        board = PCBBoard.load(FIXTURE_PCB)
        initial = len(board.nets)
        board.add_net("PWR_3V3")
        assert len(board.nets) == initial + 1

    def test_add_net_returns_pcb_net(self):
        """add_net() returns the new PCBNet."""
        board = PCBBoard.load(FIXTURE_PCB)
        net = board.add_net("PWR_3V3")
        assert isinstance(net, PCBNet)
        assert net.name == "PWR_3V3"

    def test_add_net_number_increments(self):
        """add_net() assigns the next available number."""
        board = PCBBoard.load(FIXTURE_PCB)
        max_before = max(n.number for n in board.nets)
        net = board.add_net("NEW_NET")
        assert net.number == max_before + 1

    def test_add_net_duplicate_raises(self):
        """add_net() raises ValueError for a duplicate name."""
        board = PCBBoard.load(FIXTURE_PCB)
        with pytest.raises(ValueError, match="already exists"):
            board.add_net("VCC")

    def test_add_net_survives_round_trip(self):
        """Added net persists after save → reload."""
        board = PCBBoard.load(FIXTURE_PCB)
        board.add_net("PWR_3V3")
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "out.kicad_pcb"
            board.save(out)
            board2 = PCBBoard.load(out)
        names = {n.name for n in board2.nets}
        assert "PWR_3V3" in names


class TestPCBBoardEditFootprint:
    """Tests for PCBBoard.add_footprint() and remove_footprint()."""

    def test_add_footprint_increases_count(self):
        """add_footprint() adds a new footprint."""
        board = PCBBoard.load(FIXTURE_PCB)
        initial = len(board.footprints)
        board.add_footprint("Resistor_SMD:R_0402_1005Metric", "R2", "4k7", "F.Cu", 120.0, 100.0)
        assert len(board.footprints) == initial + 1

    def test_add_footprint_returns_footprint(self):
        """add_footprint() returns the new PCBFootprint."""
        board = PCBBoard.load(FIXTURE_PCB)
        fp = board.add_footprint("Resistor_SMD:R_0402_1005Metric", "R2", "4k7", "F.Cu", 120.0, 100.0)
        assert isinstance(fp, PCBFootprint)
        assert fp.reference == "R2"
        assert fp.value == "4k7"
        assert fp.layer == "F.Cu"
        assert fp.position.x == pytest.approx(120.0)

    def test_add_footprint_survives_round_trip(self):
        """Added footprint persists after save → reload."""
        board = PCBBoard.load(FIXTURE_PCB)
        board.add_footprint("Resistor_SMD:R_0402_1005Metric", "R2", "4k7", "F.Cu", 120.0, 100.0)
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "out.kicad_pcb"
            board.save(out)
            board2 = PCBBoard.load(out)
        refs = [fp.reference for fp in board2.footprints]
        assert "R2" in refs

    def test_remove_footprint_decreases_count(self):
        """remove_footprint() removes a footprint."""
        board = PCBBoard.load(FIXTURE_PCB)
        initial = len(board.footprints)
        result = board.remove_footprint("R1")
        assert result is True
        assert len(board.footprints) == initial - 1

    def test_remove_footprint_not_found_returns_false(self):
        """remove_footprint() returns False for unknown reference."""
        board = PCBBoard.load(FIXTURE_PCB)
        assert board.remove_footprint("U99") is False

    def test_remove_footprint_gone_after_round_trip(self):
        """Removed footprint is absent after save → reload."""
        board = PCBBoard.load(FIXTURE_PCB)
        board.remove_footprint("R1")
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "out.kicad_pcb"
            board.save(out)
            board2 = PCBBoard.load(out)
        refs = [fp.reference for fp in board2.footprints]
        assert "R1" not in refs


class TestPCBBoardEditTrack:
    """Tests for PCBBoard.add_track()."""

    def test_add_track_increases_count(self):
        """add_track() adds a new track segment."""
        board = PCBBoard.load(FIXTURE_PCB)
        initial = len(board.tracks)
        board.add_track(110.0, 100.0, 120.0, 100.0, "F.Cu", 0.25, "VCC")
        assert len(board.tracks) == initial + 1

    def test_add_track_returns_track(self):
        """add_track() returns the new PCBTrack."""
        board = PCBBoard.load(FIXTURE_PCB)
        track = board.add_track(110.0, 100.0, 120.0, 100.0, "F.Cu", 0.25)
        assert isinstance(track, PCBTrack)
        assert track.start.x == pytest.approx(110.0)
        assert track.end.x == pytest.approx(120.0)
        assert track.layer == "F.Cu"
        assert track.width == pytest.approx(0.25)

    def test_add_track_by_net_name(self):
        """add_track() resolves net name to number."""
        board = PCBBoard.load(FIXTURE_PCB)
        track = board.add_track(110.0, 100.0, 120.0, 100.0, "F.Cu", 0.25, "VCC")
        vcc = board.get_net("VCC")
        assert vcc is not None
        assert track.net == vcc.number

    def test_add_track_survives_round_trip(self):
        """Added track persists after save → reload."""
        board = PCBBoard.load(FIXTURE_PCB)
        board.add_track(110.0, 100.0, 120.0, 100.0, "F.Cu", 0.25)
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "out.kicad_pcb"
            board.save(out)
            board2 = PCBBoard.load(out)
        assert len(board2.tracks) == len(board.tracks)


class TestPCBBoardEditVia:
    """Tests for PCBBoard.add_via()."""

    def test_add_via_increases_count(self):
        """add_via() adds a new via."""
        board = PCBBoard.load(FIXTURE_PCB)
        initial = len(board.vias)
        board.add_via(115.0, 100.0, "VCC")
        assert len(board.vias) == initial + 1

    def test_add_via_returns_via(self):
        """add_via() returns the new PCBVia."""
        board = PCBBoard.load(FIXTURE_PCB)
        via = board.add_via(115.0, 100.0)
        assert isinstance(via, PCBVia)
        assert via.position.x == pytest.approx(115.0)

    def test_add_via_by_net_name(self):
        """add_via() resolves net name to number."""
        board = PCBBoard.load(FIXTURE_PCB)
        via = board.add_via(115.0, 100.0, "GND")
        gnd = board.get_net("GND")
        assert gnd is not None
        assert via.net == gnd.number

    def test_add_via_survives_round_trip(self):
        """Added via persists after save → reload."""
        board = PCBBoard.load(FIXTURE_PCB)
        board.add_via(115.0, 100.0, "VCC")
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "out.kicad_pcb"
            board.save(out)
            board2 = PCBBoard.load(out)
        assert len(board2.vias) == 1
