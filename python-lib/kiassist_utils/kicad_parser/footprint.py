"""KiCad footprint (.kicad_mod) file model.

Provides :class:`Footprint`, :class:`Pad`, and :class:`FootprintGraphic`
classes for reading, modifying, and writing KiCad footprint files.

Typical usage::

    fp = Footprint.load("Resistor_SMD:R_0402.kicad_mod")
    fp.add_pad("3", "smd", "roundrect", 0.0, 0.0, 1.0, 0.5)
    fp.renumber_pads()
    fp.save("my_footprint.kicad_mod")
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .models import Effects, Position, Property, KiUUID, Stroke
from .sexpr import QStr, SExpr, parse, serialize
from ._helpers import _find, _find_all, _parse_position

# ---------------------------------------------------------------------------
# Module-local helper utilities
# ---------------------------------------------------------------------------


def _parse_size(tree: List[SExpr]) -> Tuple[float, float]:
    w = float(tree[1]) if len(tree) > 1 else 0.0
    h = float(tree[2]) if len(tree) > 2 else 0.0
    return (w, h)


def _parse_layers(tree: List[SExpr]) -> List[str]:
    return [str(item) for item in tree[1:] if isinstance(item, (str, QStr))]


# ---------------------------------------------------------------------------
# Pad
# ---------------------------------------------------------------------------


@dataclass
class Pad:
    """A pad in a footprint.

    Attributes:
        number:   Pad number string (e.g. ``"1"``, ``"A1"``).
        type:     Pad type keyword: ``"smd"``, ``"thru_hole"``, ``"connect"``,
                  ``"np_thru_hole"``.
        shape:    Pad shape keyword: ``"circle"``, ``"rect"``, ``"roundrect"``,
                  ``"oval"``, ``"trapezoid"``, ``"custom"``.
        position: Pad centre position and optional rotation.
        size:     Pad (width, height) in mm.
        drill:    Drill diameter for thru-hole pads (0.0 for SMD).
        layers:   Layer membership list (e.g. ``["F.Cu", "F.Paste", "F.Mask"]``).
        net:      Net name string (empty if unassigned).
        raw_tree: Original S-expression for round-trip fidelity.
    """

    number: str = "1"
    type: str = "smd"
    shape: str = "rect"
    position: Position = field(default_factory=lambda: Position(0.0, 0.0))
    size: Tuple[float, float] = (1.0, 1.0)
    drill: float = 0.0
    layers: List[str] = field(default_factory=list)
    net: str = ""
    raw_tree: Optional[List[SExpr]] = field(default=None, repr=False)

    @classmethod
    def from_tree(cls, tree: List[SExpr]) -> "Pad":
        pad = cls(raw_tree=tree)
        pad.number = str(tree[1]) if len(tree) > 1 else "1"
        pad.type = str(tree[2]) if len(tree) > 2 else "smd"
        pad.shape = str(tree[3]) if len(tree) > 3 else "rect"
        at = _find(tree, "at")
        if at:
            pad.position = _parse_position(at)
        size_node = _find(tree, "size")
        if size_node:
            pad.size = _parse_size(size_node)
        drill_node = _find(tree, "drill")
        if drill_node and len(drill_node) > 1:
            pad.drill = float(drill_node[1])
        layers_node = _find(tree, "layers")
        if layers_node:
            pad.layers = _parse_layers(layers_node)
        net_node = _find(tree, "net")
        if net_node and len(net_node) > 2:
            pad.net = str(net_node[2])
        return pad

    def to_tree(self) -> List[SExpr]:
        if self.raw_tree is not None:
            # Update mutable fields in the raw tree
            for i, item in enumerate(self.raw_tree):
                if isinstance(item, list) and item and item[0] == "at":
                    self.raw_tree[i] = ["at", self.position.x, self.position.y, self.position.angle]
            if len(self.raw_tree) > 1:
                self.raw_tree[1] = QStr(self.number) if isinstance(self.raw_tree[1], QStr) else self.number
            return self.raw_tree

        layers_node: List[SExpr] = ["layers"] + [QStr(layer) for layer in self.layers]
        tree: List[SExpr] = [
            "pad",
            QStr(self.number),
            self.type,
            self.shape,
            ["at", self.position.x, self.position.y, self.position.angle],
            ["size", self.size[0], self.size[1]],
        ]
        if self.drill:
            tree.append(["drill", self.drill])
        tree.append(layers_node)
        return tree


# ---------------------------------------------------------------------------
# FootprintGraphic
# ---------------------------------------------------------------------------


@dataclass
class FootprintGraphic:
    """A graphical element inside a footprint (line, circle, arc, text, …).

    The original S-expression tree is preserved verbatim for round-trip
    fidelity — graphic editing is out of scope for Phase 1.

    Attributes:
        tag:      Element type tag (e.g. ``"fp_line"``, ``"fp_text"``).
        raw_tree: Original S-expression.
    """

    tag: str = ""
    raw_tree: List[SExpr] = field(default_factory=list)

    @classmethod
    def from_tree(cls, tree: List[SExpr]) -> "FootprintGraphic":
        tag = str(tree[0]) if tree else ""
        return cls(tag=tag, raw_tree=tree)

    def to_tree(self) -> List[SExpr]:
        return self.raw_tree


# ---------------------------------------------------------------------------
# Footprint
# ---------------------------------------------------------------------------

_GRAPHIC_TAGS = {"fp_text", "fp_line", "fp_rect", "fp_circle", "fp_arc", "fp_poly", "fp_curve", "zone"}


@dataclass
class Footprint:
    """Model for a KiCad footprint file (.kicad_mod).

    Attributes:
        name:        Footprint name (top-level identifier).
        layer:       Primary copper layer (usually ``"F.Cu"``).
        description: Human-readable description.
        tags:        Space-separated keyword tags.
        attributes:  List of attribute keywords (e.g. ``"smd"``).
        graphics:    List of :class:`FootprintGraphic` elements.
        pads:        List of :class:`Pad` instances.
        models:      List of raw 3D model S-expression trees.
        properties:  Footprint-level properties.
        _extra:      Unrecognised top-level items.
    """

    name: str = ""
    layer: str = "F.Cu"
    description: str = ""
    tags: str = ""
    version: int = 0
    generator: str = ""
    generator_version: str = ""
    attributes: List[str] = field(default_factory=list)
    graphics: List[FootprintGraphic] = field(default_factory=list)
    pads: List[Pad] = field(default_factory=list)
    models: List[List[SExpr]] = field(default_factory=list)
    properties: List[Property] = field(default_factory=list)
    _extra: List[List[SExpr]] = field(default_factory=list, repr=False)

    # ------------------------------------------------------------------
    # Load / save
    # ------------------------------------------------------------------

    @classmethod
    def load(cls, path: str | os.PathLike) -> "Footprint":
        """Load a footprint from *path*.

        Args:
            path: Path to a ``.kicad_mod`` file.

        Returns:
            Parsed :class:`Footprint`.

        Raises:
            FileNotFoundError: If *path* does not exist.
            ValueError: If the file is not a valid KiCad footprint.
        """
        text = Path(path).read_text(encoding="utf-8")
        tree = parse(text)
        return cls._from_tree(tree)

    def save(self, path: str | os.PathLike) -> None:
        """Write the footprint to *path*.

        Args:
            path: Destination ``.kicad_mod`` file path.
        """
        tree = self._to_tree()
        text = serialize(tree, indent=0, number_precision=6)
        Path(path).write_text(text + "\n", encoding="utf-8")

    # ------------------------------------------------------------------
    # Pad operations
    # ------------------------------------------------------------------

    def add_pad(
        self,
        number: str,
        pad_type: str,
        shape: str,
        x: float,
        y: float,
        width: float,
        height: float,
        layers: Optional[List[str]] = None,
        drill: float = 0.0,
    ) -> Pad:
        """Add a new pad to the footprint.

        Args:
            number:   Pad number string.
            pad_type: Pad type (``"smd"``, ``"thru_hole"``, etc.).
            shape:    Pad shape (``"rect"``, ``"circle"``, ``"oval"``, etc.).
            x, y:     Centre position in mm.
            width:    Pad width in mm.
            height:   Pad height in mm.
            layers:   Layer list; defaults to ``["F.Cu", "F.Paste", "F.Mask"]``
                      for SMD or ``["*.Cu", "*.Mask"]`` for thru-hole.
            drill:    Drill diameter for thru-hole pads.

        Returns:
            The newly created :class:`Pad`.
        """
        if layers is None:
            if pad_type in ("thru_hole", "np_thru_hole"):
                layers = ["*.Cu", "*.Mask"]
            else:
                layers = ["F.Cu", "F.Paste", "F.Mask"]
        pad = Pad(
            number=number,
            type=pad_type,
            shape=shape,
            position=Position(x, y),
            size=(width, height),
            drill=drill,
            layers=layers,
        )
        self.pads.append(pad)
        return pad

    def remove_pad(self, number: str) -> bool:
        """Remove the first pad whose number equals *number*.

        Returns:
            ``True`` if removed, ``False`` if not found.
        """
        for i, pad in enumerate(self.pads):
            if pad.number == number:
                del self.pads[i]
                return True
        return False

    def renumber_pads(self, start: int = 1) -> None:
        """Renumber all pads sequentially starting from *start*.

        Pads are renumbered in their current list order.  When a pad's number
        is numeric, it is updated to the next integer; non-numeric pad numbers
        are left unchanged.

        Args:
            start: First pad number to use.
        """
        counter = start
        for pad in self.pads:
            if pad.number.isdigit():
                pad.number = str(counter)
                pad.raw_tree = None  # Force re-serialisation
                counter += 1

    def modify_pad(self, number: str, **kwargs: Any) -> bool:
        """Update attributes of the pad with the given *number*.

        Keyword arguments correspond to :class:`Pad` attributes.  Matched
        pads have their *raw_tree* cleared so updated fields are serialised.

        Returns:
            ``True`` if found and updated, ``False`` if not found.
        """
        for pad in self.pads:
            if pad.number == number:
                for attr, val in kwargs.items():
                    setattr(pad, attr, val)
                pad.raw_tree = None
                return True
        return False

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @classmethod
    def _from_tree(cls, tree: List[SExpr]) -> "Footprint":
        if not tree or tree[0] != "footprint":
            raise ValueError("Not a valid KiCad footprint file (expected 'footprint' root tag)")

        fp = cls()
        fp.name = str(tree[1]) if len(tree) > 1 else ""

        layer_node = _find(tree, "layer")
        if layer_node and len(layer_node) > 1:
            fp.layer = str(layer_node[1])

        version_node = _find(tree, "version")
        if version_node and len(version_node) > 1:
            fp.version = int(version_node[1])
        gen_node = _find(tree, "generator")
        if gen_node and len(gen_node) > 1:
            fp.generator = str(gen_node[1])
        gen_ver_node = _find(tree, "generator_version")
        if gen_ver_node and len(gen_ver_node) > 1:
            fp.generator_version = str(gen_ver_node[1])

        desc_node = _find(tree, "descr")
        if desc_node and len(desc_node) > 1:
            fp.description = str(desc_node[1])

        tags_node = _find(tree, "tags")
        if tags_node and len(tags_node) > 1:
            fp.tags = str(tags_node[1])

        attr_node = _find(tree, "attr")
        if attr_node:
            fp.attributes = [str(a) for a in attr_node[1:] if isinstance(a, (str, QStr))]

        _KNOWN = {
            "attr", "descr", "footprint", "fp_arc", "fp_circle",
            "fp_curve", "fp_line", "fp_poly", "fp_rect", "fp_text",
            "generator", "generator_version", "layer",
            "model", "pad", "property", "tags", "version", "zone",
        }

        for item in tree[2:]:
            if not isinstance(item, list) or not item:
                continue
            tag = str(item[0])
            if tag in _GRAPHIC_TAGS:
                fp.graphics.append(FootprintGraphic.from_tree(item))
            elif tag == "pad":
                fp.pads.append(Pad.from_tree(item))
            elif tag == "model":
                fp.models.append(item)
            elif tag == "property":
                key = str(item[1]) if len(item) > 1 else ""
                value = str(item[2]) if len(item) > 2 else ""
                fp.properties.append(Property(key=key, value=value))
            elif tag not in _KNOWN:
                fp._extra.append(item)

        return fp

    def _to_tree(self) -> List[SExpr]:
        tree: List[SExpr] = ["footprint", QStr(self.name)]
        tree.append(["layer", QStr(self.layer)])
        if self.version:
            tree.append(["version", self.version])
        if self.generator:
            tree.append(["generator", QStr(self.generator)])
        if self.generator_version:
            tree.append(["generator_version", QStr(self.generator_version)])
        if self.description:
            tree.append(["descr", QStr(self.description)])
        if self.tags:
            tree.append(["tags", QStr(self.tags)])
        if self.attributes:
            attr_node: List[SExpr] = ["attr"] + [a for a in self.attributes]
            tree.append(attr_node)
        for g in self.graphics:
            tree.append(g.to_tree())
        for pad in self.pads:
            tree.append(pad.to_tree())
        for model in self.models:
            tree.append(model)
        for p in self.properties:
            tree.append(["property", QStr(p.key), QStr(p.value)])
        for extra in self._extra:
            tree.append(extra)
        return tree
