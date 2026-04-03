"""KiCad Footprint file model (.kicad_mod).

Provides classes to load, manipulate, and save KiCad footprint files.
"""

from dataclasses import dataclass, field
from typing import List, Optional
from pathlib import Path

from .sexpr import (SExpr, parse, serialize, parse_file, serialize_to_file,
                    find_all, find_first, get_value, set_value, remove_by_tag)
from .models import (Position, Stroke, Effects, Property, Pts, Fill,
                     new_uuid, Color)


@dataclass
class Drill:
    """Drill specification for a through-hole pad."""
    diameter: float = 0.0
    oval: bool = False
    width: float = 0.0
    height: float = 0.0
    offset_x: float = 0.0
    offset_y: float = 0.0

    def to_sexpr(self) -> SExpr:
        result: List[SExpr] = ["drill"]
        if self.oval:
            result.append("oval")
            result.append(self.width)
            result.append(self.height)
        else:
            result.append(self.diameter)
        if self.offset_x != 0.0 or self.offset_y != 0.0:
            result.append(["offset", self.offset_x, self.offset_y])
        return result

    @classmethod
    def from_sexpr(cls, expr: SExpr) -> "Drill":
        if not isinstance(expr, list) or len(expr) < 2:
            return cls()

        oval = "oval" in expr
        diameter = 0.0
        width = 0.0
        height = 0.0
        offset_x = 0.0
        offset_y = 0.0

        # Find numeric values (skip tag and 'oval' keyword)
        nums = [v for v in expr[1:] if isinstance(v, (int, float))]
        if oval and len(nums) >= 2:
            width = float(nums[0])
            height = float(nums[1])
        elif nums:
            diameter = float(nums[0])

        offset_expr = find_first(expr, "offset")
        if offset_expr is not None and len(offset_expr) >= 3:
            offset_x = float(offset_expr[1])
            offset_y = float(offset_expr[2])

        return cls(diameter=diameter, oval=oval, width=width, height=height,
                   offset_x=offset_x, offset_y=offset_y)


@dataclass
class Pad:
    """A pad on a footprint.

    Attributes:
        number: Pad number/name (matches symbol pin number).
        type: Pad type (smd, thru_hole, connect, np_thru_hole).
        shape: Pad shape (circle, rect, oval, trapezoid, roundrect, custom).
        position: Pad position relative to footprint origin.
        size: Pad size [width, height] in mm.
        drill: Drill specification for through-hole pads.
        layers: List of layers the pad exists on.
        roundrect_rratio: Corner radius ratio for roundrect pads.
        net: Net assignment (number, name) — only in PCB context.
        uuid: UUID for the pad.
    """
    number: str = ""
    type: str = "smd"
    shape: str = "rect"
    position: Position = field(default_factory=Position)
    size: List[float] = field(default_factory=lambda: [1.0, 1.0])
    drill: Optional[Drill] = None
    layers: List[str] = field(default_factory=list)
    roundrect_rratio: float = 0.0
    net: Optional[List] = None
    uuid: str = ""
    _other_items: List[SExpr] = field(default_factory=list)

    def to_sexpr(self) -> SExpr:
        result: List[SExpr] = ["pad", self.number, self.type, self.shape,
                                self.position.to_sexpr(),
                                ["size", self.size[0], self.size[1]]]
        if self.drill is not None:
            result.append(self.drill.to_sexpr())
        if self.layers:
            layers_expr: List[SExpr] = ["layers"]
            layers_expr.extend(self.layers)
            result.append(layers_expr)
        if self.roundrect_rratio > 0:
            result.append(["roundrect_rratio", self.roundrect_rratio])
        if self.net is not None:
            result.append(self.net)
        if self.uuid:
            result.append(["uuid", self.uuid])
        for item in self._other_items:
            result.append(item)
        return result

    @classmethod
    def from_sexpr(cls, expr: SExpr) -> "Pad":
        if not isinstance(expr, list) or len(expr) < 4:
            return cls()

        number = str(expr[1])
        pad_type = str(expr[2])
        shape = str(expr[3])
        position = Position()
        size = [1.0, 1.0]
        drill = None
        layers = []
        roundrect_rratio = 0.0
        net = None
        uuid_str = ""
        other_items = []

        at_expr = find_first(expr, "at")
        if at_expr is not None:
            position = Position.from_sexpr(at_expr)

        size_expr = find_first(expr, "size")
        if size_expr is not None and len(size_expr) >= 3:
            size = [float(size_expr[1]), float(size_expr[2])]

        drill_expr = find_first(expr, "drill")
        if drill_expr is not None:
            drill = Drill.from_sexpr(drill_expr)

        layers_expr = find_first(expr, "layers")
        if layers_expr is not None:
            layers = [str(l) for l in layers_expr[1:] if isinstance(l, str)]

        rr_val = get_value(expr, "roundrect_rratio")
        if rr_val is not None:
            roundrect_rratio = float(rr_val)

        net_expr = find_first(expr, "net")
        if net_expr is not None:
            net = net_expr

        uuid_val = get_value(expr, "uuid")
        if uuid_val is not None:
            uuid_str = str(uuid_val)

        _known_tags = {"pad", "at", "size", "drill", "layers",
                       "roundrect_rratio", "net", "uuid"}
        for item in expr[4:]:
            if isinstance(item, list) and item:
                tag = item[0] if isinstance(item[0], str) else None
                if tag and tag not in _known_tags:
                    other_items.append(item)

        return cls(number=number, type=pad_type, shape=shape,
                   position=position, size=size, drill=drill,
                   layers=layers, roundrect_rratio=roundrect_rratio,
                   net=net, uuid=uuid_str, _other_items=other_items)


