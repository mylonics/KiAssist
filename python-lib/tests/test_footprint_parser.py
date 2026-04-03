"""Tests for the KiCad footprint (.kicad_mod) parser."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from kiassist_utils.kicad_parser.footprint import Footprint, Pad

# Path to the bundled fixture footprint
FIXTURE_MOD = Path(__file__).parent / "fixtures" / "test_footprint.kicad_mod"


class TestFootprintLoad:
    """Tests for Footprint.load()."""

    def test_load_fixture_file(self):
        """Loads the fixture file without raising."""
        fp = Footprint.load(FIXTURE_MOD)
        assert fp is not None

    def test_name_parsed(self):
        """Footprint name is parsed correctly."""
        fp = Footprint.load(FIXTURE_MOD)
        assert fp.name == "Resistor_SMD:R_0402_1005Metric"

    def test_layer_parsed(self):
        """Primary layer is parsed."""
        fp = Footprint.load(FIXTURE_MOD)
        assert fp.layer == "F.Cu"

    def test_description_parsed(self):
        """Description string is parsed."""
        fp = Footprint.load(FIXTURE_MOD)
        assert "0402" in fp.description

    def test_tags_parsed(self):
        """Tags string is parsed."""
        fp = Footprint.load(FIXTURE_MOD)
        assert fp.tags == "resistor"

    def test_attributes_parsed(self):
        """Attribute keywords are parsed."""
        fp = Footprint.load(FIXTURE_MOD)
        assert "smd" in fp.attributes

    def test_pads_parsed(self):
        """Pads are parsed."""
        fp = Footprint.load(FIXTURE_MOD)
        assert len(fp.pads) == 2

    def test_pad_numbers(self):
        """Pad numbers are parsed correctly."""
        fp = Footprint.load(FIXTURE_MOD)
        numbers = {p.number for p in fp.pads}
        assert "1" in numbers
        assert "2" in numbers

    def test_pad_type(self):
        """Pad type is parsed."""
        fp = Footprint.load(FIXTURE_MOD)
        for pad in fp.pads:
            assert pad.type == "smd"

    def test_pad_shape(self):
        """Pad shape is parsed."""
        fp = Footprint.load(FIXTURE_MOD)
        for pad in fp.pads:
            assert pad.shape == "roundrect"

    def test_pad_size(self):
        """Pad size is parsed."""
        fp = Footprint.load(FIXTURE_MOD)
        pad1 = next(p for p in fp.pads if p.number == "1")
        assert pad1.size[0] == pytest.approx(0.975)
        assert pad1.size[1] == pytest.approx(0.95)

    def test_pad_layers(self):
        """Pad layer list is parsed."""
        fp = Footprint.load(FIXTURE_MOD)
        pad1 = next(p for p in fp.pads if p.number == "1")
        assert "F.Cu" in pad1.layers
        assert "F.Paste" in pad1.layers
        assert "F.Mask" in pad1.layers

    def test_graphics_parsed(self):
        """Graphic elements are loaded."""
        fp = Footprint.load(FIXTURE_MOD)
        assert len(fp.graphics) > 0

    def test_models_parsed(self):
        """3D model references are loaded."""
        fp = Footprint.load(FIXTURE_MOD)
        assert len(fp.models) == 1

    def test_nonexistent_file_raises(self):
        """Loading a nonexistent file raises FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            Footprint.load("/nonexistent/path.kicad_mod")

    def test_invalid_content_raises(self):
        """Loading invalid content raises ValueError."""
        with tempfile.NamedTemporaryFile(suffix=".kicad_mod", mode="w", delete=False) as f:
            f.write("(not_a_footprint (layer F.Cu))")
            name = f.name
        with pytest.raises(ValueError):
            Footprint.load(name)


class TestFootprintSave:
    """Tests for Footprint.save()."""

    def test_save_creates_file(self):
        """save() creates a file at the given path."""
        fp = Footprint.load(FIXTURE_MOD)
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "out.kicad_mod"
            fp.save(out)
            assert out.exists()

    def test_save_round_trip_name(self):
        """Footprint name survives a save → reload round-trip."""
        fp = Footprint.load(FIXTURE_MOD)
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "out.kicad_mod"
            fp.save(out)
            fp2 = Footprint.load(out)
        assert fp2.name == fp.name

    def test_save_round_trip_pad_count(self):
        """Pad count survives a save → reload round-trip."""
        fp = Footprint.load(FIXTURE_MOD)
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "out.kicad_mod"
            fp.save(out)
            fp2 = Footprint.load(out)
        assert len(fp2.pads) == len(fp.pads)

    def test_save_round_trip_layer(self):
        """Layer survives a save → reload round-trip."""
        fp = Footprint.load(FIXTURE_MOD)
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "out.kicad_mod"
            fp.save(out)
            fp2 = Footprint.load(out)
        assert fp2.layer == fp.layer


