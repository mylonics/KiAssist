"""KiCad Schematic file model (.kicad_sch).

Provides classes to load, manipulate, and save KiCad schematic files.
Supports KiCad 6/7/8 format.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any
from pathlib import Path

from .sexpr import (SExpr, parse, serialize, parse_file, serialize_to_file,
                    find_all, find_first, get_value, set_value, remove_by_tag)
from .models import (Position, Stroke, Effects, Property, Pts, Fill,
                     new_uuid, Color)


@dataclass
class Wire:
    """A wire in the schematic connecting two points."""
    pts: Pts = field(default_factory=Pts)
    stroke: Stroke = field(default_factory=Stroke)
    uuid: str = ""

    def to_sexpr(self) -> SExpr:
        result: List[SExpr] = ["wire", self.pts.to_sexpr(), self.stroke.to_sexpr()]
        if self.uuid:
            result.append(["uuid", self.uuid])
        return result

    @classmethod
    def from_sexpr(cls, expr: SExpr) -> "Wire":
        if not isinstance(expr, list):
            return cls()
        pts = Pts()
        stroke = Stroke()
        uuid_str = ""

        pts_expr = find_first(expr, "pts")
        if pts_expr is not None:
            pts = Pts.from_sexpr(pts_expr)

        stroke_expr = find_first(expr, "stroke")
        if stroke_expr is not None:
            stroke = Stroke.from_sexpr(stroke_expr)

        uuid_val = get_value(expr, "uuid")
        if uuid_val is not None:
            uuid_str = str(uuid_val)

        return cls(pts=pts, stroke=stroke, uuid=uuid_str)


@dataclass
class Bus:
    """A bus in the schematic."""
    pts: Pts = field(default_factory=Pts)
    stroke: Stroke = field(default_factory=Stroke)
    uuid: str = ""

    def to_sexpr(self) -> SExpr:
        result: List[SExpr] = ["bus", self.pts.to_sexpr(), self.stroke.to_sexpr()]
        if self.uuid:
            result.append(["uuid", self.uuid])
        return result

    @classmethod
    def from_sexpr(cls, expr: SExpr) -> "Bus":
        if not isinstance(expr, list):
            return cls()
        pts = Pts()
        stroke = Stroke()
        uuid_str = ""

        pts_expr = find_first(expr, "pts")
        if pts_expr is not None:
            pts = Pts.from_sexpr(pts_expr)

        stroke_expr = find_first(expr, "stroke")
        if stroke_expr is not None:
            stroke = Stroke.from_sexpr(stroke_expr)

        uuid_val = get_value(expr, "uuid")
        if uuid_val is not None:
            uuid_str = str(uuid_val)

        return cls(pts=pts, stroke=stroke, uuid=uuid_str)


@dataclass
class Junction:
    """A junction point in the schematic."""
    position: Position = field(default_factory=Position)
    diameter: float = 0.0
    color: Optional[Color] = None
    uuid: str = ""

    def to_sexpr(self) -> SExpr:
        result: List[SExpr] = ["junction", self.position.to_sexpr(),
                                ["diameter", self.diameter]]
        if self.color is not None:
            result.append(self.color.to_sexpr())
        if self.uuid:
            result.append(["uuid", self.uuid])
        return result

    @classmethod
    def from_sexpr(cls, expr: SExpr) -> "Junction":
        if not isinstance(expr, list):
            return cls()
        position = Position()
        diameter = 0.0
        color = None
        uuid_str = ""

        at_expr = find_first(expr, "at")
        if at_expr is not None:
            position = Position.from_sexpr(at_expr)

        diameter_val = get_value(expr, "diameter")
        if diameter_val is not None:
            diameter = float(diameter_val)

        color_expr = find_first(expr, "color")
        if color_expr is not None:
            color = Color.from_sexpr(color_expr)

        uuid_val = get_value(expr, "uuid")
        if uuid_val is not None:
            uuid_str = str(uuid_val)

        return cls(position=position, diameter=diameter, color=color, uuid=uuid_str)


@dataclass
class NoConnect:
    """A no-connect marker."""
    position: Position = field(default_factory=Position)
    uuid: str = ""

    def to_sexpr(self) -> SExpr:
        result: List[SExpr] = ["no_connect", self.position.to_sexpr()]
        if self.uuid:
            result.append(["uuid", self.uuid])
        return result

    @classmethod
    def from_sexpr(cls, expr: SExpr) -> "NoConnect":
        if not isinstance(expr, list):
            return cls()
        position = Position()
        uuid_str = ""

        at_expr = find_first(expr, "at")
        if at_expr is not None:
            position = Position.from_sexpr(at_expr)

        uuid_val = get_value(expr, "uuid")
        if uuid_val is not None:
            uuid_str = str(uuid_val)

        return cls(position=position, uuid=uuid_str)


@dataclass
class BusEntry:
    """A bus entry element."""
    position: Position = field(default_factory=Position)
    size: List[float] = field(default_factory=lambda: [2.54, 2.54])
    stroke: Stroke = field(default_factory=Stroke)
    uuid: str = ""

    def to_sexpr(self) -> SExpr:
        result: List[SExpr] = [
            "bus_entry",
            self.position.to_sexpr(),
            ["size", self.size[0], self.size[1]],
            self.stroke.to_sexpr(),
        ]
        if self.uuid:
            result.append(["uuid", self.uuid])
        return result

    @classmethod
    def from_sexpr(cls, expr: SExpr) -> "BusEntry":
        if not isinstance(expr, list):
            return cls()
        position = Position()
        size = [2.54, 2.54]
        stroke = Stroke()
        uuid_str = ""

        at_expr = find_first(expr, "at")
        if at_expr is not None:
            position = Position.from_sexpr(at_expr)

        size_expr = find_first(expr, "size")
        if size_expr is not None and len(size_expr) >= 3:
            size = [float(size_expr[1]), float(size_expr[2])]

        stroke_expr = find_first(expr, "stroke")
        if stroke_expr is not None:
            stroke = Stroke.from_sexpr(stroke_expr)

        uuid_val = get_value(expr, "uuid")
        if uuid_val is not None:
            uuid_str = str(uuid_val)

        return cls(position=position, size=size, stroke=stroke, uuid=uuid_str)


@dataclass
class Label:
    """A net label in the schematic."""
    text: str = ""
    position: Position = field(default_factory=Position)
    effects: Effects = field(default_factory=Effects)
    uuid: str = ""
    fields_autoplaced: bool = False

    def to_sexpr(self) -> SExpr:
        result: List[SExpr] = ["label", self.text, self.position.to_sexpr()]
        if self.fields_autoplaced:
            result.append(["fields_autoplaced", "yes"])
        result.append(self.effects.to_sexpr())
        if self.uuid:
            result.append(["uuid", self.uuid])
        return result

    @classmethod
    def from_sexpr(cls, expr: SExpr) -> "Label":
        if not isinstance(expr, list) or len(expr) < 2:
            return cls()
        text = str(expr[1]) if len(expr) > 1 else ""
        position = Position()
        effects = Effects()
        uuid_str = ""
        fields_autoplaced = False

        at_expr = find_first(expr, "at")
        if at_expr is not None:
            position = Position.from_sexpr(at_expr)

        effects_expr = find_first(expr, "effects")
        if effects_expr is not None:
            effects = Effects.from_sexpr(effects_expr)

        uuid_val = get_value(expr, "uuid")
        if uuid_val is not None:
            uuid_str = str(uuid_val)

        fap = find_first(expr, "fields_autoplaced")
        if fap is not None:
            fields_autoplaced = True

        return cls(text=text, position=position, effects=effects,
                   uuid=uuid_str, fields_autoplaced=fields_autoplaced)


@dataclass
class GlobalLabel:
    """A global label in the schematic."""
    text: str = ""
    shape: str = "input"
    position: Position = field(default_factory=Position)
    effects: Effects = field(default_factory=Effects)
    uuid: str = ""
    fields_autoplaced: bool = False
    properties: List[Property] = field(default_factory=list)

    def to_sexpr(self) -> SExpr:
        result: List[SExpr] = ["global_label", self.text,
                                ["shape", self.shape],
                                self.position.to_sexpr()]
        if self.fields_autoplaced:
            result.append(["fields_autoplaced", "yes"])
        result.append(self.effects.to_sexpr())
        if self.uuid:
            result.append(["uuid", self.uuid])
        for prop in self.properties:
            result.append(prop.to_sexpr())
        return result

    @classmethod
    def from_sexpr(cls, expr: SExpr) -> "GlobalLabel":
        if not isinstance(expr, list) or len(expr) < 2:
            return cls()
        text = str(expr[1])
        shape = "input"
        position = Position()
        effects = Effects()
        uuid_str = ""
        fields_autoplaced = False
        properties = []

        shape_val = get_value(expr, "shape")
        if shape_val is not None:
            shape = str(shape_val)

        at_expr = find_first(expr, "at")
        if at_expr is not None:
            position = Position.from_sexpr(at_expr)

        effects_expr = find_first(expr, "effects")
        if effects_expr is not None:
            effects = Effects.from_sexpr(effects_expr)

        uuid_val = get_value(expr, "uuid")
        if uuid_val is not None:
            uuid_str = str(uuid_val)

        fap = find_first(expr, "fields_autoplaced")
        if fap is not None:
            fields_autoplaced = True

        for item in expr:
            if (isinstance(item, list) and len(item) > 0 and
                    isinstance(item[0], str) and item[0] == "property"):
                properties.append(Property.from_sexpr(item))

        return cls(text=text, shape=shape, position=position, effects=effects,
                   uuid=uuid_str, fields_autoplaced=fields_autoplaced,
                   properties=properties)


@dataclass
class HierarchicalLabel:
    """A hierarchical label in the schematic."""
    text: str = ""
    shape: str = "input"
    position: Position = field(default_factory=Position)
    effects: Effects = field(default_factory=Effects)
    uuid: str = ""
    fields_autoplaced: bool = False

    def to_sexpr(self) -> SExpr:
        result: List[SExpr] = ["hierarchical_label", self.text,
                                ["shape", self.shape],
                                self.position.to_sexpr()]
        if self.fields_autoplaced:
            result.append(["fields_autoplaced", "yes"])
        result.append(self.effects.to_sexpr())
        if self.uuid:
            result.append(["uuid", self.uuid])
        return result

    @classmethod
    def from_sexpr(cls, expr: SExpr) -> "HierarchicalLabel":
        if not isinstance(expr, list) or len(expr) < 2:
            return cls()
        text = str(expr[1])
        shape = "input"
        position = Position()
        effects = Effects()
        uuid_str = ""
        fields_autoplaced = False

        shape_val = get_value(expr, "shape")
        if shape_val is not None:
            shape = str(shape_val)

        at_expr = find_first(expr, "at")
        if at_expr is not None:
            position = Position.from_sexpr(at_expr)

        effects_expr = find_first(expr, "effects")
        if effects_expr is not None:
            effects = Effects.from_sexpr(effects_expr)

        uuid_val = get_value(expr, "uuid")
        if uuid_val is not None:
            uuid_str = str(uuid_val)

        fap = find_first(expr, "fields_autoplaced")
        if fap is not None:
            fields_autoplaced = True

        return cls(text=text, shape=shape, position=position, effects=effects,
                   uuid=uuid_str, fields_autoplaced=fields_autoplaced)


@dataclass
class SheetPin:
    """A pin on a hierarchical sheet."""
    name: str = ""
    shape: str = "input"
    position: Position = field(default_factory=Position)
    effects: Effects = field(default_factory=Effects)
    uuid: str = ""

    def to_sexpr(self) -> SExpr:
        result: List[SExpr] = ["pin", self.name, self.shape,
                                self.position.to_sexpr()]
        result.append(self.effects.to_sexpr())
        if self.uuid:
            result.append(["uuid", self.uuid])
        return result

    @classmethod
    def from_sexpr(cls, expr: SExpr) -> "SheetPin":
        if not isinstance(expr, list) or len(expr) < 3:
            return cls()
        name = str(expr[1])
        shape = str(expr[2])
        position = Position()
        effects = Effects()
        uuid_str = ""

        at_expr = find_first(expr, "at")
        if at_expr is not None:
            position = Position.from_sexpr(at_expr)

        effects_expr = find_first(expr, "effects")
        if effects_expr is not None:
            effects = Effects.from_sexpr(effects_expr)

        uuid_val = get_value(expr, "uuid")
        if uuid_val is not None:
            uuid_str = str(uuid_val)

        return cls(name=name, shape=shape, position=position,
                   effects=effects, uuid=uuid_str)


@dataclass
class Sheet:
    """A hierarchical sheet reference in the schematic."""
    position: Position = field(default_factory=Position)
    size: List[float] = field(default_factory=lambda: [25.4, 25.4])
    fields_autoplaced: bool = False
    stroke: Stroke = field(default_factory=Stroke)
    fill: Fill = field(default_factory=Fill)
    uuid: str = ""
    properties: List[Property] = field(default_factory=list)
    pins: List[SheetPin] = field(default_factory=list)
    instances: List[SExpr] = field(default_factory=list)

    def to_sexpr(self) -> SExpr:
        result: List[SExpr] = [
            "sheet",
            self.position.to_sexpr(),
            ["size", self.size[0], self.size[1]],
        ]
        if self.fields_autoplaced:
            result.append(["fields_autoplaced", "yes"])
        result.append(self.stroke.to_sexpr())
        result.append(self.fill.to_sexpr())
        if self.uuid:
            result.append(["uuid", self.uuid])
        for prop in self.properties:
            result.append(prop.to_sexpr())
        for pin in self.pins:
            result.append(pin.to_sexpr())
        for inst in self.instances:
            result.append(inst)
        return result

    @classmethod
    def from_sexpr(cls, expr: SExpr) -> "Sheet":
        if not isinstance(expr, list):
            return cls()
        position = Position()
        size = [25.4, 25.4]
        fields_autoplaced = False
        stroke = Stroke()
        fill_obj = Fill()
        uuid_str = ""
        properties = []
        pins = []
        instances = []

        at_expr = find_first(expr, "at")
        if at_expr is not None:
            position = Position.from_sexpr(at_expr)

        size_expr = find_first(expr, "size")
        if size_expr is not None and len(size_expr) >= 3:
            size = [float(size_expr[1]), float(size_expr[2])]

        fap = find_first(expr, "fields_autoplaced")
        if fap is not None:
            fields_autoplaced = True

        stroke_expr = find_first(expr, "stroke")
        if stroke_expr is not None:
            stroke = Stroke.from_sexpr(stroke_expr)

        fill_expr = find_first(expr, "fill")
        if fill_expr is not None:
            fill_obj = Fill.from_sexpr(fill_expr)

        uuid_val = get_value(expr, "uuid")
        if uuid_val is not None:
            uuid_str = str(uuid_val)

        for item in expr:
            if not isinstance(item, list) or len(item) == 0:
                continue
            tag = item[0] if isinstance(item[0], str) else None
            if tag == "property":
                properties.append(Property.from_sexpr(item))
            elif tag == "pin":
                pins.append(SheetPin.from_sexpr(item))
            elif tag == "instances":
                instances.append(item)

        return cls(position=position, size=size,
                   fields_autoplaced=fields_autoplaced, stroke=stroke,
                   fill=fill_obj, uuid=uuid_str, properties=properties,
                   pins=pins, instances=instances)


@dataclass
class LibSymbol:
    """A library symbol definition embedded in the schematic."""
    raw: SExpr = field(default_factory=list)

    @property
    def name(self) -> str:
        if isinstance(self.raw, list) and len(self.raw) >= 2:
            return str(self.raw[1])
        return ""

    @classmethod
    def from_sexpr(cls, expr: SExpr) -> "LibSymbol":
        return cls(raw=expr)

    def to_sexpr(self) -> SExpr:
        return self.raw


@dataclass
class SchematicSymbol:
    """A placed symbol instance in the schematic."""
    lib_id: str = ""
    lib_name: str = ""
    position: Position = field(default_factory=Position)
    mirror: str = ""
    unit: int = 1
    exclude_from_sim: bool = False
    in_bom: bool = True
    on_board: bool = True
    dnp: bool = False
    fields_autoplaced: bool = False
    uuid: str = ""
    properties: List[Property] = field(default_factory=list)
    pin_uuids: Dict[str, str] = field(default_factory=dict)
    instances: List[SExpr] = field(default_factory=list)

    @property
    def reference(self) -> str:
        """Get the reference designator (e.g., 'R1', 'U2')."""
        for prop in self.properties:
            if prop.key == "Reference":
                return prop.value
        return ""

    @property
    def value(self) -> str:
        """Get the value (e.g., '10k', 'STM32F407')."""
        for prop in self.properties:
            if prop.key == "Value":
                return prop.value
        return ""

    @property
    def footprint(self) -> str:
        """Get the footprint name."""
        for prop in self.properties:
            if prop.key == "Footprint":
                return prop.value
        return ""

    def get_property(self, key: str) -> Optional[str]:
        """Get a property value by key."""
        for prop in self.properties:
            if prop.key == key:
                return prop.value
        return None

    def set_property(self, key: str, value: str) -> None:
        """Set a property value by key, creating it if it doesn't exist."""
        for prop in self.properties:
            if prop.key == key:
                prop.value = value
                return
        self.properties.append(Property(key=key, value=value))

    def to_sexpr(self) -> SExpr:
        result: List[SExpr] = ["symbol"]
        if self.lib_id:
            result.append(["lib_id", self.lib_id])
        if self.lib_name:
            result.append(["lib_name", self.lib_name])
        result.append(self.position.to_sexpr())
        if self.mirror:
            result.append(["mirror", self.mirror])
        result.append(["unit", self.unit])
        if self.exclude_from_sim:
            result.append(["exclude_from_sim", "yes"])
        result.append(["in_bom", "yes" if self.in_bom else "no"])
        result.append(["on_board", "yes" if self.on_board else "no"])
        if self.dnp:
            result.append(["dnp", "yes"])
        if self.fields_autoplaced:
            result.append(["fields_autoplaced", "yes"])
        if self.uuid:
            result.append(["uuid", self.uuid])
        for prop in self.properties:
            result.append(prop.to_sexpr())
        for pin_name, pin_uuid in self.pin_uuids.items():
            result.append(["pin", pin_name, ["uuid", pin_uuid]])
        for inst in self.instances:
            result.append(inst)
        return result

    @classmethod
    def from_sexpr(cls, expr: SExpr) -> "SchematicSymbol":
        if not isinstance(expr, list):
            return cls()

        lib_id = ""
        lib_name = ""
        position = Position()
        mirror = ""
        unit = 1
        exclude_from_sim = False
        in_bom = True
        on_board = True
        dnp = False
        fields_autoplaced = False
        uuid_str = ""
        properties = []
        pin_uuids = {}
        instances = []

        lib_id_val = get_value(expr, "lib_id")
        if lib_id_val is not None:
            lib_id = str(lib_id_val)

        lib_name_val = get_value(expr, "lib_name")
        if lib_name_val is not None:
            lib_name = str(lib_name_val)

        at_expr = find_first(expr, "at")
        if at_expr is not None:
            position = Position.from_sexpr(at_expr)

        mirror_val = get_value(expr, "mirror")
        if mirror_val is not None:
            mirror = str(mirror_val)

        unit_val = get_value(expr, "unit")
        if unit_val is not None:
            unit = int(unit_val)

        esim_val = get_value(expr, "exclude_from_sim")
        if esim_val is not None:
            exclude_from_sim = str(esim_val) == "yes"

        in_bom_val = get_value(expr, "in_bom")
        if in_bom_val is not None:
            in_bom = str(in_bom_val) == "yes"

        on_board_val = get_value(expr, "on_board")
        if on_board_val is not None:
            on_board = str(on_board_val) == "yes"

        dnp_val = get_value(expr, "dnp")
        if dnp_val is not None:
            dnp = str(dnp_val) == "yes"

        fap = find_first(expr, "fields_autoplaced")
        if fap is not None:
            fields_autoplaced = True

        uuid_val = get_value(expr, "uuid")
        if uuid_val is not None:
            uuid_str = str(uuid_val)

        for item in expr:
            if not isinstance(item, list) or len(item) == 0:
                continue
            tag = item[0] if isinstance(item[0], str) else None
            if tag == "property":
                properties.append(Property.from_sexpr(item))
            elif tag == "pin":
                if len(item) >= 2:
                    pin_name = str(item[1])
                    pin_uuid_val = get_value(item, "uuid")
                    if pin_uuid_val is not None:
                        pin_uuids[pin_name] = str(pin_uuid_val)
            elif tag == "instances":
                instances.append(item)

        return cls(
            lib_id=lib_id, lib_name=lib_name, position=position,
            mirror=mirror, unit=unit, exclude_from_sim=exclude_from_sim,
            in_bom=in_bom, on_board=on_board, dnp=dnp,
            fields_autoplaced=fields_autoplaced, uuid=uuid_str,
            properties=properties, pin_uuids=pin_uuids, instances=instances
        )