@dataclass
class FootprintGraphic:
    """A graphic element within a footprint (fp_text, fp_line, fp_rect, etc.)."""
    raw: SExpr = field(default_factory=list)

    @property
    def type(self) -> str:
        if isinstance(self.raw, list) and self.raw:
            return str(self.raw[0])
        return ""

    def to_sexpr(self) -> SExpr:
        return self.raw

    @classmethod
    def from_sexpr(cls, expr: SExpr) -> "FootprintGraphic":
        return cls(raw=expr)


@dataclass
class Model3D:
    """A 3D model reference for a footprint."""
    path: str = ""
    offset: List[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])
    scale: List[float] = field(default_factory=lambda: [1.0, 1.0, 1.0])
    rotate: List[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])

    def to_sexpr(self) -> SExpr:
        return [
            "model", self.path,
            ["offset", ["xyz", self.offset[0], self.offset[1], self.offset[2]]],
            ["scale", ["xyz", self.scale[0], self.scale[1], self.scale[2]]],
            ["rotate", ["xyz", self.rotate[0], self.rotate[1], self.rotate[2]]],
        ]

    @classmethod
    def from_sexpr(cls, expr: SExpr) -> "Model3D":
        if not isinstance(expr, list) or len(expr) < 2:
            return cls()

        path = str(expr[1])
        offset = [0.0, 0.0, 0.0]
        scale = [1.0, 1.0, 1.0]
        rotate = [0.0, 0.0, 0.0]

        def _parse_xyz(tag_expr):
            xyz = find_first(tag_expr, "xyz")
            if xyz is not None and len(xyz) >= 4:
                return [float(xyz[1]), float(xyz[2]), float(xyz[3])]
            return None

        offset_expr = find_first(expr, "offset")
        if offset_expr is not None:
            vals = _parse_xyz(offset_expr)
            if vals:
                offset = vals

        scale_expr = find_first(expr, "scale")
        if scale_expr is not None:
            vals = _parse_xyz(scale_expr)
            if vals:
                scale = vals

        rotate_expr = find_first(expr, "rotate")
        if rotate_expr is not None:
            vals = _parse_xyz(rotate_expr)
            if vals:
                rotate = vals

        return cls(path=path, offset=offset, scale=scale, rotate=rotate)


