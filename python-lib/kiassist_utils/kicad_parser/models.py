"""Base data models for KiCad file elements.

Typed dataclasses for common elements shared across all KiCad file formats:
schematic, symbol library, footprint, and PCB.
"""

from dataclasses import dataclass, field
from typing import List, Optional, Tuple
import uuid as _uuid

from .sexpr import SExpr, find_first, get_value


@dataclass
class Position:
    """2D position with optional rotation angle.

    KiCad coordinate system: origin at top-left, X right, Y down.
    All values in millimeters.
    """
    x: float = 0.0
    y: float = 0.0
    angle: float = 0.0

    def to_sexpr(self) -> SExpr:
        """Convert to S-expression: (at x y [angle])."""
        if self.angle != 0.0:
            return ["at", self.x, self.y, self.angle]
        return ["at", self.x, self.y]

    @classmethod
    def from_sexpr(cls, expr: SExpr) -> "Position":
        """Parse from S-expression: (at x y [angle])."""
        if not isinstance(expr, list) or len(expr) < 3:
            return cls()
        x = float(expr[1])
        y = float(expr[2])
        angle = float(expr[3]) if len(expr) > 3 else 0.0
        return cls(x=x, y=y, angle=angle)


@dataclass
class Color:
    """RGBA color representation.

    Values are 0-255 for RGB, 0.0-1.0 for alpha.
    """
    r: int = 0
    g: int = 0
    b: int = 0
    a: float = 0.0

    def to_sexpr(self) -> SExpr:
        """Convert to S-expression: (color r g b a)."""
        return ["color", self.r, self.g, self.b, self.a]

    @classmethod
    def from_sexpr(cls, expr: SExpr) -> "Color":
        """Parse from S-expression: (color r g b a)."""
        if not isinstance(expr, list) or len(expr) < 5:
            return cls()
        return cls(
            r=int(expr[1]),
            g=int(expr[2]),
            b=int(expr[3]),
            a=float(expr[4])
        )


@dataclass
class Stroke:
    """Line style properties.

    Used for wires, graphic lines, borders, etc.
    """
    width: float = 0.0
    type: str = "default"
    color: Optional[Color] = None

    def to_sexpr(self) -> SExpr:
        """Convert to S-expression: (stroke (width w) (type t) [(color ...)])."""
        result: List[SExpr] = ["stroke", ["width", self.width], ["type", self.type]]
        if self.color is not None:
            result.append(self.color.to_sexpr())
        return result

    @classmethod
    def from_sexpr(cls, expr: SExpr) -> "Stroke":
        """Parse from S-expression: (stroke (width w) (type t) [(color ...)])."""
        if not isinstance(expr, list):
            return cls()
        width = 0.0
        stroke_type = "default"
        color = None

        width_val = get_value(expr, "width")
        if width_val is not None:
            width = float(width_val)

        type_val = get_value(expr, "type")
        if type_val is not None:
            stroke_type = str(type_val)

        color_expr = find_first(expr, "color")
        if color_expr is not None:
            color = Color.from_sexpr(color_expr)

        return cls(width=width, type=stroke_type, color=color)


@dataclass
class FontConfig:
    """Font configuration for text elements."""
    size_x: float = 1.27
    size_y: float = 1.27
    thickness: float = 0.0
    bold: bool = False
    italic: bool = False
    face: str = ""

    def to_sexpr(self) -> SExpr:
        """Convert to S-expression: (font (size x y) [bold] [italic])."""
        result: List[SExpr] = ["font", ["size", self.size_x, self.size_y]]
        if self.face:
            result.append(["face", self.face])
        if self.thickness > 0:
            result.append(["thickness", self.thickness])
        if self.bold:
            result.append("bold")
        if self.italic:
            result.append("italic")
        return result

    @classmethod
    def from_sexpr(cls, expr: SExpr) -> "FontConfig":
        """Parse from S-expression."""
        if not isinstance(expr, list):
            return cls()

        size_x = 1.27
        size_y = 1.27
        thickness = 0.0
        bold = False
        italic = False
        face_str = ""

        size_expr = find_first(expr, "size")
        if size_expr is not None and len(size_expr) >= 3:
            size_x = float(size_expr[1])
            size_y = float(size_expr[2])

        thickness_val = get_value(expr, "thickness")
        if thickness_val is not None:
            thickness = float(thickness_val)

        face_val = get_value(expr, "face")
        if face_val is not None:
            face_str = str(face_val)

        bold = "bold" in expr
        italic = "italic" in expr

        return cls(size_x=size_x, size_y=size_y, thickness=thickness,
                   bold=bold, italic=italic, face=face_str)