class Schematic:
    """KiCad schematic file (.kicad_sch).

    Provides methods to load, manipulate, and save KiCad schematic files.
    """

    def __init__(self):
        self.version: int = 20240215
        self.generator: str = "kiassist"
        self.generator_version: str = ""
        self.uuid: str = new_uuid()
        self.paper: str = "A4"
        self.title_block: SExpr = []
        self.lib_symbols: List[LibSymbol] = []
        self.symbols: List[SchematicSymbol] = []
        self.wires: List[Wire] = []
        self.buses: List[Bus] = []
        self.junctions: List[Junction] = []
        self.no_connects: List[NoConnect] = []
        self.bus_entries: List[BusEntry] = []
        self.labels: List[Label] = []
        self.global_labels: List[GlobalLabel] = []
        self.hierarchical_labels: List[HierarchicalLabel] = []
        self.sheets: List[Sheet] = []
        self.sheet_instances: List[SExpr] = []
        self.symbol_instances: List[SExpr] = []
        # Store unknown/unhandled items for round-trip fidelity
        self._other_items: List[SExpr] = []

    @classmethod
    def load(cls, path: str) -> "Schematic":
        """Load a schematic from a .kicad_sch file.

        Args:
            path: Path to the schematic file.

        Returns:
            Parsed Schematic object.
        """
        tree = parse_file(path)
        return cls._from_tree(tree)

    @classmethod
    def from_text(cls, text: str) -> "Schematic":
        """Parse a schematic from S-expression text.

        Args:
            text: S-expression text.

        Returns:
            Parsed Schematic object.
        """
        tree = parse(text)
        return cls._from_tree(tree)

    @classmethod
    def _from_tree(cls, tree: SExpr) -> "Schematic":
        """Build a Schematic from a parsed S-expression tree."""
        sch = cls()

        if not isinstance(tree, list) or not tree:
            return sch

        # Parse top-level fields
        version_val = get_value(tree, "version")
        if version_val is not None:
            sch.version = int(version_val)

        gen_val = get_value(tree, "generator")
        if gen_val is not None:
            sch.generator = str(gen_val)

        gen_ver_val = get_value(tree, "generator_version")
        if gen_ver_val is not None:
            sch.generator_version = str(gen_ver_val)

        uuid_val = get_value(tree, "uuid")
        if uuid_val is not None:
            sch.uuid = str(uuid_val)

        paper_expr = find_first(tree, "paper")
        if paper_expr is not None and len(paper_expr) >= 2:
            sch.paper = str(paper_expr[1])

        title_expr = find_first(tree, "title_block")
        if title_expr is not None:
            sch.title_block = title_expr

        # Parse lib_symbols
        lib_syms_expr = find_first(tree, "lib_symbols")
        if lib_syms_expr is not None:
            for item in lib_syms_expr[1:]:
                if isinstance(item, list) and item and item[0] == "symbol":
                    sch.lib_symbols.append(LibSymbol.from_sexpr(item))

        # Parse items
        _tag_handlers = {
            "symbol": lambda item: sch.symbols.append(
                SchematicSymbol.from_sexpr(item)),
            "wire": lambda item: sch.wires.append(Wire.from_sexpr(item)),
            "bus": lambda item: sch.buses.append(Bus.from_sexpr(item)),
            "junction": lambda item: sch.junctions.append(
                Junction.from_sexpr(item)),
            "no_connect": lambda item: sch.no_connects.append(
                NoConnect.from_sexpr(item)),
            "bus_entry": lambda item: sch.bus_entries.append(
                BusEntry.from_sexpr(item)),
            "label": lambda item: sch.labels.append(Label.from_sexpr(item)),
            "global_label": lambda item: sch.global_labels.append(
                GlobalLabel.from_sexpr(item)),
            "hierarchical_label": lambda item: sch.hierarchical_labels.append(
                HierarchicalLabel.from_sexpr(item)),
            "sheet": lambda item: sch.sheets.append(Sheet.from_sexpr(item)),
            "sheet_instances": lambda item: sch.sheet_instances.append(item),
            "symbol_instances": lambda item: sch.symbol_instances.append(item),
        }

        # Skip known top-level non-item tags
        _skip_tags = {"kicad_sch", "version", "generator", "generator_version",
                       "uuid", "paper", "title_block", "lib_symbols"}

        for item in tree:
            if not isinstance(item, list) or not item:
                continue
            tag = item[0] if isinstance(item[0], str) else None
            if tag is None or tag in _skip_tags:
                continue
            handler = _tag_handlers.get(tag)
            if handler:
                handler(item)
            else:
                sch._other_items.append(item)

        return sch

    def to_sexpr(self) -> SExpr:
        """Convert the schematic back to an S-expression tree."""
        tree: List[SExpr] = ["kicad_sch",
                              ["version", self.version],
                              ["generator", self.generator]]
        if self.generator_version:
            tree.append(["generator_version", self.generator_version])
        tree.append(["uuid", self.uuid])
        tree.append(["paper", self.paper])

        if self.title_block:
            tree.append(self.title_block)

        # lib_symbols
        if self.lib_symbols:
            lib_syms: List[SExpr] = ["lib_symbols"]
            for ls in self.lib_symbols:
                lib_syms.append(ls.to_sexpr())
            tree.append(lib_syms)

        # Schematic items
        for junction in self.junctions:
            tree.append(junction.to_sexpr())
        for nc in self.no_connects:
            tree.append(nc.to_sexpr())
        for be in self.bus_entries:
            tree.append(be.to_sexpr())
        for wire in self.wires:
            tree.append(wire.to_sexpr())
        for bus in self.buses:
            tree.append(bus.to_sexpr())
        for label in self.labels:
            tree.append(label.to_sexpr())
        for gl in self.global_labels:
            tree.append(gl.to_sexpr())
        for hl in self.hierarchical_labels:
            tree.append(hl.to_sexpr())

        # Symbols
        for sym in self.symbols:
            tree.append(sym.to_sexpr())

        # Sheets
        for sheet in self.sheets:
            tree.append(sheet.to_sexpr())

        # Instances
        for si in self.sheet_instances:
            tree.append(si)
        for si in self.symbol_instances:
            tree.append(si)

        # Preserve unknown items
        for item in self._other_items:
            tree.append(item)

        return tree

    def save(self, path: str) -> None:
        """Save the schematic to a .kicad_sch file.

        Args:
            path: Output file path.
        """
        tree = self.to_sexpr()
        serialize_to_file(tree, path, precision=4)

    def serialize(self) -> str:
        """Serialize the schematic to S-expression text.

        Returns:
            S-expression text.
        """
        tree = self.to_sexpr()
        return serialize(tree, precision=4)

    # --- Manipulation Methods ---

    def add_symbol(self, lib_id: str, position: Position,
                   reference: str = "", value: str = "",
                   footprint: str = "", unit: int = 1) -> SchematicSymbol:
        """Add a new symbol to the schematic.

        Args:
            lib_id: Library identifier (e.g., "Device:R").
            position: Placement position.
            reference: Reference designator (e.g., "R1").
            value: Component value (e.g., "10k").
            footprint: Footprint name.
            unit: Symbol unit number.

        Returns:
            The newly created SchematicSymbol.
        """
        sym = SchematicSymbol(
            lib_id=lib_id,
            position=position,
            unit=unit,
            uuid=new_uuid(),
        )
        if reference:
            sym.properties.append(Property(key="Reference", value=reference,
                                           id=0, position=position))
        if value:
            sym.properties.append(Property(key="Value", value=value,
                                           id=1, position=position))
        if footprint:
            sym.properties.append(Property(key="Footprint", value=footprint,
                                           id=2, position=position))
        self.symbols.append(sym)
        return sym

    def remove_symbol(self, reference: str) -> Optional[SchematicSymbol]:
        """Remove a symbol by its reference designator.

        Args:
            reference: Reference to match (e.g., "R1").

        Returns:
            The removed symbol, or None if not found.
        """
        for i, sym in enumerate(self.symbols):
            if sym.reference == reference:
                return self.symbols.pop(i)
        return None

    def add_wire(self, start: Position, end: Position) -> Wire:
        """Add a new wire between two points.

        Args:
            start: Start position.
            end: End position.

        Returns:
            The newly created Wire.
        """
        wire = Wire(
            pts=Pts(points=[start, end]),
            stroke=Stroke(width=0, type="default"),
            uuid=new_uuid(),
        )
        self.wires.append(wire)
        return wire

    def add_label(self, text: str, position: Position) -> Label:
        """Add a net label at a position.

        Args:
            text: Label text (net name).
            position: Label position.

        Returns:
            The newly created Label.
        """
        label = Label(text=text, position=position, uuid=new_uuid())
        self.labels.append(label)
        return label

    def add_global_label(self, text: str, position: Position,
                         shape: str = "input") -> GlobalLabel:
        """Add a global label at a position.

        Args:
            text: Label text.
            position: Label position.
            shape: Label shape (input, output, bidirectional, etc.).

        Returns:
            The newly created GlobalLabel.
        """
        gl = GlobalLabel(text=text, position=position, shape=shape,
                         uuid=new_uuid())
        self.global_labels.append(gl)
        return gl

    def add_junction(self, position: Position) -> Junction:
        """Add a junction point.

        Args:
            position: Junction position.

        Returns:
            The newly created Junction.
        """
        junction = Junction(position=position, uuid=new_uuid())
        self.junctions.append(junction)
        return junction

    def add_no_connect(self, position: Position) -> NoConnect:
        """Add a no-connect marker.

        Args:
            position: No-connect position.

        Returns:
            The newly created NoConnect.
        """
        nc = NoConnect(position=position, uuid=new_uuid())
        self.no_connects.append(nc)
        return nc

    # --- Query Methods ---

    def find_symbols(self, pattern: str = "",
                     lib_id: str = "") -> List[SchematicSymbol]:
        """Find symbols matching a pattern.

        Args:
            pattern: Pattern to match against reference, value, or lib_id.
                     Empty string matches all.
            lib_id: If provided, match only symbols with this lib_id.

        Returns:
            List of matching symbols.
        """
        results = []
        pattern_lower = pattern.lower()
        for sym in self.symbols:
            if lib_id and sym.lib_id != lib_id:
                continue
            if not pattern:
                results.append(sym)
            elif (pattern_lower in sym.reference.lower() or
                  pattern_lower in sym.value.lower() or
                  pattern_lower in sym.lib_id.lower()):
                results.append(sym)
        return results

    def get_nets(self) -> Dict[str, List[str]]:
        """Get all net names and their connected pin references.

        Returns:
            Dictionary mapping net names to lists of connected pin references.
        """
        nets: Dict[str, List[str]] = {}
        for label in self.labels:
            if label.text not in nets:
                nets[label.text] = []
        for gl in self.global_labels:
            if gl.text not in nets:
                nets[gl.text] = []
        return nets

    def get_connected_nets(self) -> Dict[str, List[str]]:
        """Get nets with connected component information.

        This is a simplified implementation that identifies nets from labels.
        Full connectivity analysis would require pin position matching.

        Returns:
            Dictionary mapping net names to connection info.
        """
        return self.get_nets()
