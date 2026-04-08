"""Upgrade legacy KiCad S-expression formats to modern (KiCad 8+) syntax.

The ``easyeda2kicad`` library emits:
- **Symbols**: version 6 – mostly compatible, needs version bump and minor tweaks.
- **Footprints**: legacy ``(module ...)`` format (KiCad 5 era) – needs full migration.

KiCad 8/9/10 can *mostly* read older files but may silently drop data or show
warnings.  We convert up-front so the S-expressions stored in the project are
always in the modern format.

Conversion rules implemented here:

Footprint (``(module ...)`` → ``(footprint ...)``):
 1. ``(module lib:name ...)`` → ``(footprint "name" (version 20240108) ...)``
 2. ``(tedit ...)`` removed  (no longer used)
 3. ``(width N)`` on graphic items → ``(stroke (width N) (type default))``
 4. ``(fp_arc ... (angle deg))`` → ``(fp_arc ... (mid mx my))``
 5. ``(fp_circle (center ...) (end ...))`` stroke upgrade
 6. ``(fp_text ... (layer ...) ...)`` → unchanged (still valid)

Symbol library:
 1. ``(version 6)`` → ``(version 20231120)``
 2. Property ``(id N)`` removed  (KiCad 8 no longer uses numeric IDs)
"""

from __future__ import annotations

import math
import re
from typing import Optional

from ..kicad_parser.sexpr import QStr, parse, serialize


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def upgrade_footprint(sexpr_text: str) -> str:
    """Upgrade a legacy footprint string to modern KiCad 8+ format.

    If the footprint is already modern (starts with ``(footprint ...)``) it is
    returned unchanged.

    Returns the upgraded S-expression string.
    """
    stripped = sexpr_text.strip()
    if not stripped:
        return sexpr_text

    # Quick check: already modern?
    if stripped.startswith("(footprint"):
        return sexpr_text

    # Must start with (module ...)
    if not stripped.startswith("(module"):
        return sexpr_text  # unknown format – leave alone

    tree = parse(stripped)
    if not tree or not isinstance(tree, list):
        return sexpr_text

    _upgrade_footprint_tree(tree)
    return serialize(tree)


def upgrade_symbol_lib(sexpr_text: str) -> str:
    """Upgrade a legacy symbol library string to modern KiCad 8+ format.

    If the file already uses version >= 20231120 it is returned unchanged.

    Returns the upgraded S-expression string.
    """
    stripped = sexpr_text.strip()
    if not stripped:
        return sexpr_text

    if not stripped.startswith("(kicad_symbol_lib"):
        return sexpr_text

    tree = parse(stripped)
    if not tree or not isinstance(tree, list):
        return sexpr_text

    _upgrade_symbol_tree(tree)
    return serialize(tree)


def needs_footprint_upgrade(sexpr_text: str) -> bool:
    """Return True if the footprint uses the legacy ``(module ...)`` format."""
    return sexpr_text.strip().startswith("(module")


def needs_symbol_upgrade(sexpr_text: str) -> bool:
    """Return True if the symbol library uses a version < 20231120."""
    m = re.search(r"\(version\s+(\d+)\)", sexpr_text[:500])
    if not m:
        return False
    return int(m.group(1)) < 20231120


# ---------------------------------------------------------------------------
# Footprint tree transforms
# ---------------------------------------------------------------------------

_MODERN_FP_VERSION = 20240108  # KiCad 8.0


def _upgrade_footprint_tree(tree: list) -> None:
    """In-place upgrade of a parsed ``(module ...)`` tree."""
    # 1. Rename module → footprint and clean up the lib:name
    if tree[0] == "module":
        tree[0] = "footprint"

    # The second element is typically "lib:name" — extract just the name
    if len(tree) > 1 and isinstance(tree[1], (str, QStr)):
        name = str(tree[1])
        if ":" in name:
            name = name.split(":", 1)[1]
        tree[1] = QStr(name)

    # 2. Insert (version ...) and (generator ...) after the name
    _remove_nodes(tree, "tedit")
    _remove_nodes(tree, "version")
    _remove_nodes(tree, "generator")
    _remove_nodes(tree, "generator_version")

    # Insert version + generator as the first children after the name
    tree.insert(2, ["version", _MODERN_FP_VERSION])
    tree.insert(3, ["generator", QStr("kiassist")])
    tree.insert(4, ["generator_version", QStr("1.0")])

    # Also update (layer F.Cu) → (layer "F.Cu") for consistency
    for i, node in enumerate(tree):
        if isinstance(node, list) and len(node) == 2 and node[0] == "layer":
            if isinstance(node[1], str) and not isinstance(node[1], QStr):
                tree[i] = ["layer", QStr(str(node[1]))]

    # 3. Walk all children and upgrade graphic items
    for i, node in enumerate(tree):
        if not isinstance(node, list) or len(node) < 1:
            continue
        tag = node[0]
        if tag in ("fp_line", "fp_rect"):
            _upgrade_stroke_width(node)
        elif tag == "fp_circle":
            _upgrade_stroke_width(node)
        elif tag == "fp_arc":
            _upgrade_fp_arc(node)
            _upgrade_stroke_width(node)
        elif tag == "fp_poly":
            _upgrade_stroke_width(node)
        elif tag == "fp_text":
            _upgrade_fp_text(node)
        elif tag == "pad":
            _upgrade_pad(node)


