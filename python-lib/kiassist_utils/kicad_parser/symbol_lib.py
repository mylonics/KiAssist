"""KiCad Symbol Library file model (.kicad_sym).

Provides classes to load, manipulate, and save KiCad symbol library files.
"""

from dataclasses import dataclass, field
from typing import List, Optional
from pathlib import Path

from .sexpr import (SExpr, parse, serialize, parse_file, serialize_to_file,
                    find_all, find_first, get_value, set_value, remove_by_tag)
from .models import (Position, Stroke, Effects, Property, Pts, Fill,
                     new_uuid, Color)


@dataclass
class Pin:
    """A pin on a symbol.

    Attributes:
        electrical_type: Pin electrical type (input, output, passive, etc.).
        graphic_style: Pin graphic style (line, inverted, clock, etc.).
        position: Pin position relative to symbol origin.
        length: Pin length in mm.
        name: Pin name (functional name).
        name_effects: Text effects for pin name.
        number: Pin number (pad number on footprint).
        number_effects: Text effects for pin number.
        hide: Whether the pin is hidden.
    """
    electrical_type: str = "passive"
    graphic_style: str = "line"
    position: Position = field(default_factory=Position)
    length: float = 2.54
    name: str = ""
    name_effects: Optional[Effects] = None
    number: str = ""
    number_effects: Optional[Effects] = None
    hide: bool = False

    def to_sexpr(self) -> SExpr:
        result: List[SExpr] = ["pin", self.electrical_type, self.graphic_style,
                                self.position.to_sexpr(),
                                ["length", self.length]]
        if self.hide:
            result.append("hide")

        name_expr: List[SExpr] = ["name", self.name]
        if self.name_effects is not None:
            name_expr.append(self.name_effects.to_sexpr())
        result.append(name_expr)

        number_expr: List[SExpr] = ["number", self.number]
        if self.number_effects is not None:
            number_expr.append(self.number_effects.to_sexpr())
        result.append(number_expr)

        return result

    @classmethod
    def from_sexpr(cls, expr: SExpr) -> "Pin":
        if not isinstance(expr, list) or len(expr) < 3:
            return cls()

        electrical_type = str(expr[1]) if len(expr) > 1 else "passive"
        graphic_style = str(expr[2]) if len(expr) > 2 else "line"
        position = Position()
        length = 2.54
        name = ""
        name_effects = None
        number = ""
        number_effects = None
        hide = "hide" in expr

        at_expr = find_first(expr, "at")
        if at_expr is not None:
            position = Position.from_sexpr(at_expr)

        length_val = get_value(expr, "length")
        if length_val is not None:
            length = float(length_val)

        name_expr = find_first(expr, "name")
        if name_expr is not None:
            name = str(name_expr[1]) if len(name_expr) > 1 else ""
            eff = find_first(name_expr, "effects")
            if eff is not None:
                name_effects = Effects.from_sexpr(eff)

        number_expr = find_first(expr, "number")
        if number_expr is not None:
            number = str(number_expr[1]) if len(number_expr) > 1 else ""
            eff = find_first(number_expr, "effects")
            if eff is not None:
                number_effects = Effects.from_sexpr(eff)

        return cls(electrical_type=electrical_type, graphic_style=graphic_style,
                   position=position, length=length, name=name,
                   name_effects=name_effects, number=number,
                   number_effects=number_effects, hide=hide)


@dataclass
class SymbolGraphic:
    """A graphic element within a symbol (rectangle, polyline, circle, arc, text)."""
    raw: SExpr = field(default_factory=list)

    @property
    def type(self) -> str:
        if isinstance(self.raw, list) and self.raw:
            return str(self.raw[0])
        return ""

    def to_sexpr(self) -> SExpr:
        return self.raw

    @classmethod
    def from_sexpr(cls, expr: SExpr) -> "SymbolGraphic":
        return cls(raw=expr)


