"""KiCad PCB (.kicad_pcb) read-only stub model.

Full PCB editing is deferred to a later phase per the execution plan.
This stub provides load/save round-trip support and read-only accessors
for the most commonly needed information (footprints, nets, layer stackup).

Typical usage::

    board = PCBBoard.load("myproject.kicad_pcb")
    for fp in board.footprints:
        print(fp.reference, fp.position)
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .models import Position, KiUUID
from .sexpr import QStr, SExpr, parse, serialize

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


def _parse_position(tree: List[SExpr]) -> Position:
    x = float(tree[1]) if len(tree) > 1 else 0.0
    y = float(tree[2]) if len(tree) > 2 else 0.0
    angle = float(tree[3]) if len(tree) > 3 else 0.0
    return Position(x, y, angle)


# ---------------------------------------------------------------------------
# Lightweight read-only view types
# ---------------------------------------------------------------------------


@dataclass
class PCBNet:
    """A net defined in the PCB file.

    Attributes:
        number: Net ordinal (0 = unconnected).
        name:   Net name string.
    """

    number: int = 0
    name: str = ""


@dataclass
class PCBFootprint:
    """A footprint instance placed on the PCB (read-only view).

    Attributes:
        name:      Footprint library identifier.
        reference: Reference designator (e.g. ``"R1"``).
        value:     Component value string.
        layer:     Primary layer (``"F.Cu"`` or ``"B.Cu"``).
        position:  Placement position and angle.
    """

    name: str = ""
    reference: str = ""
    value: str = ""
    layer: str = "F.Cu"
    position: Position = field(default_factory=lambda: Position(0, 0))


@dataclass
class PCBTrack:
    """A copper track segment on the PCB (read-only view).

    Attributes:
        start:  Start coordinate.
        end:    End coordinate.
        width:  Track width in mm.
        layer:  Copper layer name.
        net:    Net number.
    """

    start: Position = field(default_factory=lambda: Position(0, 0))
    end: Position = field(default_factory=lambda: Position(0, 0))
    width: float = 0.25
    layer: str = "F.Cu"
    net: int = 0


# ---------------------------------------------------------------------------
# PCBBoard (stub)
# ---------------------------------------------------------------------------


@dataclass
class PCBBoard:
    """Read-only model for a KiCad PCB file (.kicad_pcb).

    Full editing support is deferred; this class exposes load/save for
    round-trip fidelity and read-only property accessors for the data
    most commonly needed by AI tools.

    Attributes:
        version:    File format version integer.
        generator:  Name of the tool that last wrote the file.
        nets:       All nets defined in the board.
        footprints: All footprint instances placed on the board.
        tracks:     All copper track segments.
        _raw_tree:  Verbatim parsed S-expression tree (preserved for
                    lossless round-trip save).
    """

    version: int = 0
    generator: str = ""
    nets: List[PCBNet] = field(default_factory=list)
    footprints: List[PCBFootprint] = field(default_factory=list)
    tracks: List[PCBTrack] = field(default_factory=list)
    _raw_tree: Optional[List[SExpr]] = field(default=None, repr=False)

    # ------------------------------------------------------------------
    # Load / save
    # ------------------------------------------------------------------

    @classmethod
    def load(cls, path: str | os.PathLike) -> "PCBBoard":
        """Load a PCB file from *path*.

        Args:
            path: Path to a ``.kicad_pcb`` file.

        Returns:
            A :class:`PCBBoard` populated with read-only views.

        Raises:
            FileNotFoundError: If *path* does not exist.
            ValueError: If the file is not a valid KiCad PCB file.
        """
        text = Path(path).read_text(encoding="utf-8")
        tree = parse(text)
        return cls._from_tree(tree)

    def save(self, path: str | os.PathLike) -> None:
        """Write the PCB back to *path*.

        The raw S-expression tree is serialised unchanged so the output
        is semantically identical to the input (round-trip fidelity).

        Args:
            path: Destination ``.kicad_pcb`` file path.

        Raises:
            RuntimeError: If the board was not loaded from a file.
        """
        if self._raw_tree is None:
            raise RuntimeError("No raw tree available; cannot save a PCBBoard that was not loaded from a file")
        text = serialize(self._raw_tree, indent=0, number_precision=6)
        Path(path).write_text(text + "\n", encoding="utf-8")

    # ------------------------------------------------------------------
    # Read-only accessors
    # ------------------------------------------------------------------

    def get_net(self, name: str) -> Optional[PCBNet]:
        """Return the :class:`PCBNet` with the given *name*, or ``None``."""
        for net in self.nets:
            if net.name == name:
                return net
        return None

    def get_footprint(self, reference: str) -> Optional[PCBFootprint]:
        """Return the :class:`PCBFootprint` with the given *reference*, or ``None``."""
        for fp in self.footprints:
            if fp.reference == reference:
                return fp
        return None

    def get_layer_stackup(self) -> List[str]:
        """Return an ordered list of copper layer names from the board stackup.

        Layers are inferred from tracks and footprints; no stackup section
        is required in the file.

        Returns:
            Unique copper layer names in encountered order.
        """
        layers: list[str] = []
        seen: set[str] = set()
        for track in self.tracks:
            if track.layer not in seen:
                seen.add(track.layer)
                layers.append(track.layer)
        for fp in self.footprints:
            if fp.layer not in seen:
                seen.add(fp.layer)
                layers.append(fp.layer)
        return layers

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @classmethod
    def _from_tree(cls, tree: List[SExpr]) -> "PCBBoard":
        if not tree or tree[0] != "kicad_pcb":
            raise ValueError("Not a valid KiCad PCB file (expected 'kicad_pcb' root tag)")

        board = cls(_raw_tree=tree)

        version_node = _find(tree, "version")
        if version_node and len(version_node) > 1:
            board.version = int(version_node[1])
        gen_node = _find(tree, "generator")
        if gen_node and len(gen_node) > 1:
            board.generator = str(gen_node[1])

        # Nets
        for net_node in _find_all(tree, "net"):
            number = int(net_node[1]) if len(net_node) > 1 else 0
            name = str(net_node[2]) if len(net_node) > 2 else ""
            board.nets.append(PCBNet(number=number, name=name))

        # Footprints
        for fp_node in _find_all(tree, "footprint"):
            fp_name = str(fp_node[1]) if len(fp_node) > 1 else ""
            layer_node = _find(fp_node, "layer")
            layer = str(layer_node[1]) if layer_node and len(layer_node) > 1 else "F.Cu"
            at_node = _find(fp_node, "at")
            pos = _parse_position(at_node) if at_node else Position(0, 0)
            ref = ""
            val = ""
            for prop in _find_all(fp_node, "property"):
                if len(prop) > 2:
                    if str(prop[1]) == "Reference":
                        ref = str(prop[2])
                    elif str(prop[1]) == "Value":
                        val = str(prop[2])
            board.footprints.append(
                PCBFootprint(name=fp_name, reference=ref, value=val, layer=layer, position=pos)
            )

        # Tracks (segment)
        for seg in _find_all(tree, "segment"):
            start_node = _find(seg, "start")
            end_node = _find(seg, "end")
            width_node = _find(seg, "width")
            layer_node = _find(seg, "layer")
            net_node = _find(seg, "net")
            start = _parse_position(["at"] + list(start_node[1:])) if start_node else Position(0, 0)
            end = _parse_position(["at"] + list(end_node[1:])) if end_node else Position(0, 0)
            width = float(width_node[1]) if width_node and len(width_node) > 1 else 0.25
            layer = str(layer_node[1]) if layer_node and len(layer_node) > 1 else "F.Cu"
            net = int(net_node[1]) if net_node and len(net_node) > 1 else 0
            board.tracks.append(PCBTrack(start=start, end=end, width=width, layer=layer, net=net))

        return board
