"""Tests for the KiCad PCB (.kicad_pcb) read-only stub (pcb.py)."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from kiassist_utils.kicad_parser.pcb import PCBBoard, PCBNet, PCBFootprint, PCBTrack

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