@dataclass
class SymbolUnit:
    """A unit (sub-symbol) within a symbol definition.

    Multi-unit symbols (e.g., quad op-amp) have multiple units.
    """
    name: str = ""
    graphics: List[SymbolGraphic] = field(default_factory=list)
    pins: List[Pin] = field(default_factory=list)

    def to_sexpr(self) -> SExpr:
        result: List[SExpr] = ["symbol", self.name]
        for g in self.graphics:
            result.append(g.to_sexpr())
        for pin in self.pins:
            result.append(pin.to_sexpr())
        return result

    @classmethod
    def from_sexpr(cls, expr: SExpr) -> "SymbolUnit":
        if not isinstance(expr, list) or len(expr) < 2:
            return cls()

        name = str(expr[1])
        graphics = []
        pins = []

        _graphic_tags = {"rectangle", "polyline", "circle", "arc", "text",
                         "bezier"}

        for item in expr[2:]:
            if not isinstance(item, list) or not item:
                continue
            tag = item[0] if isinstance(item[0], str) else None
            if tag == "pin":
                pins.append(Pin.from_sexpr(item))
            elif tag in _graphic_tags:
                graphics.append(SymbolGraphic.from_sexpr(item))

        return cls(name=name, graphics=graphics, pins=pins)


@dataclass
class SymbolDef:
    """A symbol definition in a symbol library.

    Attributes:
        name: Symbol name (e.g., "R", "C", "STM32F407VGTx").
        extends: Parent symbol if this is a derived symbol.
        pin_numbers_hide: Whether pin numbers are hidden.
        pin_names_offset: Pin name offset distance.
        pin_names_hide: Whether pin names are hidden.
        exclude_from_sim: Whether excluded from simulation.
        in_bom: Whether included in BOM.
        on_board: Whether placed on board.
        properties: Symbol properties.
        units: Symbol units (sub-symbols).
    """
    name: str = ""
    extends: str = ""
    pin_numbers_hide: bool = False
    pin_names_offset: float = 1.016
    pin_names_hide: bool = False
    exclude_from_sim: bool = False
    in_bom: bool = True
    on_board: bool = True
    properties: List[Property] = field(default_factory=list)
    units: List[SymbolUnit] = field(default_factory=list)
    # Store raw for items we don't fully parse
    _other_items: List[SExpr] = field(default_factory=list)

    @property
    def all_pins(self) -> List[Pin]:
        """Get all pins across all units."""
        pins = []
        for unit in self.units:
            pins.extend(unit.pins)
        return pins

    def find_pin_by_number(self, number: str) -> Optional[Pin]:
        """Find a pin by its number."""
        for pin in self.all_pins:
            if pin.number == number:
                return pin
        return None

    def find_pin_by_name(self, name: str) -> Optional[Pin]:
        """Find a pin by its name."""
        for pin in self.all_pins:
            if pin.name == name:
                return pin
        return None

    def to_sexpr(self) -> SExpr:
        result: List[SExpr] = ["symbol", self.name]
        if self.extends:
            result.append(["extends", self.extends])
        if self.pin_numbers_hide:
            result.append(["pin_numbers", "hide"])

        pn_expr: List[SExpr] = ["pin_names"]
        if self.pin_names_offset != 1.016:
            pn_expr.append(["offset", self.pin_names_offset])
        if self.pin_names_hide:
            pn_expr.append("hide")
        if len(pn_expr) > 1:
            result.append(pn_expr)

        if self.exclude_from_sim:
            result.append(["exclude_from_sim", "yes"])
        result.append(["in_bom", "yes" if self.in_bom else "no"])
        result.append(["on_board", "yes" if self.on_board else "no"])

        for prop in self.properties:
            result.append(prop.to_sexpr())

        for unit in self.units:
            result.append(unit.to_sexpr())

        for item in self._other_items:
            result.append(item)

        return result

    @classmethod
    def from_sexpr(cls, expr: SExpr) -> "SymbolDef":
        if not isinstance(expr, list) or len(expr) < 2:
            return cls()

        name = str(expr[1])
        extends = ""
        pin_numbers_hide = False
        pin_names_offset = 1.016
        pin_names_hide = False
        exclude_from_sim = False
        in_bom = True
        on_board = True
        properties = []
        units = []
        other_items = []

        extends_val = get_value(expr, "extends")
        if extends_val is not None:
            extends = str(extends_val)

        pn_expr = find_first(expr, "pin_numbers")
        if pn_expr is not None:
            pin_numbers_hide = "hide" in pn_expr

        pnames_expr = find_first(expr, "pin_names")
        if pnames_expr is not None:
            pin_names_hide = "hide" in pnames_expr
            offset_val = get_value(pnames_expr, "offset")
            if offset_val is not None:
                pin_names_offset = float(offset_val)

        esim_val = get_value(expr, "exclude_from_sim")
        if esim_val is not None:
            exclude_from_sim = str(esim_val) == "yes"

        in_bom_val = get_value(expr, "in_bom")
        if in_bom_val is not None:
            in_bom = str(in_bom_val) == "yes"

        on_board_val = get_value(expr, "on_board")
        if on_board_val is not None:
            on_board = str(on_board_val) == "yes"

        _known_tags = {"symbol", "extends", "pin_numbers", "pin_names",
                       "exclude_from_sim", "in_bom", "on_board", "property"}

        for item in expr[2:]:
            if not isinstance(item, list) or not item:
                continue
            tag = item[0] if isinstance(item[0], str) else None
            if tag == "property":
                properties.append(Property.from_sexpr(item))
            elif tag == "symbol":
                units.append(SymbolUnit.from_sexpr(item))
            elif tag not in _known_tags:
                other_items.append(item)

        return cls(name=name, extends=extends, pin_numbers_hide=pin_numbers_hide,
                   pin_names_offset=pin_names_offset, pin_names_hide=pin_names_hide,
                   exclude_from_sim=exclude_from_sim, in_bom=in_bom,
                   on_board=on_board, properties=properties, units=units,
                   _other_items=other_items)


