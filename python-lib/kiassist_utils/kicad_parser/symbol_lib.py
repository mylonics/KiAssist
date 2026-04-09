"""KiCad symbol library (.kicad_sym) file model.

Provides :class:`SymbolLibrary`, :class:`SymbolDef`, :class:`SymbolUnit`,
and :class:`Pin` classes for reading, modifying, and writing KiCad symbol
library files.

Typical usage::

    lib = SymbolLibrary.load("Device.kicad_sym")
    sym = lib.find_by_name("R")
    print([p.number for p in sym.pins()])
    lib.save("Device.kicad_sym")
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from .models import Effects, Position, Property, KiUUID
from .sexpr import QStr, SExpr, parse, serialize
from ._helpers import _find, _find_all, _parse_position, _parse_effects


# ---------------------------------------------------------------------------
# Pin
# ---------------------------------------------------------------------------


@dataclass
class Pin:
    """A single pin in a symbol definition.

    Attributes:
        electrical_type: KiCad electrical type keyword (e.g. ``"input"``,
                         ``"output"``, ``"bidirectional"``, ``"power_in"``).
        graphic_style:   Graphic style keyword (e.g. ``"line"``, ``"inverted"``).
        position:        Pin's anchor position and angle.
        length:          Pin length in mm.
        name:            Pin name label.
        number:          Pin number string (e.g. ``"1"``, ``"A2"``).
    """

    electrical_type: str = "unspecified"
    graphic_style: str = "line"
    position: Position = field(default_factory=lambda: Position(0, 0))
    length: float = 2.54
    name: str = ""
    number: str = ""

    @classmethod
    def from_tree(cls, tree: List[SExpr]) -> "Pin":
        p = cls()
        if len(tree) > 1:
            p.electrical_type = str(tree[1])
        if len(tree) > 2:
            p.graphic_style = str(tree[2])
        at = _find(tree, "at")
        if at:
            p.position = _parse_position(at)
        length_node = _find(tree, "length")
        if length_node and len(length_node) > 1:
            p.length = float(length_node[1])
        name_node = _find(tree, "name")
        if name_node and len(name_node) > 1:
            p.name = str(name_node[1])
        number_node = _find(tree, "number")
        if number_node and len(number_node) > 1:
            p.number = str(number_node[1])
        return p

    def to_tree(self) -> List[SExpr]:
        return [
            "pin",
            self.electrical_type,
            self.graphic_style,
            ["at", self.position.x, self.position.y, self.position.angle],
            ["length", self.length],
            ["name", QStr(self.name), ["effects", ["font", ["size", 1.27, 1.27]]]],
            ["number", QStr(self.number), ["effects", ["font", ["size", 1.27, 1.27]]]],
        ]


# ---------------------------------------------------------------------------
# SymbolUnit
# ---------------------------------------------------------------------------


@dataclass
class SymbolUnit:
    """One unit of a (possibly multi-unit) symbol.

    Attributes:
        unit_number: Which unit this is (0 = common graphics, 1+ = unit variants).
        style:       Style index (0 = normal, 1 = DeMorgan alternate body).
        pins:        Pins belonging to this unit.
        raw_tree:    Original S-expression for graphics preservation.
    """

    unit_number: int = 0
    style: int = 0
    pins: List[Pin] = field(default_factory=list)
    raw_tree: Optional[List[SExpr]] = field(default=None, repr=False)

    @classmethod
    def from_tree(cls, tree: List[SExpr]) -> "SymbolUnit":
        unit = cls(raw_tree=tree)
        # Name is like "Device:R_0_1" — extract numbers from last two parts
        if len(tree) > 1:
            parts = str(tree[1]).split("_")
            try:
                unit.style = int(parts[-1])
                unit.unit_number = int(parts[-2])
            except (ValueError, IndexError):
                pass
        for pin_tree in _find_all(tree, "pin"):
            unit.pins.append(Pin.from_tree(pin_tree))
        return unit

    def to_tree(self, parent_name: str = "") -> List[SExpr]:
        if self.raw_tree is not None:
            # If the parent was renamed we must patch the unit name in the
            # preserved raw tree so KiCad sees the correct prefix.
            if parent_name and len(self.raw_tree) > 1:
                old_name = str(self.raw_tree[1])
                expected_suffix = f"_{self.unit_number}_{self.style}"
                new_name = f"{parent_name}{expected_suffix}"
                if old_name != new_name:
                    self.raw_tree[1] = QStr(new_name)
            return self.raw_tree
        unit_name = f"{parent_name}_{self.unit_number}_{self.style}" if parent_name else f"unit_{self.unit_number}_{self.style}"
        tree: List[SExpr] = ["symbol", QStr(unit_name)]
        for pin in self.pins:
            tree.append(pin.to_tree())
        return tree


# ---------------------------------------------------------------------------
# SymbolDef
# ---------------------------------------------------------------------------


@dataclass
class SymbolDef:
    """A symbol definition inside a :class:`SymbolLibrary`.

    Attributes:
        name:               Symbol name string (e.g. ``"R"``, ``"NE555"``).
        extends:            Name of the parent symbol if this one extends it.
        pin_numbers_hide:   Whether pin numbers are hidden.
        pin_names_offset:   Offset for pin name labels.
        properties:         Key/value property list.
        units:              List of :class:`SymbolUnit` sub-symbols.
        raw_tree:           Original S-expression tree for round-trip fidelity.
    """

    name: str = ""
    extends: str = ""
    pin_numbers_hide: bool = False
    pin_names_offset: float = 1.016
    properties: List[Property] = field(default_factory=list)
    units: List[SymbolUnit] = field(default_factory=list)
    raw_tree: Optional[List[SExpr]] = field(default=None, repr=False)

    def pins(self) -> List[Pin]:
        """Return all pins across all units."""
        result: List[Pin] = []
        for unit in self.units:
            result.extend(unit.pins)
        return result

    @classmethod
    def from_tree(cls, tree: List[SExpr]) -> "SymbolDef":
        sd = cls(raw_tree=tree)
        sd.name = str(tree[1]) if len(tree) > 1 else ""
        extends_node = _find(tree, "extends")
        if extends_node and len(extends_node) > 1:
            sd.extends = str(extends_node[1])
        pin_numbers_node = _find(tree, "pin_numbers")
        if pin_numbers_node:
            sd.pin_numbers_hide = "hide" in pin_numbers_node or _find(pin_numbers_node, "hide") is not None
        pin_names_node = _find(tree, "pin_names")
        if pin_names_node:
            offset_node = _find(pin_names_node, "offset")
            if offset_node and len(offset_node) > 1:
                sd.pin_names_offset = float(offset_node[1])
        for prop in _find_all(tree, "property"):
            key = str(prop[1]) if len(prop) > 1 else ""
            value = str(prop[2]) if len(prop) > 2 else ""
            pos: Optional[Position] = None
            eff: Optional[Effects] = None
            at = _find(prop, "at")
            if at:
                pos = _parse_position(at)
            effects_node = _find(prop, "effects")
            if effects_node:
                eff = _parse_effects(effects_node)
            sd.properties.append(Property(key=key, value=value, position=pos, effects=eff))
        for unit_tree in _find_all(tree, "symbol"):
            sd.units.append(SymbolUnit.from_tree(unit_tree))
        return sd

    def to_tree(self) -> List[SExpr]:
        if self.raw_tree is not None:
            # Patch the symbol name in case it was changed after parsing.
            if len(self.raw_tree) > 1 and str(self.raw_tree[1]) != self.name:
                self.raw_tree[1] = QStr(self.name)
            return self.raw_tree
        tree: List[SExpr] = ["symbol", QStr(self.name)]
        if self.extends:
            tree.append(["extends", QStr(self.extends)])
        if self.pin_numbers_hide:
            tree.append(["pin_numbers", ["hide", "yes"]])
        tree.append(["pin_names", ["offset", self.pin_names_offset]])
        for p in self.properties:
            prop_node: List[SExpr] = ["property", QStr(p.key), QStr(p.value)]
            if p.position:
                prop_node.append(["at", p.position.x, p.position.y, p.position.angle])
            if p.effects:
                eff = p.effects
                font_node: List[SExpr] = ["font", ["size", eff.font_size[0], eff.font_size[1]]]
                if eff.bold:
                    font_node.append("bold")
                if eff.italic:
                    font_node.append("italic")
                effects_node: List[SExpr] = ["effects", font_node]
                if eff.hide:
                    effects_node.append(["hide", "yes"])
                if eff.justify:
                    effects_node.append(["justify"] + eff.justify.split())
                prop_node.append(effects_node)
            tree.append(prop_node)
        for unit in self.units:
            tree.append(unit.to_tree(parent_name=self.name))
        return tree


# ---------------------------------------------------------------------------
# SymbolLibrary
# ---------------------------------------------------------------------------


@dataclass
class SymbolLibrary:
    """Model for a KiCad symbol library file (.kicad_sym).

    Attributes:
        version:   File format version integer.
        generator: Name of the tool that last wrote the file.
        symbols:   All symbol definitions in the library.
    """

    version: int = 0
    generator: str = ""
    generator_version: str = ""
    symbols: List[SymbolDef] = field(default_factory=list)

    # ------------------------------------------------------------------
    # Load / save
    # ------------------------------------------------------------------

    @classmethod
    def load(cls, path: str | os.PathLike) -> "SymbolLibrary":
        """Load a symbol library from *path*.

        Supports both single ``.kicad_sym`` files and KiCad 10+
        ``.kicad_symdir`` directories (which contain one ``.kicad_sym``
        file per symbol).

        Args:
            path: Path to a ``.kicad_sym`` file or ``.kicad_symdir``
                  directory.

        Returns:
            Parsed :class:`SymbolLibrary`.

        Raises:
            FileNotFoundError: If *path* does not exist.
            ValueError: If the file is not a valid KiCad symbol library.
        """
        p = Path(path)
        if p.is_dir():
            return cls._load_symdir(p)
        text = p.read_text(encoding="utf-8")
        tree = parse(text)
        return cls._from_tree(tree)

    @classmethod
    def _load_symdir(cls, dirpath: Path) -> "SymbolLibrary":
        """Load a KiCad 10+ directory-based symbol library.

        Each ``.kicad_sym`` file in *dirpath* contains one symbol.
        All are merged into a single :class:`SymbolLibrary`.
        """
        lib = cls()
        for sym_file in sorted(dirpath.glob("*.kicad_sym")):
            try:
                text = sym_file.read_text(encoding="utf-8")
                tree = parse(text)
                sub = cls._from_tree(tree)
                lib.symbols.extend(sub.symbols)
                if not lib.version and sub.version:
                    lib.version = sub.version
                    lib.generator = sub.generator
                    lib.generator_version = sub.generator_version
            except Exception:
                import logging
                logging.getLogger(__name__).debug(
                    "Failed to parse symbol file %s", sym_file, exc_info=True,
                )
                continue
        return lib

    def save(self, path: str | os.PathLike) -> None:
        """Write the symbol library to *path*.

        Args:
            path: Destination ``.kicad_sym`` file path.
        """
        tree = self._to_tree()
        text = serialize(tree, indent=0, number_precision=4)
        Path(path).write_text(text + "\n", encoding="utf-8")

    # ------------------------------------------------------------------
    # CRUD helpers
    # ------------------------------------------------------------------

    def add_symbol(self, symbol: SymbolDef) -> None:
        """Add *symbol* to the library.

        Raises:
            ValueError: If a symbol with the same name already exists.
        """
        if self.find_by_name(symbol.name) is not None:
            raise ValueError(f"Symbol '{symbol.name}' already exists in the library")
        self.symbols.append(symbol)

    def remove_symbol(self, name: str) -> bool:
        """Remove the symbol named *name*.

        Returns:
            ``True`` if removed, ``False`` if not found.
        """
        for i, sym in enumerate(self.symbols):
            if sym.name == name:
                del self.symbols[i]
                return True
        return False

    def modify_symbol(self, name: str, **kwargs: Any) -> bool:
        """Update attributes of the symbol named *name*.

        Keyword arguments correspond to :class:`SymbolDef` attributes.
        When a matching symbol is modified, its *raw_tree* is cleared so the
        updated attributes are used during serialisation.

        Returns:
            ``True`` if the symbol was found and updated, ``False`` otherwise.
        """
        sym = self.find_by_name(name)
        if sym is None:
            return False
        for attr, val in kwargs.items():
            setattr(sym, attr, val)
        sym.raw_tree = None  # Force re-serialisation from dataclass fields
        return True

    def find_by_name(self, name: str) -> Optional[SymbolDef]:
        """Return the :class:`SymbolDef` with the given *name*, or ``None``."""
        for sym in self.symbols:
            if sym.name == name:
                return sym
        return None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @classmethod
    def _from_tree(cls, tree: List[SExpr]) -> "SymbolLibrary":
        if not tree or tree[0] != "kicad_symbol_lib":
            raise ValueError(
                "Not a valid KiCad symbol library file (expected 'kicad_symbol_lib' root tag)"
            )
        lib = cls()
        version_node = _find(tree, "version")
        if version_node and len(version_node) > 1:
            lib.version = int(version_node[1])
        gen_node = _find(tree, "generator")
        if gen_node and len(gen_node) > 1:
            lib.generator = str(gen_node[1])
        gen_ver_node = _find(tree, "generator_version")
        if gen_ver_node and len(gen_ver_node) > 1:
            lib.generator_version = str(gen_ver_node[1])
        for sym_tree in _find_all(tree, "symbol"):
            lib.symbols.append(SymbolDef.from_tree(sym_tree))
        return lib

    def _to_tree(self) -> List[SExpr]:
        tree: List[SExpr] = ["kicad_symbol_lib"]
        tree.append(["version", self.version])
        tree.append(["generator", QStr(self.generator)])
        if self.generator_version:
            tree.append(["generator_version", QStr(self.generator_version)])
        for sym in self.symbols:
            tree.append(sym.to_tree())
        return tree
