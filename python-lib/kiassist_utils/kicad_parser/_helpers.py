"""Shared helper utilities for KiCad S-expression parser modules.

These helpers are used across :mod:`schematic`, :mod:`symbol_lib`,
:mod:`footprint`, :mod:`pcb`, and :mod:`library` to avoid duplication.
"""

from __future__ import annotations

from typing import Any, List, Optional

from .models import Effects, Position
from .sexpr import SExpr

# ---------------------------------------------------------------------------
# S-expression tree traversal
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


# ---------------------------------------------------------------------------
# KiCad sub-expression parsers
# ---------------------------------------------------------------------------


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