@dataclass
class Effects:
    """Text formatting effects.

    Used for properties, labels, and other text elements.
    """
    font: FontConfig = field(default_factory=FontConfig)
    justify: str = ""
    hide: bool = False

    def to_sexpr(self) -> SExpr:
        """Convert to S-expression: (effects (font ...) [(justify ...)] [hide])."""
        result: List[SExpr] = ["effects", self.font.to_sexpr()]
        if self.justify:
            parts = self.justify.split()
            justify_expr: List[SExpr] = ["justify"]
            justify_expr.extend(parts)
            result.append(justify_expr)
        if self.hide:
            result.append("hide")
        return result

    @classmethod
    def from_sexpr(cls, expr: SExpr) -> "Effects":
        """Parse from S-expression: (effects (font ...) [(justify ...)] [hide])."""
        if not isinstance(expr, list):
            return cls()

        font = FontConfig()
        justify = ""
        hide = False

        font_expr = find_first(expr, "font")
        if font_expr is not None:
            font = FontConfig.from_sexpr(font_expr)

        justify_expr = find_first(expr, "justify")
        if justify_expr is not None:
            justify = " ".join(str(j) for j in justify_expr[1:])

        hide = "hide" in expr

        return cls(font=font, justify=justify, hide=hide)


@dataclass
class Property:
    """Key-value property with position and text formatting.

    Used for symbol properties (Reference, Value, Footprint, etc.).
    """
    key: str = ""
    value: str = ""
    id: int = -1
    position: Optional[Position] = None
    effects: Optional[Effects] = None

    def to_sexpr(self) -> SExpr:
        """Convert to S-expression: (property "key" "value" (at ...) (effects ...))."""
        result: List[SExpr] = ["property", self.key, self.value]
        if self.id >= 0:
            result.append(["id", self.id])
        if self.position is not None:
            result.append(self.position.to_sexpr())
        if self.effects is not None:
            result.append(self.effects.to_sexpr())
        return result

    @classmethod
    def from_sexpr(cls, expr: SExpr) -> "Property":
        """Parse from S-expression: (property "key" "value" ...)."""
        if not isinstance(expr, list) or len(expr) < 3:
            return cls()

        key = str(expr[1])
        value = str(expr[2])
        prop_id = -1
        position = None
        effects = None

        id_val = get_value(expr, "id")
        if id_val is not None:
            prop_id = int(id_val)

        at_expr = find_first(expr, "at")
        if at_expr is not None:
            position = Position.from_sexpr(at_expr)

        effects_expr = find_first(expr, "effects")
        if effects_expr is not None:
            effects = Effects.from_sexpr(effects_expr)

        return cls(key=key, value=value, id=prop_id, position=position,
                   effects=effects)


def new_uuid() -> str:
    """Generate a new UUID v4 string for KiCad elements."""
    return str(_uuid.uuid4())


@dataclass
class Pts:
    """Coordinate point list.

    Used for wires, polylines, and other multi-point elements.
    """
    points: List[Position] = field(default_factory=list)

    def to_sexpr(self) -> SExpr:
        """Convert to S-expression: (pts (xy x y) (xy x y) ...)."""
        result: List[SExpr] = ["pts"]
        for pt in self.points:
            result.append(["xy", pt.x, pt.y])
        return result

    @classmethod
    def from_sexpr(cls, expr: SExpr) -> "Pts":
        """Parse from S-expression: (pts (xy x y) ...)."""
        if not isinstance(expr, list):
            return cls()

        points = []
        for item in expr:
            if (isinstance(item, list) and len(item) >= 3 and
                    isinstance(item[0], str) and item[0] == "xy"):
                points.append(Position(x=float(item[1]), y=float(item[2])))
        return cls(points=points)


@dataclass
class Fill:
    """Fill style for graphic elements."""
    type: str = "none"
    color: Optional[Color] = None

    def to_sexpr(self) -> SExpr:
        """Convert to S-expression: (fill (type t) [(color ...)])."""
        result: List[SExpr] = ["fill", ["type", self.type]]
        if self.color is not None:
            result.append(self.color.to_sexpr())
        return result

    @classmethod
    def from_sexpr(cls, expr: SExpr) -> "Fill":
        """Parse from S-expression: (fill (type t) [(color ...)])."""
        if not isinstance(expr, list):
            return cls()
        fill_type = "none"
        color = None

        type_val = get_value(expr, "type")
        if type_val is not None:
            fill_type = str(type_val)

        color_expr = find_first(expr, "color")
        if color_expr is not None:
            color = Color.from_sexpr(color_expr)

        return cls(type=fill_type, color=color)