class Footprint:
    """KiCad footprint file (.kicad_mod).

    Provides methods to load, manipulate, and save KiCad footprint files.
    """

    def __init__(self):
        self.name: str = ""
        self.version: int = 20240215
        self.generator: str = "kiassist"
        self.generator_version: str = ""
        self.layer: str = "F.Cu"
        self.description: str = ""
        self.tags: str = ""
        self.attr: List[str] = []
        self.pads: List[Pad] = []
        self.graphics: List[FootprintGraphic] = []
        self.models: List[Model3D] = []
        self.properties: List[Property] = []
        self._other_items: List[SExpr] = []

    @classmethod
    def load(cls, path: str) -> "Footprint":
        """Load a footprint from a .kicad_mod file."""
        tree = parse_file(path)
        return cls._from_tree(tree)

    @classmethod
    def from_text(cls, text: str) -> "Footprint":
        """Parse a footprint from S-expression text."""
        tree = parse(text)
        return cls._from_tree(tree)

    @classmethod
    def _from_tree(cls, tree: SExpr) -> "Footprint":
        fp = cls()

        if not isinstance(tree, list) or len(tree) < 2:
            return fp

        # Footprint name is the second element
        if isinstance(tree[1], str):
            fp.name = tree[1]

        version_val = get_value(tree, "version")
        if version_val is not None:
            fp.version = int(version_val)

        gen_val = get_value(tree, "generator")
        if gen_val is not None:
            fp.generator = str(gen_val)

        gen_ver_val = get_value(tree, "generator_version")
        if gen_ver_val is not None:
            fp.generator_version = str(gen_ver_val)

        layer_val = get_value(tree, "layer")
        if layer_val is not None:
            fp.layer = str(layer_val)

        descr_val = get_value(tree, "descr")
        if descr_val is not None:
            fp.description = str(descr_val)

        tags_val = get_value(tree, "tags")
        if tags_val is not None:
            fp.tags = str(tags_val)

        attr_expr = find_first(tree, "attr")
        if attr_expr is not None:
            fp.attr = [str(a) for a in attr_expr[1:] if isinstance(a, str)]

        _graphic_tags = {"fp_text", "fp_line", "fp_rect", "fp_circle",
                         "fp_arc", "fp_poly"}
        _known_tags = {"footprint", "version", "generator", "generator_version",
                       "layer", "descr", "tags", "attr", "pad", "model",
                       "property"} | _graphic_tags

        for item in tree[2:]:
            if not isinstance(item, list) or not item:
                continue
            tag = item[0] if isinstance(item[0], str) else None
            if tag == "pad":
                fp.pads.append(Pad.from_sexpr(item))
            elif tag in _graphic_tags:
                fp.graphics.append(FootprintGraphic.from_sexpr(item))
            elif tag == "model":
                fp.models.append(Model3D.from_sexpr(item))
            elif tag == "property":
                fp.properties.append(Property.from_sexpr(item))
            elif tag not in _known_tags:
                fp._other_items.append(item)

        return fp

    def to_sexpr(self) -> SExpr:
        tree: List[SExpr] = ["footprint", self.name]

        tree.append(["version", self.version])
        tree.append(["generator", self.generator])
        if self.generator_version:
            tree.append(["generator_version", self.generator_version])
        tree.append(["layer", self.layer])

        if self.description:
            tree.append(["descr", self.description])
        if self.tags:
            tree.append(["tags", self.tags])

        if self.attr:
            attr_expr: List[SExpr] = ["attr"]
            attr_expr.extend(self.attr)
            tree.append(attr_expr)

        for prop in self.properties:
            tree.append(prop.to_sexpr())

        for g in self.graphics:
            tree.append(g.to_sexpr())

        for pad in self.pads:
            tree.append(pad.to_sexpr())

        for model in self.models:
            tree.append(model.to_sexpr())

        for item in self._other_items:
            tree.append(item)

        return tree

    def save(self, path: str) -> None:
        """Save the footprint to a .kicad_mod file."""
        tree = self.to_sexpr()
        serialize_to_file(tree, path, precision=6)

    def serialize(self) -> str:
        """Serialize the footprint to S-expression text."""
        return serialize(self.to_sexpr(), precision=6)

    # --- Manipulation Methods ---

    def add_pad(self, number: str, pad_type: str = "smd",
                shape: str = "rect", position: Optional[Position] = None,
                size: Optional[List[float]] = None,
                layers: Optional[List[str]] = None,
                drill: Optional[Drill] = None) -> Pad:
        """Add a new pad to the footprint.

        Args:
            number: Pad number/name.
            pad_type: Pad type (smd, thru_hole, connect, np_thru_hole).
            shape: Pad shape.
            position: Pad position.
            size: Pad size [width, height].
            layers: Layers for the pad.
            drill: Drill specification for through-hole pads.

        Returns:
            The newly created Pad.
        """
        pad = Pad(
            number=number,
            type=pad_type,
            shape=shape,
            position=position or Position(),
            size=size or [1.0, 1.0],
            layers=layers or (["F.Cu", "F.Paste", "F.Mask"] if pad_type == "smd"
                              else ["*.Cu", "*.Mask"]),
            drill=drill,
            uuid=new_uuid(),
        )
        self.pads.append(pad)
        return pad

    def remove_pad(self, number: str) -> Optional[Pad]:
        """Remove a pad by its number.

        Args:
            number: Pad number to remove.

        Returns:
            The removed pad, or None if not found.
        """
        for i, pad in enumerate(self.pads):
            if pad.number == number:
                return self.pads.pop(i)
        return None

    def renumber_pads(self, start: int = 1) -> None:
        """Renumber all pads sequentially starting from a given number.

        Args:
            start: Starting pad number.
        """
        for i, pad in enumerate(self.pads):
            pad.number = str(start + i)

    def modify_pad(self, number: str, **kwargs) -> Optional[Pad]:
        """Modify a pad's attributes.

        Args:
            number: Pad number to modify.
            **kwargs: Attributes to update.

        Returns:
            The modified pad, or None if not found.
        """
        for pad in self.pads:
            if pad.number == number:
                for key, value in kwargs.items():
                    if hasattr(pad, key):
                        setattr(pad, key, value)
                return pad
        return None
