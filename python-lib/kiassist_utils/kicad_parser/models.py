"""Shared dataclasses for KiCad file models.

All coordinate values are in millimetres.  KiCad uses a Y-down coordinate
system (positive Y goes downward on screen).
"""

from __future__ import annotations

import uuid as _uuid_mod
from dataclasses import dataclass, field
from typing import List, Optional, Tuple


@dataclass
class Position:
    """A 2-D position with an optional rotation angle.

    Attributes:
        x:     X coordinate in mm.
        y:     Y coordinate in mm.
        angle: Rotation angle in degrees (default 0).
    """

    x: float
    y: float
    angle: float = 0.0

    def __iter__(self):
        yield self.x
        yield self.y
        yield self.angle


@dataclass
class Stroke:
    """Line / wire stroke style.

    Attributes:
        width: Stroke width in mm (0 = default/thin).
        type:  Stroke type keyword (``default``, ``dash``, ``dot``, …).
        color: RGBA colour tuple with components in [0, 1] (default transparent).
    """

    width: float = 0.0
    type: str = "default"
    color: Tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)


@dataclass
class Effects:
    """Text rendering effects.

    Attributes:
        font_size:  (width, height) in mm.
        bold:       Bold text.
        italic:     Italic text.
        justify:    Horizontal justification keyword (``left``, ``right``, or
                    empty for centred).
        hide:       Whether the text is hidden.
    """

    font_size: Tuple[float, float] = (1.27, 1.27)
    bold: bool = False
    italic: bool = False
    justify: str = ""
    hide: bool = False


@dataclass
class Property:
    """A key/value property attached to a symbol or other entity.

    Attributes:
        key:      Property name (e.g. ``"Reference"``, ``"Value"``).
        value:    Property value string.
        position: Position of the property label on the schematic, if any.
        effects:  Text rendering effects for the label.
    """

    key: str
    value: str
    position: Optional[Position] = None
    effects: Optional[Effects] = None


@dataclass
class KiUUID:
    """Wrapper around a KiCad v4 UUID string.

    Attributes:
        value: UUID string in hyphenated format (e.g. ``"xxxxxxxx-…"``).
    """

    value: str = ""

    @classmethod
    def new(cls) -> "KiUUID":
        """Generate a new random UUID."""
        return cls(value=str(_uuid_mod.uuid4()))

    def __str__(self) -> str:
        return self.value

    def __bool__(self) -> bool:
        return bool(self.value)


@dataclass
class Pts:
    """An ordered list of coordinate points (used for wires, polygons, etc.).

    Attributes:
        points: Sequence of :class:`Position` objects.
    """

    points: List[Position] = field(default_factory=list)

    def add(self, x: float, y: float) -> None:
        """Append a new point."""
        self.points.append(Position(x, y))

    def __len__(self) -> int:
        return len(self.points)

    def __iter__(self):
        return iter(self.points)