class SymbolLibrary:
    """KiCad symbol library file (.kicad_sym).

    Provides methods to load, manipulate, and save KiCad symbol library files.
    """

    def __init__(self):
        self.version: int = 20240215
        self.generator: str = "kiassist"
        self.generator_version: str = ""
        self.symbols: List[SymbolDef] = []

    @classmethod
    def load(cls, path: str) -> "SymbolLibrary":
        """Load a symbol library from a .kicad_sym file."""
        tree = parse_file(path)
        return cls._from_tree(tree)

    @classmethod
    def from_text(cls, text: str) -> "SymbolLibrary":
        """Parse a symbol library from S-expression text."""
        tree = parse(text)
        return cls._from_tree(tree)

    @classmethod
    def _from_tree(cls, tree: SExpr) -> "SymbolLibrary":
        lib = cls()

        if not isinstance(tree, list):
            return lib

        version_val = get_value(tree, "version")
        if version_val is not None:
            lib.version = int(version_val)

        gen_val = get_value(tree, "generator")
        if gen_val is not None:
            lib.generator = str(gen_val)

        gen_ver_val = get_value(tree, "generator_version")
        if gen_ver_val is not None:
            lib.generator_version = str(gen_ver_val)

        for item in tree:
            if (isinstance(item, list) and item and
                    isinstance(item[0], str) and item[0] == "symbol"):
                lib.symbols.append(SymbolDef.from_sexpr(item))

        return lib

    def to_sexpr(self) -> SExpr:
        tree: List[SExpr] = ["kicad_symbol_lib",
                              ["version", self.version],
                              ["generator", self.generator]]
        if self.generator_version:
            tree.append(["generator_version", self.generator_version])
        for sym in self.symbols:
            tree.append(sym.to_sexpr())
        return tree

    def save(self, path: str) -> None:
        """Save the symbol library to a .kicad_sym file."""
        tree = self.to_sexpr()
        serialize_to_file(tree, path, precision=4)

    def serialize(self) -> str:
        """Serialize the symbol library to S-expression text."""
        return serialize(self.to_sexpr(), precision=4)

    # --- Manipulation Methods ---

    def add_symbol(self, name: str, **kwargs) -> SymbolDef:
        """Add a new symbol to the library.

        Args:
            name: Symbol name.
            **kwargs: Additional SymbolDef attributes.

        Returns:
            The newly created SymbolDef.
        """
        sym = SymbolDef(name=name, **kwargs)
        self.symbols.append(sym)
        return sym

    def remove_symbol(self, name: str) -> Optional[SymbolDef]:
        """Remove a symbol by name.

        Args:
            name: Symbol name to remove.

        Returns:
            The removed symbol, or None if not found.
        """
        for i, sym in enumerate(self.symbols):
            if sym.name == name:
                return self.symbols.pop(i)
        return None

    def find_by_name(self, name: str) -> Optional[SymbolDef]:
        """Find a symbol by its name.

        Args:
            name: Symbol name to search for.

        Returns:
            The matching SymbolDef, or None.
        """
        for sym in self.symbols:
            if sym.name == name:
                return sym
        return None

    def modify_symbol(self, name: str, **kwargs) -> Optional[SymbolDef]:
        """Modify a symbol's attributes.

        Args:
            name: Symbol name to modify.
            **kwargs: Attributes to update.

        Returns:
            The modified symbol, or None if not found.
        """
        sym = self.find_by_name(name)
        if sym is None:
            return None
        for key, value in kwargs.items():
            if hasattr(sym, key):
                setattr(sym, key, value)
        return sym