class TestFootprintAddPad:
    """Tests for Footprint.add_pad()."""

    def test_add_pad_increases_count(self):
        """add_pad() increases the pad count by 1."""
        fp = Footprint.load(FIXTURE_MOD)
        initial = len(fp.pads)
        fp.add_pad("3", "smd", "rect", 0.0, 2.0, 1.0, 0.5)
        assert len(fp.pads) == initial + 1

    def test_add_pad_attributes(self):
        """Newly added pad has the given attributes."""
        fp = Footprint()
        fp.name = "TestFP"
        pad = fp.add_pad("1", "thru_hole", "circle", 0.0, 0.0, 2.0, 2.0, drill=1.0)
        assert pad.number == "1"
        assert pad.type == "thru_hole"
        assert pad.shape == "circle"
        assert pad.size[0] == pytest.approx(2.0)
        assert pad.drill == pytest.approx(1.0)

    def test_add_pad_default_smd_layers(self):
        """SMD pad gets default front copper layers."""
        fp = Footprint()
        fp.name = "TestFP"
        pad = fp.add_pad("1", "smd", "rect", 0.0, 0.0, 1.0, 1.0)
        assert "F.Cu" in pad.layers
        assert "F.Paste" in pad.layers
        assert "F.Mask" in pad.layers

    def test_add_pad_default_thruhole_layers(self):
        """Thru-hole pad gets default copper layers."""
        fp = Footprint()
        fp.name = "TestFP"
        pad = fp.add_pad("1", "thru_hole", "circle", 0.0, 0.0, 2.0, 2.0, drill=1.0)
        assert "*.Cu" in pad.layers
        assert "*.Mask" in pad.layers

    def test_add_pad_survives_round_trip(self):
        """Added pad survives a save → reload cycle."""
        fp = Footprint.load(FIXTURE_MOD)
        fp.add_pad("3", "smd", "rect", 0.0, 2.0, 0.5, 0.5)
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "out.kicad_mod"
            fp.save(out)
            fp2 = Footprint.load(out)
        assert len(fp2.pads) == 3


class TestFootprintRemovePad:
    """Tests for Footprint.remove_pad()."""

    def test_remove_existing_pad(self):
        """Removing an existing pad decreases count by 1."""
        fp = Footprint.load(FIXTURE_MOD)
        initial = len(fp.pads)
        result = fp.remove_pad("1")
        assert result is True
        assert len(fp.pads) == initial - 1

    def test_remove_nonexistent_pad_returns_false(self):
        """Removing a nonexistent pad number returns False."""
        fp = Footprint.load(FIXTURE_MOD)
        assert fp.remove_pad("99") is False

    def test_removed_pad_absent_after_round_trip(self):
        """Removed pad is absent after save → reload."""
        fp = Footprint.load(FIXTURE_MOD)
        fp.remove_pad("1")
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "out.kicad_mod"
            fp.save(out)
            fp2 = Footprint.load(out)
        pad_numbers = {p.number for p in fp2.pads}
        assert "1" not in pad_numbers


class TestFootprintRenumberPads:
    """Tests for Footprint.renumber_pads()."""

    def test_renumber_pads_sequential(self):
        """renumber_pads() renumbers pads sequentially from 1."""
        fp = Footprint()
        fp.name = "TestFP"
        fp.add_pad("5", "smd", "rect", 0.0, 0.0, 1.0, 1.0)
        fp.add_pad("3", "smd", "rect", 1.0, 0.0, 1.0, 1.0)
        fp.add_pad("7", "smd", "rect", 2.0, 0.0, 1.0, 1.0)
        fp.renumber_pads(start=1)
        numbers = [p.number for p in fp.pads]
        assert numbers == ["1", "2", "3"]

    def test_renumber_custom_start(self):
        """renumber_pads(start=2) starts numbering from 2."""
        fp = Footprint()
        fp.name = "TestFP"
        fp.add_pad("1", "smd", "rect", 0.0, 0.0, 1.0, 1.0)
        fp.add_pad("2", "smd", "rect", 1.0, 0.0, 1.0, 1.0)
        fp.renumber_pads(start=10)
        numbers = [p.number for p in fp.pads]
        assert numbers == ["10", "11"]


class TestFootprintModifyPad:
    """Tests for Footprint.modify_pad()."""

    def test_modify_existing_pad(self):
        """Modifying an existing pad returns True and updates the attribute."""
        fp = Footprint.load(FIXTURE_MOD)
        result = fp.modify_pad("1", size=(1.5, 1.5))
        assert result is True
        pad = next(p for p in fp.pads if p.number == "1")
        assert pad.size == (1.5, 1.5)

    def test_modify_nonexistent_pad(self):
        """Modifying a nonexistent pad returns False."""
        fp = Footprint.load(FIXTURE_MOD)
        assert fp.modify_pad("99", size=(1.0, 1.0)) is False

    def test_modify_pad_clears_raw_tree(self):
        """modify_pad() clears raw_tree so new attributes are serialised."""
        fp = Footprint.load(FIXTURE_MOD)
        fp.modify_pad("1", size=(2.0, 2.0))
        pad = next(p for p in fp.pads if p.number == "1")
        assert pad.raw_tree is None
