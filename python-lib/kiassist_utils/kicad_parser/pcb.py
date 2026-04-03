"""KiCad PCB file model (.kicad_pcb) — Stub for future development.

Provides basic load/save and read-only accessors for PCB files.
Full PCB editing is deferred to a later phase per project priorities.
"""

from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any

from .sexpr import (SExpr, parse, serialize, parse_file, serialize_to_file,
                    find_all, find_first, get_value)
from .models import Position, new_uuid


@dataclass
class PCBNet:
    """A net definition in the PCB."""
    number: int = 0
    name: str = ""


@dataclass
class PCBFootprint:
    """A footprint instance on the PCB (read-only summary)."""
    reference: str = ""
    value: str = ""
    footprint: str = ""
    layer: str = ""
    position: Position = field(default_factory=Position)
    pads: List[str] = field(default_factory=list)


@dataclass
class PCBTrack:
    """A track segment on the PCB (read-only summary)."""
    start: Position = field(default_factory=Position)
    end: Position = field(default_factory=Position)
    width: float = 0.0
    layer: str = ""
    net: int = 0


class PCBBoard:
    """KiCad PCB file (.kicad_pcb) — read-only stub.

    Provides basic loading and read-only access to PCB data.
    Full editing capabilities will be added in a future phase.
    """

    def __init__(self):
        self.version: int = 0
        self.generator: str = ""
        self.generator_version: str = ""
        self.uuid: str = ""
        self._raw: SExpr = []
        self._nets: List[PCBNet] = []
        self._footprints: List[PCBFootprint] = []
        self._tracks: List[PCBTrack] = []
        self._layers: List[Dict[str, str]] = []

    @classmethod
    def load(cls, path: str) -> "PCBBoard":
        """Load a PCB from a .kicad_pcb file.

        Args:
            path: Path to the PCB file.

        Returns:
            Parsed PCBBoard object (read-only).
        """
        tree = parse_file(path)
        return cls._from_tree(tree)

    @classmethod
    def from_text(cls, text: str) -> "PCBBoard":
        """Parse a PCB from S-expression text."""
        from .sexpr import parse as sexpr_parse
        tree = sexpr_parse(text)
        return cls._from_tree(tree)

    @classmethod
    def _from_tree(cls, tree: SExpr) -> "PCBBoard":
        pcb = cls()
        pcb._raw = tree

        if not isinstance(tree, list):
            return pcb

        version_val = get_value(tree, "version")
        if version_val is not None:
            pcb.version = int(version_val)

        gen_val = get_value(tree, "generator")
        if gen_val is not None:
            pcb.generator = str(gen_val)

        gen_ver_val = get_value(tree, "generator_version")
        if gen_ver_val is not None:
            pcb.generator_version = str(gen_ver_val)

        uuid_val = get_value(tree, "uuid")
        if uuid_val is not None:
            pcb.uuid = str(uuid_val)

        # Parse nets
        for net_expr in find_all(tree, "net"):
            if len(net_expr) >= 3:
                pcb._nets.append(PCBNet(
                    number=int(net_expr[1]),
                    name=str(net_expr[2])
                ))

        # Parse layers
        layers_expr = find_first(tree, "layers")
        if layers_expr is not None:
            for item in layers_expr[1:]:
                if isinstance(item, list) and len(item) >= 3:
                    pcb._layers.append({
                        "number": str(item[0]),
                        "name": str(item[1]),
                        "type": str(item[2]),
                    })

        # Parse footprint summaries
        for fp_expr in find_all(tree, "footprint"):
            if len(fp_expr) < 2:
                continue
            fp = PCBFootprint()
            fp.footprint = str(fp_expr[1])

            at_expr = find_first(fp_expr, "at")
            if at_expr is not None:
                fp.position = Position.from_sexpr(at_expr)

            layer_val = get_value(fp_expr, "layer")
            if layer_val is not None:
                fp.layer = str(layer_val)

            # Extract reference and value from properties
            for prop_expr in find_all(fp_expr, "property"):
                if len(prop_expr) >= 3:
                    key = str(prop_expr[1])
                    val = str(prop_expr[2])
                    if key == "Reference":
                        fp.reference = val
                    elif key == "Value":
                        fp.value = val

            # Collect pad numbers
            for pad_expr in find_all(fp_expr, "pad"):
                if len(pad_expr) >= 2:
                    fp.pads.append(str(pad_expr[1]))

            pcb._footprints.append(fp)

        # Parse track segments
        for seg_expr in find_all(tree, "segment"):
            track = PCBTrack()
            start_expr = find_first(seg_expr, "start")
            if start_expr is not None and len(start_expr) >= 3:
                track.start = Position(x=float(start_expr[1]),
                                       y=float(start_expr[2]))
            end_expr = find_first(seg_expr, "end")
            if end_expr is not None and len(end_expr) >= 3:
                track.end = Position(x=float(end_expr[1]),
                                     y=float(end_expr[2]))
            width_val = get_value(seg_expr, "width")
            if width_val is not None:
                track.width = float(width_val)
            layer_val = get_value(seg_expr, "layer")
            if layer_val is not None:
                track.layer = str(layer_val)
            net_val = get_value(seg_expr, "net")
            if net_val is not None:
                track.net = int(net_val)
            pcb._tracks.append(track)

        return pcb

    def save(self, path: str) -> None:
        """Save the PCB to a .kicad_pcb file.

        Note: This saves the raw S-expression tree without modifications.
        Full editing is not yet supported.
        """
        if self._raw:
            serialize_to_file(self._raw, path, precision=6)

    @property
    def nets(self) -> List[PCBNet]:
        """Get all nets defined in the PCB."""
        return list(self._nets)

    @property
    def footprints(self) -> List[PCBFootprint]:
        """Get all footprint instances on the PCB."""
        return list(self._footprints)

    @property
    def tracks(self) -> List[PCBTrack]:
        """Get all track segments on the PCB."""
        return list(self._tracks)

    @property
    def layers(self) -> List[Dict[str, str]]:
        """Get all layer definitions."""
        return list(self._layers)

    def get_net_by_name(self, name: str) -> Optional[PCBNet]:
        """Find a net by its name."""
        for net in self._nets:
            if net.name == name:
                return net
        return None

    def get_footprint_by_reference(self, ref: str) -> Optional[PCBFootprint]:
        """Find a footprint by its reference designator."""
        for fp in self._footprints:
            if fp.reference == ref:
                return fp
        return None

    def summary(self) -> Dict[str, Any]:
        """Get a summary of the PCB contents.

        Returns:
            Dictionary with counts of various elements.
        """
        return {
            "version": self.version,
            "generator": self.generator,
            "nets": len(self._nets),
            "footprints": len(self._footprints),
            "tracks": len(self._tracks),
            "layers": len(self._layers),
        }
