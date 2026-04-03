"""KiCad schematic (.kicad_sch) file model.

Provides the :class:`Schematic` class and supporting data classes for reading,
modifying, and writing KiCad schematic files (format version 6+).

Typical usage::

    sch = Schematic.load("myproject.kicad_sch")
    resistors = sch.find_symbols(value="10k")
    sch.add_wire(0.0, 0.0, 2.54, 0.0)
    sch.save("myproject.kicad_sch")
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from .models import Effects, Position, Property, KiUUID, Pts, Stroke
from .sexpr import QStr, SExpr, parse, serialize

# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------


def _find(tree: List[SExpr], tag: str) -> Optional[List[SExpr]]:
    """Return the first child list whose tag equals *tag*, or ``None``."""
    for item in tree:
        if isinstance(item, list) and item and item[0] == tag:
            return item
    return None


def _find_all(tree: List[SExpr], tag: str) -> List[List[SExpr]]:
    """Return all child lists whose tag equals *tag*."""
    return [item for item in tree if isinstance(item, list) and item and item[0] == tag]


def _atom(tree: List[SExpr], tag: str, default: Any = None) -> Any:
    """Return the first atom value inside the *tag* child, or *default*."""
    child = _find(tree, tag)
    if child is None or len(child) < 2:
        return default
    return child[1]


def _parse_position(tree: List[SExpr]) -> Position:
    """Parse an ``(at x y [angle])`` sub-expression."""
    x = float(tree[1]) if len(tree) > 1 else 0.0
    y = float(tree[2]) if len(tree) > 2 else 0.0
    angle = float(tree[3]) if len(tree) > 3 else 0.0
    return Position(x, y, angle)


def _parse_effects(tree: List[SExpr]) -> Effects:
    """Parse an ``(effects …)`` sub-expression."""
    eff = Effects()
    font = _find(tree, "font")
    if font:
        size = _find(font, "size")
        if size and len(size) >= 3:
            eff.font_size = (float(size[1]), float(size[2]))
        eff.bold = "bold" in font
        eff.italic = "italic" in font
    justify = _find(tree, "justify")
    if justify and len(justify) > 1:
        # Support multi-word justify like (justify right bottom) or (justify left mirror)
        eff.justify = " ".join(str(x) for x in justify[1:])
    hide_node = _find(tree, "hide")
    if hide_node is not None:
        # (hide yes) or bare (hide) — check value when present
        eff.hide = len(hide_node) < 2 or str(hide_node[1]).lower() == "yes"
    else:
        eff.hide = "hide" in tree  # legacy bare hide atom
    return eff


def _parse_stroke(tree: List[SExpr]) -> Stroke:
    """Parse a ``(stroke …)`` sub-expression."""
    stroke = Stroke()
    width_node = _find(tree, "width")
    if width_node and len(width_node) > 1:
        stroke.width = float(width_node[1])
    type_node = _find(tree, "type")
    if type_node and len(type_node) > 1:
        stroke.type = str(type_node[1])
    return stroke


def _serialize_effects(eff: Effects) -> List[SExpr]:
    """Serialize an :class:`Effects` object back to an S-expression list."""
    font_node: List[SExpr] = [
        "font",
        ["size", eff.font_size[0], eff.font_size[1]],
    ]
    if eff.bold:
        font_node.append("bold")
    if eff.italic:
        font_node.append("italic")
    effects_node: List[SExpr] = ["effects", font_node]
    if eff.hide:
        effects_node.append(["hide", "yes"])
    if eff.justify:
        # justify may be multi-word like "right bottom" or "left mirror"
        effects_node.append(["justify"] + eff.justify.split())
    return effects_node


def _parse_property(tree: List[SExpr]) -> Property:
    """Parse a ``(property "key" "value" …)`` sub-expression."""
    key = str(tree[1]) if len(tree) > 1 else ""
    value = str(tree[2]) if len(tree) > 2 else ""
    pos: Optional[Position] = None
    eff: Optional[Effects] = None
    at = _find(tree, "at")
    if at:
        pos = _parse_position(at)
    effects = _find(tree, "effects")
    if effects:
        eff = _parse_effects(effects)
    return Property(key=key, value=value, position=pos, effects=eff)


# ---------------------------------------------------------------------------
# Title block
# ---------------------------------------------------------------------------


@dataclass
class TitleBlock:
    """The title block / drawing header of a schematic sheet.

    Attributes:
        title:    Drawing title string.
        date:     Date string (e.g. ``"2026-04-03"``).
        revision: Revision string (e.g. ``"1.0"``).
        company:  Company / author name.
        comments: Mapping of comment number (1–4) to comment text.
    """

    title: str = ""
    date: str = ""
    revision: str = ""
    company: str = ""
    comments: Dict[int, str] = field(default_factory=dict)

    @classmethod
    def from_tree(cls, tree: List[SExpr]) -> "TitleBlock":
        tb = cls()
        title_node = _find(tree, "title")
        if title_node and len(title_node) > 1:
            tb.title = str(title_node[1])
        date_node = _find(tree, "date")
        if date_node and len(date_node) > 1:
            tb.date = str(date_node[1])
        rev_node = _find(tree, "rev")
        if rev_node and len(rev_node) > 1:
            tb.revision = str(rev_node[1])
        company_node = _find(tree, "company")
        if company_node and len(company_node) > 1:
            tb.company = str(company_node[1])
        for comment_node in _find_all(tree, "comment"):
            if len(comment_node) >= 3:
                try:
                    num = int(comment_node[1])
                    text = str(comment_node[2])
                    tb.comments[num] = text
                except (ValueError, IndexError):
                    pass
        return tb

    def to_tree(self) -> List[SExpr]:
        tree: List[SExpr] = ["title_block"]
        if self.title:
            tree.append(["title", QStr(self.title)])
        if self.date:
            tree.append(["date", QStr(self.date)])
        if self.revision:
            tree.append(["rev", QStr(self.revision)])
        if self.company:
            tree.append(["company", QStr(self.company)])
        for num in sorted(self.comments):
            tree.append(["comment", num, QStr(self.comments[num])])
        return tree


# ---------------------------------------------------------------------------
# Schematic items
# ---------------------------------------------------------------------------


@dataclass
class Wire:
    """A schematic wire segment."""

    pts: Pts = field(default_factory=Pts)
    stroke: Stroke = field(default_factory=Stroke)
    uuid: KiUUID = field(default_factory=KiUUID)

    @classmethod
    def from_tree(cls, tree: List[SExpr]) -> "Wire":
        w = cls()
        pts_node = _find(tree, "pts")
        if pts_node:
            for xy in _find_all(pts_node, "xy"):
                w.pts.add(float(xy[1]), float(xy[2]))
        stroke_node = _find(tree, "stroke")
        if stroke_node:
            w.stroke = _parse_stroke(stroke_node)
        uuid_node = _find(tree, "uuid")
        if uuid_node and len(uuid_node) > 1:
            w.uuid = KiUUID(str(uuid_node[1]))
        return w

    def to_tree(self) -> List[SExpr]:
        xy_nodes = [["xy", p.x, p.y] for p in self.pts]
        pts_node: List[SExpr] = ["pts"] + xy_nodes
        tree: List[SExpr] = [
            "wire",
            pts_node,
            ["stroke", ["width", self.stroke.width], ["type", self.stroke.type]],
        ]
        if self.uuid:
            tree.append(["uuid", QStr(self.uuid.value)])
        return tree


@dataclass
class Bus:
    """A schematic bus segment."""

    pts: Pts = field(default_factory=Pts)
    stroke: Stroke = field(default_factory=Stroke)
    uuid: KiUUID = field(default_factory=KiUUID)

    @classmethod
    def from_tree(cls, tree: List[SExpr]) -> "Bus":
        b = cls()
        pts_node = _find(tree, "pts")
        if pts_node:
            for xy in _find_all(pts_node, "xy"):
                b.pts.add(float(xy[1]), float(xy[2]))
        stroke_node = _find(tree, "stroke")
        if stroke_node:
            b.stroke = _parse_stroke(stroke_node)
        uuid_node = _find(tree, "uuid")
        if uuid_node and len(uuid_node) > 1:
            b.uuid = KiUUID(str(uuid_node[1]))
        return b

    def to_tree(self) -> List[SExpr]:
        xy_nodes = [["xy", p.x, p.y] for p in self.pts]
        pts_node: List[SExpr] = ["pts"] + xy_nodes
        tree: List[SExpr] = [
            "bus",
            pts_node,
            ["stroke", ["width", self.stroke.width], ["type", self.stroke.type]],
        ]
        if self.uuid:
            tree.append(["uuid", QStr(self.uuid.value)])
        return tree


@dataclass
class Junction:
    """A junction marker where wires connect."""

    position: Position = field(default_factory=lambda: Position(0, 0))
    diameter: float = 0.0
    color: Tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
    uuid: KiUUID = field(default_factory=KiUUID)

    @classmethod
    def from_tree(cls, tree: List[SExpr]) -> "Junction":
        j = cls()
        at = _find(tree, "at")
        if at:
            j.position = _parse_position(at)
        diam = _find(tree, "diameter")
        if diam and len(diam) > 1:
            j.diameter = float(diam[1])
        uuid_node = _find(tree, "uuid")
        if uuid_node and len(uuid_node) > 1:
            j.uuid = KiUUID(str(uuid_node[1]))
        return j

    def to_tree(self) -> List[SExpr]:
        tree: List[SExpr] = [
            "junction",
            ["at", self.position.x, self.position.y],
            ["diameter", self.diameter],
            ["color", 0, 0, 0, 0],
        ]
        if self.uuid:
            tree.append(["uuid", QStr(self.uuid.value)])
        return tree


@dataclass
class NoConnect:
    """A no-connect marker on an unconnected pin."""

    position: Position = field(default_factory=lambda: Position(0, 0))
    uuid: KiUUID = field(default_factory=KiUUID)

    @classmethod
    def from_tree(cls, tree: List[SExpr]) -> "NoConnect":
        nc = cls()
        at = _find(tree, "at")
        if at:
            nc.position = _parse_position(at)
        uuid_node = _find(tree, "uuid")
        if uuid_node and len(uuid_node) > 1:
            nc.uuid = KiUUID(str(uuid_node[1]))
        return nc

    def to_tree(self) -> List[SExpr]:
        tree: List[SExpr] = [
            "no_connect",
            ["at", self.position.x, self.position.y],
        ]
        if self.uuid:
            tree.append(["uuid", QStr(self.uuid.value)])
        return tree


@dataclass
class BusEntry:
    """A bus wire entry (diagonal segment connecting a wire to a bus)."""

    position: Position = field(default_factory=lambda: Position(0, 0))
    size: Tuple[float, float] = (2.54, -2.54)
    stroke: Stroke = field(default_factory=Stroke)
    uuid: KiUUID = field(default_factory=KiUUID)

    @classmethod
    def from_tree(cls, tree: List[SExpr]) -> "BusEntry":
        be = cls()
        at = _find(tree, "at")
        if at:
            be.position = _parse_position(at)
        size_node = _find(tree, "size")
        if size_node and len(size_node) >= 3:
            be.size = (float(size_node[1]), float(size_node[2]))
        stroke_node = _find(tree, "stroke")
        if stroke_node:
            be.stroke = _parse_stroke(stroke_node)
        uuid_node = _find(tree, "uuid")
        if uuid_node and len(uuid_node) > 1:
            be.uuid = KiUUID(str(uuid_node[1]))
        return be

    def to_tree(self) -> List[SExpr]:
        tree: List[SExpr] = [
            "bus_entry",
            ["at", self.position.x, self.position.y],
            ["size", self.size[0], self.size[1]],
            ["stroke", ["width", self.stroke.width], ["type", self.stroke.type]],
        ]
        if self.uuid:
            tree.append(["uuid", QStr(self.uuid.value)])
        return tree


@dataclass
class Label:
    """A net label on the schematic."""

    text: str = ""
    position: Position = field(default_factory=lambda: Position(0, 0))
    effects: Effects = field(default_factory=Effects)
    uuid: KiUUID = field(default_factory=KiUUID)

    @classmethod
    def from_tree(cls, tree: List[SExpr]) -> "Label":
        lbl = cls()
        lbl.text = str(tree[1]) if len(tree) > 1 else ""
        at = _find(tree, "at")
        if at:
            lbl.position = _parse_position(at)
        effects = _find(tree, "effects")
        if effects:
            lbl.effects = _parse_effects(effects)
        uuid_node = _find(tree, "uuid")
        if uuid_node and len(uuid_node) > 1:
            lbl.uuid = KiUUID(str(uuid_node[1]))
        return lbl

    def to_tree(self) -> List[SExpr]:
        tree: List[SExpr] = [
            "label",
            QStr(self.text),
            ["at", self.position.x, self.position.y, self.position.angle],
            _serialize_effects(self.effects),
        ]
        if self.uuid:
            tree.append(["uuid", QStr(self.uuid.value)])
        return tree


@dataclass
class GlobalLabel:
    """A global label (visible across all schematic sheets in a hierarchy)."""

    text: str = ""
    shape: str = "input"
    position: Position = field(default_factory=lambda: Position(0, 0))
    effects: Effects = field(default_factory=Effects)
    uuid: KiUUID = field(default_factory=KiUUID)

    @classmethod
    def from_tree(cls, tree: List[SExpr]) -> "GlobalLabel":
        gl = cls()
        gl.text = str(tree[1]) if len(tree) > 1 else ""
        shape_node = _find(tree, "shape")
        if shape_node and len(shape_node) > 1:
            gl.shape = str(shape_node[1])
        at = _find(tree, "at")
        if at:
            gl.position = _parse_position(at)
        effects = _find(tree, "effects")
        if effects:
            gl.effects = _parse_effects(effects)
        uuid_node = _find(tree, "uuid")
        if uuid_node and len(uuid_node) > 1:
            gl.uuid = KiUUID(str(uuid_node[1]))
        return gl

    def to_tree(self) -> List[SExpr]:
        tree: List[SExpr] = [
            "global_label",
            QStr(self.text),
            ["shape", self.shape],
            ["at", self.position.x, self.position.y, self.position.angle],
            _serialize_effects(self.effects),
        ]
        if self.uuid:
            tree.append(["uuid", QStr(self.uuid.value)])
        return tree


@dataclass
class HierarchicalLabel:
    """A hierarchical label (connects a sub-sheet pin to the parent sheet)."""

    text: str = ""
    shape: str = "input"
    position: Position = field(default_factory=lambda: Position(0, 0))
    effects: Effects = field(default_factory=Effects)
    uuid: KiUUID = field(default_factory=KiUUID)

    @classmethod
    def from_tree(cls, tree: List[SExpr]) -> "HierarchicalLabel":
        hl = cls()
        hl.text = str(tree[1]) if len(tree) > 1 else ""
        shape_node = _find(tree, "shape")
        if shape_node and len(shape_node) > 1:
            hl.shape = str(shape_node[1])
        at = _find(tree, "at")
        if at:
            hl.position = _parse_position(at)
        effects = _find(tree, "effects")
        if effects:
            hl.effects = _parse_effects(effects)
        uuid_node = _find(tree, "uuid")
        if uuid_node and len(uuid_node) > 1:
            hl.uuid = KiUUID(str(uuid_node[1]))
        return hl

    def to_tree(self) -> List[SExpr]:
        tree: List[SExpr] = [
            "hierarchical_label",
            QStr(self.text),
            ["shape", self.shape],
            ["at", self.position.x, self.position.y, self.position.angle],
            _serialize_effects(self.effects),
        ]
        if self.uuid:
            tree.append(["uuid", QStr(self.uuid.value)])
        return tree


@dataclass
class Sheet:
    """A sub-sheet reference in a hierarchical schematic."""

    position: Position = field(default_factory=lambda: Position(0, 0))
    size: Tuple[float, float] = (5.08, 5.08)
    properties: List[Property] = field(default_factory=list)
    uuid: KiUUID = field(default_factory=KiUUID)
    _extra: List[List[SExpr]] = field(default_factory=list, repr=False)

    @classmethod
    def from_tree(cls, tree: List[SExpr]) -> "Sheet":
        sh = cls()
        at = _find(tree, "at")
        if at:
            sh.position = _parse_position(at)
        size_node = _find(tree, "size")
        if size_node and len(size_node) >= 3:
            sh.size = (float(size_node[1]), float(size_node[2]))
        for prop in _find_all(tree, "property"):
            sh.properties.append(_parse_property(prop))
        uuid_node = _find(tree, "uuid")
        if uuid_node and len(uuid_node) > 1:
            sh.uuid = KiUUID(str(uuid_node[1]))
        _KNOWN_SHEET = {"at", "size", "property", "uuid", "stroke", "fill", "fields_autoplaced"}
        for item in tree[1:]:
            if isinstance(item, list) and item and str(item[0]) not in _KNOWN_SHEET:
                sh._extra.append(item)
        return sh

    def to_tree(self) -> List[SExpr]:
        tree: List[SExpr] = [
            "sheet",
            ["at", self.position.x, self.position.y],
            ["size", self.size[0], self.size[1]],
        ]
        for p in self.properties:
            prop_node: List[SExpr] = ["property", QStr(p.key), QStr(p.value)]
            if p.position:
                prop_node.append(["at", p.position.x, p.position.y, p.position.angle])
            if p.effects:
                prop_node.append(_serialize_effects(p.effects))
            tree.append(prop_node)
        if self.uuid:
            tree.append(["uuid", QStr(self.uuid.value)])
        for extra in self._extra:
            tree.append(extra)
        return tree


# ---------------------------------------------------------------------------
# Library symbol (embedded in lib_symbols section)
# ---------------------------------------------------------------------------


@dataclass
class LibSymbol:
    """A symbol definition as embedded in a schematic's ``lib_symbols`` block.

    This is the *definition* (graphics, pins, properties) rather than an
    *instance* placed on the schematic.
    """

    name: str = ""
    raw_tree: Optional[List[SExpr]] = field(default=None, repr=False)

    @classmethod
    def from_tree(cls, tree: List[SExpr]) -> "LibSymbol":
        name = str(tree[1]) if len(tree) > 1 else ""
        return cls(name=name, raw_tree=tree)

    def to_tree(self) -> List[SExpr]:
        if self.raw_tree is not None:
            return self.raw_tree
        return ["symbol", QStr(self.name)]


# ---------------------------------------------------------------------------
# Placed schematic symbol (instance)
# ---------------------------------------------------------------------------


@dataclass
class SchematicSymbol:
    """A symbol instance placed on the schematic sheet."""

    lib_id: str = ""
    position: Position = field(default_factory=lambda: Position(0, 0))
    unit: int = 1
    in_bom: bool = True
    on_board: bool = True
    exclude_from_sim: bool = False
    properties: List[Property] = field(default_factory=list)
    pin_uuids: Dict[str, KiUUID] = field(default_factory=dict)
    uuid: KiUUID = field(default_factory=KiUUID)
    raw_tree: Optional[List[SExpr]] = field(default=None, repr=False)

    # Convenience properties
    @property
    def reference(self) -> str:
        """Return the reference designator (e.g. ``"R1"``)."""
        for p in self.properties:
            if p.key == "Reference":
                return p.value
        return ""

    @property
    def value(self) -> str:
        """Return the component value (e.g. ``"10k"``)."""
        for p in self.properties:
            if p.key == "Value":
                return p.value
        return ""

    @property
    def footprint(self) -> str:
        """Return the footprint assignment (e.g. ``"Resistor_SMD:R_0402"``)."""
        for p in self.properties:
            if p.key == "Footprint":
                return p.value
        return ""

    @classmethod
    def from_tree(cls, tree: List[SExpr]) -> "SchematicSymbol":
        sym = cls(raw_tree=tree)
        lib_id_node = _find(tree, "lib_id")
        if lib_id_node and len(lib_id_node) > 1:
            sym.lib_id = str(lib_id_node[1])
        at = _find(tree, "at")
        if at:
            sym.position = _parse_position(at)
        unit_node = _find(tree, "unit")
        if unit_node and len(unit_node) > 1:
            sym.unit = int(unit_node[1])
        in_bom_node = _find(tree, "in_bom")
        if in_bom_node and len(in_bom_node) > 1:
            sym.in_bom = str(in_bom_node[1]) == "yes"
        on_board_node = _find(tree, "on_board")
        if on_board_node and len(on_board_node) > 1:
            sym.on_board = str(on_board_node[1]) == "yes"
        exclude_node = _find(tree, "exclude_from_sim")
        if exclude_node and len(exclude_node) > 1:
            sym.exclude_from_sim = str(exclude_node[1]) == "yes"
        for prop in _find_all(tree, "property"):
            sym.properties.append(_parse_property(prop))
        # Parse pin UUIDs: (pin "N" (uuid "..."))
        for pin_node in _find_all(tree, "pin"):
            if len(pin_node) >= 2:
                pin_num = str(pin_node[1])
                pin_uuid_node = _find(pin_node, "uuid")
                if pin_uuid_node and len(pin_uuid_node) > 1:
                    sym.pin_uuids[pin_num] = KiUUID(str(pin_uuid_node[1]))
        uuid_node = _find(tree, "uuid")
        if uuid_node and len(uuid_node) > 1:
            sym.uuid = KiUUID(str(uuid_node[1]))
        return sym

    def to_tree(self) -> List[SExpr]:
        """Serialise back to an S-expression list.

        If the symbol was loaded from a file, the original *raw_tree* is
        returned unchanged so that unrecognised attributes are preserved.
        """
        if self.raw_tree is not None:
            return self.raw_tree

        tree: List[SExpr] = [
            "symbol",
            ["lib_id", QStr(self.lib_id)],
            ["at", self.position.x, self.position.y, self.position.angle],
            ["unit", self.unit],
            ["exclude_from_sim", "yes" if self.exclude_from_sim else "no"],
            ["in_bom", "yes" if self.in_bom else "no"],
            ["on_board", "yes" if self.on_board else "no"],
        ]
        if self.uuid:
            tree.append(["uuid", QStr(self.uuid.value)])
        for p in self.properties:
            prop_node: List[SExpr] = ["property", QStr(p.key), QStr(p.value)]
            if p.position:
                prop_node.append(["at", p.position.x, p.position.y, p.position.angle])
            if p.effects:
                prop_node.append(_serialize_effects(p.effects))
            tree.append(prop_node)
        return tree


# ---------------------------------------------------------------------------
# Main Schematic class
# ---------------------------------------------------------------------------


@dataclass
class Schematic:
    """Model for a KiCad schematic file (.kicad_sch).

    Attributes:
        version:             File format version integer (e.g. ``20231120``).
        generator:           Name of the tool that last wrote the file.
        uuid:                Schematic UUID.
        paper:               Paper size string (e.g. ``"A4"``).
        title_block:         Drawing title block (title, date, revision, …).
        lib_symbols:         Embedded library symbol definitions.
        wires:               Wire segments on this sheet.
        buses:               Bus segments on this sheet.
        junctions:           Junction markers.
        no_connects:         No-connect markers.
        bus_entries:         Bus-entry diagonal segments.
        labels:              Net labels.
        global_labels:       Global labels.
        hierarchical_labels: Hierarchical labels.
        sheets:              Sub-sheet references.
        symbols:             Placed component instances.
        _extra:              Unrecognised top-level items preserved for round-trips.
    """

    version: int = 0
    generator: str = ""
    generator_version: str = ""
    uuid: KiUUID = field(default_factory=KiUUID)
    paper: str = "A4"
    title_block: Optional[TitleBlock] = None
    lib_symbols: List[LibSymbol] = field(default_factory=list)
    wires: List[Wire] = field(default_factory=list)
    buses: List[Bus] = field(default_factory=list)
    junctions: List[Junction] = field(default_factory=list)
    no_connects: List[NoConnect] = field(default_factory=list)
    bus_entries: List[BusEntry] = field(default_factory=list)
    labels: List[Label] = field(default_factory=list)
    global_labels: List[GlobalLabel] = field(default_factory=list)
    hierarchical_labels: List[HierarchicalLabel] = field(default_factory=list)
    sheets: List[Sheet] = field(default_factory=list)
    symbols: List[SchematicSymbol] = field(default_factory=list)
    _extra: List[List[SExpr]] = field(default_factory=list, repr=False)

    # ------------------------------------------------------------------
    # Load / save
    # ------------------------------------------------------------------

    @classmethod
    def load(cls, path: str | os.PathLike) -> "Schematic":
        """Load a schematic from *path* and return a :class:`Schematic`.

        Args:
            path: Path to a ``.kicad_sch`` file.

        Returns:
            Parsed :class:`Schematic` instance.

        Raises:
            FileNotFoundError: If *path* does not exist.
            ValueError: If the file is not a valid KiCad schematic.
        """
        text = Path(path).read_text(encoding="utf-8")
        tree = parse(text)
        return cls._from_tree(tree)

    def save(self, path: str | os.PathLike) -> None:
        """Write the schematic to *path*.

        Args:
            path: Destination ``.kicad_sch`` file path.  Parent directories
                  must already exist.
        """
        tree = self._to_tree()
        text = serialize(tree, indent=0, number_precision=4)
        Path(path).write_text(text + "\n", encoding="utf-8")

    # ------------------------------------------------------------------
    # Mutation helpers
    # ------------------------------------------------------------------

    def add_wire(self, x1: float, y1: float, x2: float, y2: float) -> Wire:
        """Add a wire segment between (x1, y1) and (x2, y2).

        Args:
            x1, y1: Start coordinate in mm.
            x2, y2: End coordinate in mm.

        Returns:
            The newly created :class:`Wire`.
        """
        wire = Wire()
        wire.pts.add(x1, y1)
        wire.pts.add(x2, y2)
        wire.uuid = KiUUID.new()
        self.wires.append(wire)
        return wire

    def add_symbol(
        self,
        lib_id: str,
        x: float,
        y: float,
        reference: str = "U?",
        value: str = "",
        footprint: str = "",
        angle: float = 0.0,
    ) -> SchematicSymbol:
        """Place a symbol instance on the schematic.

        Args:
            lib_id:    Library identifier string (e.g. ``"Device:R"``).
            x, y:      Position in mm.
            reference: Reference designator (e.g. ``"R1"``).
            value:     Component value (e.g. ``"10k"``).
            footprint: Footprint assignment string.
            angle:     Rotation angle in degrees.

        Returns:
            The newly created :class:`SchematicSymbol`.
        """
        sym = SchematicSymbol()
        sym.lib_id = lib_id
        sym.position = Position(x, y, angle)
        sym.uuid = KiUUID.new()
        sym.properties = [
            Property("Reference", reference, Position(x + 1.27, y - 1.27)),
            Property("Value", value or lib_id.split(":")[-1], Position(x + 1.27, y + 1.27)),
        ]
        if footprint:
            sym.properties.append(Property("Footprint", footprint, Position(x, y + 2.54)))
        self.symbols.append(sym)
        return sym

    def remove_symbol(self, reference: str) -> bool:
        """Remove the first symbol whose reference equals *reference*.

        Args:
            reference: Reference designator to remove (e.g. ``"R1"``).

        Returns:
            ``True`` if a symbol was removed, ``False`` if not found.
        """
        for i, sym in enumerate(self.symbols):
            if sym.reference == reference:
                del self.symbols[i]
                return True
        return False

    def add_junction(self, x: float, y: float) -> Junction:
        """Add a junction marker at (x, y).

        Args:
            x, y: Position in mm.

        Returns:
            The newly created :class:`Junction`.
        """
        j = Junction()
        j.position = Position(x, y)
        j.uuid = KiUUID.new()
        self.junctions.append(j)
        return j

    def add_no_connect(self, x: float, y: float) -> NoConnect:
        """Add a no-connect marker at (x, y).

        Args:
            x, y: Position in mm.

        Returns:
            The newly created :class:`NoConnect`.
        """
        nc = NoConnect()
        nc.position = Position(x, y)
        nc.uuid = KiUUID.new()
        self.no_connects.append(nc)
        return nc

    def add_label(self, text: str, x: float, y: float, angle: float = 0.0) -> Label:
        """Add a net label at (x, y).

        Args:
            text:  Label / net name.
            x, y:  Position in mm.
            angle: Rotation angle.

        Returns:
            The newly created :class:`Label`.
        """
        lbl = Label()
        lbl.text = text
        lbl.position = Position(x, y, angle)
        lbl.uuid = KiUUID.new()
        self.labels.append(lbl)
        return lbl

    # ------------------------------------------------------------------
    # Query helpers
    # ------------------------------------------------------------------

    def find_symbols(
        self,
        reference: Optional[str] = None,
        value: Optional[str] = None,
        lib_id: Optional[str] = None,
    ) -> List[SchematicSymbol]:
        """Return symbols matching the given filters.

        Filters are ANDed together; omitting a filter means "match all".

        Args:
            reference: Filter by reference designator (exact match).
            value:     Filter by component value (exact match).
            lib_id:    Filter by library identifier (exact match).

        Returns:
            List of matching :class:`SchematicSymbol` instances.
        """
        results = []
        for sym in self.symbols:
            if reference is not None and sym.reference != reference:
                continue
            if value is not None and sym.value != value:
                continue
            if lib_id is not None and sym.lib_id != lib_id:
                continue
            results.append(sym)
        return results

    def get_pin_positions(self, reference: str) -> Dict[str, Position]:
        """Return pin positions for all lib_symbols pins of *reference*.

        Note: pin positions are taken from the lib_symbol definition embedded
        in the schematic.  The returned dict maps pin number/name to absolute
        position after applying the symbol's placement transform.

        Args:
            reference: Reference designator.

        Returns:
            Dictionary mapping pin number string to absolute :class:`Position`.
        """
        syms = self.find_symbols(reference=reference)
        if not syms:
            return {}
        sym = syms[0]
        # Find lib symbol
        for lib_sym in self.lib_symbols:
            if lib_sym.name == sym.lib_id or lib_sym.name.endswith(":" + sym.lib_id.split(":")[-1]):
                result: Dict[str, Position] = {}
                if lib_sym.raw_tree:
                    for unit in _find_all(lib_sym.raw_tree, "symbol"):
                        for pin in _find_all(unit, "pin"):
                            num_node = _find(pin, "number")
                            at_node = _find(pin, "at")
                            if num_node and at_node:
                                pin_num = str(num_node[1])
                                pin_pos = _parse_position(at_node)
                                # Apply symbol rotation
                                import math
                                angle_rad = math.radians(sym.position.angle)
                                rx = pin_pos.x * math.cos(angle_rad) - pin_pos.y * math.sin(angle_rad)
                                ry = pin_pos.x * math.sin(angle_rad) + pin_pos.y * math.cos(angle_rad)
                                result[pin_num] = Position(
                                    sym.position.x + rx,
                                    sym.position.y + ry,
                                )
                return result
        return {}

    def get_connected_nets(self) -> Dict[str, List[str]]:
        """Return a mapping of net name to list of connected pin references.

        Nets are identified by :class:`Label` and :class:`GlobalLabel` text.
        Wire topology is traced using a union-find algorithm: all wire segments
        are merged into connected components, then labels and symbol pins whose
        positions coincide with a wire endpoint (within 0.001 mm) are assigned
        to the same net.

        Returns:
            Dict mapping net name to list of ``"RefDes:PinNum"`` strings.
        """
        _EPS = 0.001  # mm coordinate snap tolerance

        # ------------------------------------------------------------------
        # 1. Collect every distinct point that appears on a wire endpoint.
        # ------------------------------------------------------------------
        all_pts: List[Tuple[float, float]] = []
        pt_index: Dict[Tuple[float, float], int] = {}

        def _snap(x: float, y: float) -> Tuple[float, float]:
            return (round(x / _EPS) * _EPS, round(y / _EPS) * _EPS)

        def _get_or_add(x: float, y: float) -> int:
            key = _snap(x, y)
            if key not in pt_index:
                pt_index[key] = len(all_pts)
                all_pts.append(key)
            return pt_index[key]

        # ------------------------------------------------------------------
        # 2. Union-find helpers.
        # ------------------------------------------------------------------
        parent: List[int] = []

        def _root(i: int) -> int:
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i

        def _union(a: int, b: int) -> None:
            ra, rb = _root(a), _root(b)
            if ra != rb:
                parent[rb] = ra

        # ------------------------------------------------------------------
        # 3. Add all wire endpoints and union consecutive points on each wire.
        # ------------------------------------------------------------------
        for w in self.wires:
            pts_list = list(w.pts)
            if not pts_list:
                continue
            ids = [_get_or_add(p.x, p.y) for p in pts_list]
            # Grow parent array to accommodate new points
            while len(parent) < len(all_pts):
                parent.append(len(parent))
            for i in range(len(ids) - 1):
                _union(ids[i], ids[i + 1])

        # Ensure parent covers all current points
        while len(parent) < len(all_pts):
            parent.append(len(parent))

        # ------------------------------------------------------------------
        # 4. Map each wire-connected component to the net names that touch it
        #    (via labels / global labels at matching positions).
        # ------------------------------------------------------------------
        component_nets: Dict[int, str] = {}

        def _assign_net(x: float, y: float, net_name: str) -> None:
            key = _snap(x, y)
            if key in pt_index:
                root = _root(pt_index[key])
                if root not in component_nets:
                    component_nets[root] = net_name

        for lbl in self.labels:
            _assign_net(lbl.position.x, lbl.position.y, lbl.text)

        for gl in self.global_labels:
            _assign_net(gl.position.x, gl.position.y, gl.text)

        # ------------------------------------------------------------------
        # 5. Map symbol pins to nets by matching pin positions to wire components.
        # ------------------------------------------------------------------
        nets: Dict[str, List[str]] = {}

        def _add(net: str, pin_ref: str) -> None:
            nets.setdefault(net, [])
            if pin_ref not in nets[net]:
                nets[net].append(pin_ref)

        for sym in self.symbols:
            pin_positions = self.get_pin_positions(sym.reference)
            for pin_num, pos in pin_positions.items():
                key = _snap(pos.x, pos.y)
                if key in pt_index:
                    root = _root(pt_index[key])
                    net_name = component_nets.get(root)
                    if net_name:
                        _add(net_name, f"{sym.reference}:{pin_num}")

        # Also include labels that are not connected to any wire as standalone entries
        for lbl in self.labels:
            nets.setdefault(lbl.text, [])
        for gl in self.global_labels:
            nets.setdefault(gl.text, [])

        return nets

    # ------------------------------------------------------------------
    # Internal serialisation helpers
    # ------------------------------------------------------------------

    @classmethod
    def _from_tree(cls, tree: List[SExpr]) -> "Schematic":
        if not tree or tree[0] != "kicad_sch":
            raise ValueError("Not a valid KiCad schematic file (expected 'kicad_sch' root tag)")

        sch = cls()
        version_node = _find(tree, "version")
        if version_node and len(version_node) > 1:
            sch.version = int(version_node[1])
        gen_node = _find(tree, "generator")
        if gen_node and len(gen_node) > 1:
            sch.generator = str(gen_node[1])
        gen_ver_node = _find(tree, "generator_version")
        if gen_ver_node and len(gen_ver_node) > 1:
            sch.generator_version = str(gen_ver_node[1])
        uuid_node = _find(tree, "uuid")
        if uuid_node and len(uuid_node) > 1:
            sch.uuid = KiUUID(str(uuid_node[1]))
        paper_node = _find(tree, "paper")
        if paper_node and len(paper_node) > 1:
            sch.paper = str(paper_node[1])

        title_block_node = _find(tree, "title_block")
        if title_block_node:
            sch.title_block = TitleBlock.from_tree(title_block_node)

        lib_syms_node = _find(tree, "lib_symbols")
        if lib_syms_node:
            for sub in _find_all(lib_syms_node, "symbol"):
                sch.lib_symbols.append(LibSymbol.from_tree(sub))

        _KNOWN_TAGS = {
            "kicad_sch", "version", "generator", "generator_version",
            "uuid", "paper", "title_block", "lib_symbols",
            "wire", "bus", "junction", "no_connect", "bus_entry",
            "label", "global_label", "hierarchical_label", "sheet", "symbol",
            "sheet_instances", "symbol_instances",
        }

        for item in tree[1:]:
            if not isinstance(item, list) or not item:
                continue
            tag = item[0]
            if tag == "wire":
                sch.wires.append(Wire.from_tree(item))
            elif tag == "bus":
                sch.buses.append(Bus.from_tree(item))
            elif tag == "junction":
                sch.junctions.append(Junction.from_tree(item))
            elif tag == "no_connect":
                sch.no_connects.append(NoConnect.from_tree(item))
            elif tag == "bus_entry":
                sch.bus_entries.append(BusEntry.from_tree(item))
            elif tag == "label":
                sch.labels.append(Label.from_tree(item))
            elif tag == "global_label":
                sch.global_labels.append(GlobalLabel.from_tree(item))
            elif tag == "hierarchical_label":
                sch.hierarchical_labels.append(HierarchicalLabel.from_tree(item))
            elif tag == "sheet":
                sch.sheets.append(Sheet.from_tree(item))
            elif tag == "symbol":
                sch.symbols.append(SchematicSymbol.from_tree(item))
            elif tag not in _KNOWN_TAGS:
                sch._extra.append(item)

        return sch

    def _to_tree(self) -> List[SExpr]:
        tree: List[SExpr] = ["kicad_sch"]
        tree.append(["version", self.version])
        tree.append(["generator", QStr(self.generator)])
        if self.generator_version:
            tree.append(["generator_version", QStr(self.generator_version)])
        if self.uuid:
            tree.append(["uuid", QStr(self.uuid.value)])
        tree.append(["paper", QStr(self.paper)])
        if self.title_block is not None:
            tree.append(self.title_block.to_tree())

        # Lib symbols
        lib_syms_node: List[SExpr] = ["lib_symbols"]
        for ls in self.lib_symbols:
            lib_syms_node.append(ls.to_tree())
        tree.append(lib_syms_node)

        # Wires
        for w in self.wires:
            tree.append(w.to_tree())
        # Buses
        for b in self.buses:
            tree.append(b.to_tree())
        # Junctions
        for j in self.junctions:
            tree.append(j.to_tree())
        # No-connects
        for nc in self.no_connects:
            tree.append(nc.to_tree())
        # Bus entries
        for be in self.bus_entries:
            tree.append(be.to_tree())
        # Labels
        for lbl in self.labels:
            tree.append(lbl.to_tree())
        # Global labels
        for gl in self.global_labels:
            tree.append(gl.to_tree())
        # Hierarchical labels
        for hl in self.hierarchical_labels:
            tree.append(hl.to_tree())
        # Sheets
        for sh in self.sheets:
            tree.append(sh.to_tree())
        # Symbols
        for sym in self.symbols:
            tree.append(sym.to_tree())
        # Extra / unrecognised items
        for extra in self._extra:
            tree.append(extra)
        return tree