def _upgrade_stroke_width(node: list) -> None:
    """Replace ``(width N)`` with ``(stroke (width N) (type default))``."""
    for i, child in enumerate(node):
        if isinstance(child, list) and len(child) == 2 and child[0] == "width":
            w = child[1]
            node[i] = ["stroke", ["width", w], ["type", "default"]]
            return
    # If there's a (layer ...) but no stroke yet, add a default one
    if not _find(node, "stroke"):
        # Insert before layer if it exists
        for i, child in enumerate(node):
            if isinstance(child, list) and len(child) >= 2 and child[0] == "layer":
                node.insert(i, ["stroke", ["width", 0], ["type", "default"]])
                return


def _upgrade_fp_arc(node: list) -> None:
    """Convert ``(start cx cy) (end sx sy) (angle deg)`` to ``(start sx sy) (mid mx my) (end ex ey)``."""
    start_node = _find(node, "start")
    end_node = _find(node, "end")
    angle_node = _find(node, "angle")

    if not start_node or not end_node or not angle_node:
        return  # already modern or malformed

    # Old format: start = centre, end = arc start point, angle = sweep
    cx = float(start_node[1])
    cy = float(start_node[2])
    sx = float(end_node[1])
    sy = float(end_node[2])
    angle_deg = float(angle_node[1])

    r = math.hypot(sx - cx, sy - cy)
    if r < 1e-10:
        return

    start_angle = math.atan2(sy - cy, sx - cx)
    sweep_rad = (angle_deg * math.pi) / 180.0
    mid_angle = start_angle + sweep_rad / 2.0
    end_angle = start_angle + sweep_rad

    mx = cx + r * math.cos(mid_angle)
    my = cy + r * math.sin(mid_angle)
    ex = cx + r * math.cos(end_angle)
    ey = cy + r * math.sin(end_angle)

    # Round to 4 decimal places
    def rnd(v: float) -> float:
        return round(v, 4)

    # Rewrite: start → arc start point, new mid node, end → arc end point
    start_node[1] = rnd(sx)
    start_node[2] = rnd(sy)

    end_node[1] = rnd(ex)
    end_node[2] = rnd(ey)

    # Insert (mid mx my) after start node
    mid_new = ["mid", rnd(mx), rnd(my)]
    start_idx = node.index(start_node)
    node.insert(start_idx + 1, mid_new)

    # Remove (angle ...)
    node.remove(angle_node)


def _upgrade_fp_text(node: list) -> None:
    """Upgrade fp_text layer quoting and stroke."""
    _upgrade_stroke_width(node)
    # Ensure layer value is quoted
    layer_node = _find(node, "layer")
    if layer_node and len(layer_node) >= 2:
        if isinstance(layer_node[1], str) and not isinstance(layer_node[1], QStr):
            layer_node[1] = QStr(str(layer_node[1]))


def _upgrade_pad(node: list) -> None:
    """Quote layer names in pad nodes."""
    layers_node = _find(node, "layers")
    if layers_node:
        for i in range(1, len(layers_node)):
            v = layers_node[i]
            if isinstance(v, str) and not isinstance(v, QStr):
                layers_node[i] = QStr(str(v))


# ---------------------------------------------------------------------------
# Symbol tree transforms
# ---------------------------------------------------------------------------

_MODERN_SYM_VERSION = 20231120  # KiCad 8.0


def _upgrade_symbol_tree(tree: list) -> None:
    """In-place upgrade of a parsed ``(kicad_symbol_lib ...)`` tree."""
    # 1. Update version
    ver_node = _find(tree, "version")
    if ver_node:
        if isinstance(ver_node[1], (int, float)) and ver_node[1] < _MODERN_SYM_VERSION:
            ver_node[1] = _MODERN_SYM_VERSION
    else:
        tree.insert(1, ["version", _MODERN_SYM_VERSION])

    # 2. Update generator
    gen_node = _find(tree, "generator")
    if gen_node:
        gen_node[1] = QStr("kiassist")
    else:
        tree.insert(2, ["generator", QStr("kiassist")])

    # Add generator_version if missing
    if not _find(tree, "generator_version"):
        gen_idx = tree.index(gen_node or _find(tree, "generator"))
        tree.insert(gen_idx + 1, ["generator_version", QStr("1.0")])

    # 3. Walk symbols and clean up properties
    for node in tree:
        if isinstance(node, list) and len(node) >= 2 and node[0] == "symbol":
            _upgrade_symbol_properties(node)


def _upgrade_symbol_properties(sym: list) -> None:
    """Remove (id N) from properties — KiCad 8 doesn't use them."""
    for node in sym:
        if isinstance(node, list) and len(node) >= 3 and node[0] == "property":
            _remove_nodes(node, "id")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _find(tree: list, tag: str) -> Optional[list]:
    """Find the first child list with the given tag."""
    for child in tree:
        if isinstance(child, list) and len(child) >= 1 and child[0] == tag:
            return child
    return None


def _remove_nodes(tree: list, tag: str) -> None:
    """Remove all child lists with the given tag."""
    tree[:] = [
        child for child in tree
        if not (isinstance(child, list) and len(child) >= 1 and child[0] == tag)
    ]
