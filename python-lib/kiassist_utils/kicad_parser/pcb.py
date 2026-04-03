"""KiCad PCB (.kicad_pcb) model with full editing support.

Provides load/save round-trip plus read/write operations for footprints,
nets, track segments, and vias.

Typical usage::

    board = PCBBoard.load("myproject.kicad_pcb")
    net = board.add_net("PWR_3V3")
    board.add_footprint("Resistor_SMD:R_0402_1005Metric", "R2", "10k", "F.Cu", 120.0, 100.0)
    board.add_track(100.0, 100.0, 110.0, 100.0, "F.Cu", 0.25, "VCC")
    board.save("myproject.kicad_pcb")
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .models import Position, KiUUID
from .sexpr import QStr, SExpr, parse, serialize
from ._helpers import _find, _find_all, _parse_position


# Default text offsets for Reference/Value properties on newly created footprints.
# These match KiCad's built-in default text placements (in mm).
_FP_REF_Y_OFFSET = -1.43
_FP_VAL_Y_OFFSET = 1.43


# ---------------------------------------------------------------------------
# Model types
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
    """A footprint instance placed on the PCB.

    Attributes:
        name:      Footprint library identifier (e.g. ``"Resistor_SMD:R_0402"``).
        reference: Reference designator (e.g. ``"R1"``).
        value:     Component value string.
        layer:     Primary layer (``"F.Cu"`` or ``"B.Cu"``).
        position:  Placement position and angle.
        uuid:      KiCad UUID.
        raw_tree:  Original S-expression (preserved for lossless pass-through).
    """

    name: str = ""
    reference: str = ""
    value: str = ""
    layer: str = "F.Cu"
    position: Position = field(default_factory=lambda: Position(0, 0))
    uuid: KiUUID = field(default_factory=KiUUID)
    raw_tree: Optional[List[SExpr]] = field(default=None, repr=False)

    def to_tree(self) -> List[SExpr]:
        """Serialise back to an S-expression list."""
        if self.raw_tree is not None:
            return self.raw_tree
        tree: List[SExpr] = [
            "footprint",
            QStr(self.name),
            ["layer", QStr(self.layer)],
        ]
        if self.uuid:
            tree.append(["uuid", QStr(self.uuid.value)])
        tree.append(["at", self.position.x, self.position.y, self.position.angle])
        tree.append(["property", QStr("Reference"), QStr(self.reference),
                     ["at", 0.0, _FP_REF_Y_OFFSET, 0.0], ["layer", QStr("F.SilkS")]])
        tree.append(["property", QStr("Value"), QStr(self.value),
                     ["at", 0.0, _FP_VAL_Y_OFFSET, 0.0], ["layer", QStr("F.Fab")]])
        return tree


@dataclass
class PCBTrack:
    """A copper track segment on the PCB.

    Attributes:
        start:  Start coordinate.
        end:    End coordinate.
        width:  Track width in mm.
        layer:  Copper layer name.
        net:    Net number.
        uuid:   KiCad UUID.
        raw_tree: Original S-expression (preserved for lossless pass-through).
    """

    start: Position = field(default_factory=lambda: Position(0, 0))
    end: Position = field(default_factory=lambda: Position(0, 0))
    width: float = 0.25
    layer: str = "F.Cu"
    net: int = 0
    uuid: KiUUID = field(default_factory=KiUUID)
    raw_tree: Optional[List[SExpr]] = field(default=None, repr=False)

    def to_tree(self) -> List[SExpr]:
        """Serialise back to an S-expression list."""
        if self.raw_tree is not None:
            return self.raw_tree
        tree: List[SExpr] = [
            "segment",
            ["start", self.start.x, self.start.y],
            ["end", self.end.x, self.end.y],
            ["width", self.width],
            ["layer", QStr(self.layer)],
            ["net", self.net],
        ]
        if self.uuid:
            tree.append(["uuid", QStr(self.uuid.value)])
        return tree


@dataclass
class PCBVia:
    """A copper via on the PCB.

    Attributes:
        position:   Via centre position.
        drill:      Drill diameter in mm.
        size:       Pad diameter in mm.
        layer_from: Top copper layer.
        layer_to:   Bottom copper layer.
        net:        Net number.
        uuid:       KiCad UUID.
        raw_tree:   Original S-expression (preserved for lossless pass-through).
    """

    position: Position = field(default_factory=lambda: Position(0, 0))
    drill: float = 0.8
    size: float = 1.6
    layer_from: str = "F.Cu"
    layer_to: str = "B.Cu"
    net: int = 0
    uuid: KiUUID = field(default_factory=KiUUID)
    raw_tree: Optional[List[SExpr]] = field(default=None, repr=False)

    def to_tree(self) -> List[SExpr]:
        """Serialise back to an S-expression list."""
        if self.raw_tree is not None:
            return self.raw_tree
        tree: List[SExpr] = [
            "via",
            ["at", self.position.x, self.position.y],
            ["size", self.size],
            ["drill", self.drill],
            ["layers", QStr(self.layer_from), QStr(self.layer_to)],
            ["net", self.net],
        ]
        if self.uuid:
            tree.append(["uuid", QStr(self.uuid.value)])
        return tree


# ---------------------------------------------------------------------------
# PCBBoard
# ---------------------------------------------------------------------------


@dataclass
class PCBBoard:
    """Model for a KiCad PCB file (.kicad_pcb).

    Supports load/save round-trip and full editing operations.

    Attributes:
        version:    File format version integer.
        generator:  Name of the tool that last wrote the file.
        nets:       All nets defined in the board.
        footprints: All footprint instances placed on the board.
        tracks:     All copper track segments.
        vias:       All copper vias.
        _raw_tree:  Verbatim parsed S-expression tree.  Updated in-place when
                    items are added or removed so that ``save()`` always writes
                    a valid file.
    """

    version: int = 0
    generator: str = ""
    nets: List[PCBNet] = field(default_factory=list)
    footprints: List[PCBFootprint] = field(default_factory=list)
    tracks: List[PCBTrack] = field(default_factory=list)
    vias: List[PCBVia] = field(default_factory=list)
    _raw_tree: Optional[List[SExpr]] = field(default=None, repr=False)

    # ------------------------------------------------------------------
    # Factory / load / save
    # ------------------------------------------------------------------

    @classmethod
    def new(cls) -> "PCBBoard":
        """Create a new, empty :class:`PCBBoard` ready for editing.

        Returns:
            An empty board with a minimal valid S-expression tree.
        """
        raw_tree: List[SExpr] = [
            "kicad_pcb",
            ["version", 20231120],
            ["generator", QStr("pcbnew")],
            ["generator_version", QStr("8.0")],
            ["general", ["thickness", 1.6]],
            ["paper", QStr("A4")],
            ["layers",
             [0, QStr("F.Cu"), "signal"],
             [31, QStr("B.Cu"), "signal"],
             ],
            ["net", 0, QStr("")],
        ]
        board = cls(_raw_tree=raw_tree, version=20231120, generator="pcbnew")
        board.nets.append(PCBNet(number=0, name=""))
        return board

    @classmethod
    def load(cls, path: str | os.PathLike) -> "PCBBoard":
        """Load a PCB file from *path*.

        Args:
            path: Path to a ``.kicad_pcb`` file.

        Returns:
            A :class:`PCBBoard` populated from the file.

        Raises:
            FileNotFoundError: If *path* does not exist.
            ValueError: If the file is not a valid KiCad PCB file.
        """
        text = Path(path).read_text(encoding="utf-8")
        tree = parse(text)
        return cls._from_tree(tree)

    def save(self, path: str | os.PathLike) -> None:
        """Write the PCB to *path*.

        All edits made via :meth:`add_net`, :meth:`add_footprint`,
        :meth:`remove_footprint`, :meth:`add_track`, and :meth:`add_via` are
        reflected in the output.

        Args:
            path: Destination ``.kicad_pcb`` file path.

        Raises:
            RuntimeError: If the board was not loaded or created via
                          :meth:`load` / :meth:`new`.
        """
        if self._raw_tree is None:
            raise RuntimeError(
                "No raw tree available; use PCBBoard.load() or PCBBoard.new()"
            )
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
        """Return an ordered list of copper layer names from the board.

        Layers are inferred from the ``(layers …)`` section, with fallback to
        tracks and footprints when no layers section is present.

        Returns:
            Unique copper layer names in file order.
        """
        layers: list[str] = []
        seen: set[str] = set()
        if self._raw_tree is not None:
            layers_node = _find(self._raw_tree, "layers")
            if layers_node:
                for item in layers_node[1:]:
                    if isinstance(item, list) and len(item) >= 2:
                        name = str(item[1])
                        if name not in seen:
                            seen.add(name)
                            layers.append(name)
                if layers:
                    return layers
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
    # Editing methods
    # ------------------------------------------------------------------

    def add_net(self, name: str) -> PCBNet:
        """Add a new net to the board.

        Args:
            name: Net name string (e.g. ``"VCC"``).

        Returns:
            The newly created :class:`PCBNet`.

        Raises:
            ValueError: If a net with the same name already exists.
        """
        if self.get_net(name) is not None:
            raise ValueError(f"Net '{name}' already exists")
        number = max((n.number for n in self.nets), default=-1) + 1
        net = PCBNet(number=number, name=name)
        self.nets.append(net)
        if self._raw_tree is not None:
            self._raw_tree.append(["net", number, QStr(name)])
        return net

    def add_footprint(
        self,
        name: str,
        reference: str,
        value: str,
        layer: str = "F.Cu",
        x: float = 0.0,
        y: float = 0.0,
        angle: float = 0.0,
    ) -> PCBFootprint:
        """Place a new footprint on the board.

        Args:
            name:      Footprint library identifier (e.g.
                       ``"Resistor_SMD:R_0402_1005Metric"``).
            reference: Reference designator (e.g. ``"R2"``).
            value:     Component value string.
            layer:     Primary layer (``"F.Cu"`` or ``"B.Cu"``).
            x, y:      Position in mm.
            angle:     Rotation angle in degrees.

        Returns:
            The newly created :class:`PCBFootprint`.
        """
        fp = PCBFootprint(
            name=name,
            reference=reference,
            value=value,
            layer=layer,
            position=Position(x, y, angle),
            uuid=KiUUID.new(),
        )
        fp_tree = fp.to_tree()
        fp.raw_tree = fp_tree
        self.footprints.append(fp)
        if self._raw_tree is not None:
            self._raw_tree.append(fp_tree)
        return fp

    def remove_footprint(self, reference: str) -> bool:
        """Remove the first footprint whose reference equals *reference*.

        Args:
            reference: Reference designator to remove (e.g. ``"R1"``).

        Returns:
            ``True`` if a footprint was removed, ``False`` if not found.
        """
        for i, fp in enumerate(self.footprints):
            if fp.reference == reference:
                if self._raw_tree is not None and fp.raw_tree is not None:
                    try:
                        self._raw_tree.remove(fp.raw_tree)
                    except ValueError:
                        pass
                del self.footprints[i]
                return True
        return False

    def add_track(
        self,
        x1: float,
        y1: float,
        x2: float,
        y2: float,
        layer: str = "F.Cu",
        width: float = 0.25,
        net: int | str = 0,
    ) -> PCBTrack:
        """Add a copper track segment.

        Args:
            x1, y1: Start coordinate in mm.
            x2, y2: End coordinate in mm.
            layer:  Copper layer (e.g. ``"F.Cu"``).
            width:  Track width in mm.
            net:    Net number or net name string.

        Returns:
            The newly created :class:`PCBTrack`.
        """
        net_number: int
        if isinstance(net, str):
            net_obj = self.get_net(net)
            net_number = net_obj.number if net_obj else 0
        else:
            net_number = int(net)

        track = PCBTrack(
            start=Position(x1, y1),
            end=Position(x2, y2),
            width=width,
            layer=layer,
            net=net_number,
            uuid=KiUUID.new(),
        )
        track_tree = track.to_tree()
        track.raw_tree = track_tree
        self.tracks.append(track)
        if self._raw_tree is not None:
            self._raw_tree.append(track_tree)
        return track

    def add_via(
        self,
        x: float,
        y: float,
        net: int | str = 0,
        drill: float = 0.8,
        size: float = 1.6,
        layer_from: str = "F.Cu",
        layer_to: str = "B.Cu",
    ) -> PCBVia:
        """Add a copper via.

        Args:
            x, y:       Via centre position in mm.
            net:        Net number or net name string.
            drill:      Drill diameter in mm.
            size:       Pad diameter in mm.
            layer_from: Top layer (default ``"F.Cu"``).
            layer_to:   Bottom layer (default ``"B.Cu"``).

        Returns:
            The newly created :class:`PCBVia`.
        """
        net_number: int
        if isinstance(net, str):
            net_obj = self.get_net(net)
            net_number = net_obj.number if net_obj else 0
        else:
            net_number = int(net)

        via = PCBVia(
            position=Position(x, y),
            drill=drill,
            size=size,
            layer_from=layer_from,
            layer_to=layer_to,
            net=net_number,
            uuid=KiUUID.new(),
        )
        via_tree = via.to_tree()
        via.raw_tree = via_tree
        self.vias.append(via)
        if self._raw_tree is not None:
            self._raw_tree.append(via_tree)
        return via

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
            uuid_node = _find(fp_node, "uuid")
            fp_uuid = KiUUID(str(uuid_node[1])) if uuid_node and len(uuid_node) > 1 else KiUUID()
            board.footprints.append(
                PCBFootprint(
                    name=fp_name,
                    reference=ref,
                    value=val,
                    layer=layer,
                    position=pos,
                    uuid=fp_uuid,
                    raw_tree=fp_node,
                )
            )

        # Track segments
        for seg in _find_all(tree, "segment"):
            start_node = _find(seg, "start")
            end_node = _find(seg, "end")
            width_node = _find(seg, "width")
            layer_node = _find(seg, "layer")
            net_node = _find(seg, "net")
            uuid_node = _find(seg, "uuid")
            start = _parse_position(["at"] + list(start_node[1:])) if start_node else Position(0, 0)
            end = _parse_position(["at"] + list(end_node[1:])) if end_node else Position(0, 0)
            width = float(width_node[1]) if width_node and len(width_node) > 1 else 0.25
            layer = str(layer_node[1]) if layer_node and len(layer_node) > 1 else "F.Cu"
            net = int(net_node[1]) if net_node and len(net_node) > 1 else 0
            track_uuid = KiUUID(str(uuid_node[1])) if uuid_node and len(uuid_node) > 1 else KiUUID()
            board.tracks.append(
                PCBTrack(
                    start=start,
                    end=end,
                    width=width,
                    layer=layer,
                    net=net,
                    uuid=track_uuid,
                    raw_tree=seg,
                )
            )

        # Vias
        for via_node in _find_all(tree, "via"):
            at_node = _find(via_node, "at")
            pos = _parse_position(at_node) if at_node else Position(0, 0)
            size_node = _find(via_node, "size")
            drill_node = _find(via_node, "drill")
            layers_node = _find(via_node, "layers")
            net_node = _find(via_node, "net")
            uuid_node = _find(via_node, "uuid")
            size = float(size_node[1]) if size_node and len(size_node) > 1 else 1.6
            drill = float(drill_node[1]) if drill_node and len(drill_node) > 1 else 0.8
            layer_from = str(layers_node[1]) if layers_node and len(layers_node) > 1 else "F.Cu"
            layer_to = str(layers_node[2]) if layers_node and len(layers_node) > 2 else "B.Cu"
            net = int(net_node[1]) if net_node and len(net_node) > 1 else 0
            via_uuid = KiUUID(str(uuid_node[1])) if uuid_node and len(uuid_node) > 1 else KiUUID()
            board.vias.append(
                PCBVia(
                    position=pos,
                    drill=drill,
                    size=size,
                    layer_from=layer_from,
                    layer_to=layer_to,
                    net=net,
                    uuid=via_uuid,
                    raw_tree=via_node,
                )
            )

        return board
