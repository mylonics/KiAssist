"""Unified KiAssist MCP server.

Exposes KiCad schematic, symbol library, footprint, IPC bridge, and project
context operations as MCP tools via the FastMCP framework.

Usage (stdio transport, for use with MCP-compatible AI clients)::

    kiassist-mcp            # registered as a console script

In-process usage (no network overhead)::

    import asyncio
    from kiassist_utils.mcp_server import in_process_call

    result = asyncio.run(in_process_call("schematic_open", {"path": "my.kicad_sch"}))
"""

from __future__ import annotations

import json
import logging
import math
import os
import platform
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from mcp.server.fastmcp import FastMCP

logger = logging.getLogger(__name__)

from .kicad_ipc import detect_kicad_instances, get_open_project_paths
from .kicad_parser.footprint import Footprint
from .kicad_parser.library import LibraryDiscovery
from .kicad_parser.models import KiUUID, Position, Property
from .kicad_parser.pcb import PCBBoard
from .kicad_parser.schematic import (
    GlobalLabel,
    Schematic,
    _find,
    _find_all,
    _parse_position,
)
from .kicad_parser.symbol_lib import Pin, SymbolDef, SymbolLibrary, SymbolUnit
from .context.memory import ProjectMemory

# ---------------------------------------------------------------------------
# Server instance
# ---------------------------------------------------------------------------

mcp: FastMCP = FastMCP(
    name="KiAssist",
    instructions=(
        "KiAssist MCP server: tools for reading and writing KiCad schematic, "
        "symbol library, footprint, and PCB files, plus KiCad IPC bridge tools."
    ),
)

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _ok(data: Any) -> Dict[str, Any]:
    """Wrap a successful result payload."""
    return {"status": "ok", "data": data}


def _err(message: str) -> Dict[str, Any]:
    """Wrap an error result payload."""
    return {"status": "error", "message": message}


def _pos_dict(pos: Position) -> Dict[str, float]:
    return {"x": pos.x, "y": pos.y, "angle": pos.angle}


def _safe_save(obj: Any, path: str | os.PathLike) -> None:
    """Save *obj* to *path* with a ``.bak`` backup and an atomic rename.

    The sequence is:
    1. If *path* already exists, copy it to ``<path>.bak`` (backup).
    2. Write the new content to a sibling temporary file in the same directory.
    3. Atomically replace *path* with the temp file via ``os.replace()``.

    Because the write goes to a temp file first, the original is never
    truncated: if the write raises the destination is untouched and the
    ``.bak`` copy is available for manual recovery.

    Args:
        obj:  Object with a ``save(path)`` method (Schematic, SymbolLibrary, …).
        path: Destination file path.

    Raises:
        Any exception raised by ``obj.save()`` or ``os.replace()`` is re-raised.
    """
    dest = Path(path)
    if dest.exists():
        shutil.copy2(dest, str(dest) + ".bak")
    tmp_fd, tmp_path = tempfile.mkstemp(dir=dest.parent, suffix=".tmp")
    try:
        os.close(tmp_fd)
        obj.save(tmp_path)
        os.replace(tmp_path, str(dest))
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise

# ===========================================================================
# 2.2  Schematic Tools
# ===========================================================================


@mcp.tool()
def schematic_open(path: str) -> Dict[str, Any]:
    """Load a schematic file and return a summary.

    Args:
        path: Absolute or relative path to a ``.kicad_sch`` file.

    Returns:
        Summary dict with ``component_count``, ``sheet_count``, ``paper``,
        ``title``, and ``version``.
    """
    try:
        sch = Schematic.load(path)
    except FileNotFoundError:
        return _err(f"File not found: {path}")
    except Exception as exc:  # noqa: BLE001
        return _err(f"Failed to load schematic: {exc}")

    title = ""
    if sch.title_block:
        title = sch.title_block.title
    return _ok(
        {
            "path": str(path),
            "version": sch.version,
            "paper": sch.paper,
            "title": title,
            "component_count": len(sch.symbols),
            "wire_count": len(sch.wires),
            "sheet_count": len(sch.sheets),
            "lib_symbol_count": len(sch.lib_symbols),
        }
    )


@mcp.tool()
def schematic_list_symbols(path: str) -> Dict[str, Any]:
    """List all placed symbols in a schematic.

    Args:
        path: Path to a ``.kicad_sch`` file.

    Returns:
        List of dicts, each with ``reference``, ``value``, ``footprint``,
        ``lib_id``, and ``position``.
    """
    try:
        sch = Schematic.load(path)
    except Exception as exc:  # noqa: BLE001
        return _err(str(exc))

    result = []
    for sym in sch.symbols:
        result.append(
            {
                "reference": sym.reference,
                "value": sym.value,
                "footprint": sym.footprint,
                "lib_id": sym.lib_id,
                "position": _pos_dict(sym.position),
            }
        )
    return _ok(result)


@mcp.tool()
def schematic_get_symbol(path: str, reference: str) -> Dict[str, Any]:
    """Get detailed information for a specific symbol.

    Args:
        path:      Path to a ``.kicad_sch`` file.
        reference: Reference designator (e.g. ``"U1"``).

    Returns:
        Dict with ``reference``, ``value``, ``footprint``, ``lib_id``,
        ``position``, ``properties``, ``pin_positions``, and
        ``connections`` (mapping pin number → net name).
    """
    try:
        sch = Schematic.load(path)
    except Exception as exc:  # noqa: BLE001
        return _err(str(exc))

    syms = sch.find_symbols(reference=reference)
    if not syms:
        return _err(f"Symbol '{reference}' not found")

    sym = syms[0]
    props = {p.key: p.value for p in sym.properties}
    pin_positions = {
        pin: _pos_dict(pos) for pin, pos in sch.get_pin_positions(reference).items()
    }

    # Build connections: map pin_number → net name using get_connected_nets
    nets = sch.get_connected_nets()
    # Invert to: "RefDes:PinNum" → net_name
    pin_to_net: Dict[str, str] = {}
    for net_name, pin_refs in nets.items():
        for pin_ref in pin_refs:
            pin_to_net[pin_ref] = net_name
    connections = {
        pin_num: pin_to_net.get(f"{reference}:{pin_num}", "")
        for pin_num in pin_positions
    }

    return _ok(
        {
            "reference": sym.reference,
            "value": sym.value,
            "footprint": sym.footprint,
            "lib_id": sym.lib_id,
            "position": _pos_dict(sym.position),
            "properties": props,
            "pin_positions": pin_positions,
            "connections": connections,
        }
    )


@mcp.tool()
def schematic_add_symbol(
    path: str,
    lib_id: str,
    x: float,
    y: float,
    reference: str = "U?",
    value: str = "",
    footprint: str = "",
    angle: float = 0.0,
) -> Dict[str, Any]:
    """Place a symbol on the schematic and save.

    Args:
        path:      Path to a ``.kicad_sch`` file (modified in-place).
        lib_id:    Library identifier string (e.g. ``"Device:R"``).
        x:         X position in mm.
        y:         Y position in mm.
        reference: Reference designator (e.g. ``"R1"``).
        value:     Component value string.
        footprint: Footprint assignment.
        angle:     Rotation angle in degrees.

    Returns:
        Dict with the added symbol's ``reference`` and ``position``.
    """
    try:
        sch = Schematic.load(path)
    except Exception as exc:  # noqa: BLE001
        return _err(str(exc))

    sym = sch.add_symbol(lib_id, x, y, reference, value, footprint, angle)
    try:
        _safe_save(sch, path)
    except Exception as exc:  # noqa: BLE001
        return _err(f"Failed to save schematic: {exc}")

    return _ok({"reference": sym.reference, "position": _pos_dict(sym.position)})


@mcp.tool()
def schematic_remove_symbol(path: str, reference: str) -> Dict[str, Any]:
    """Remove a symbol from the schematic by reference designator.

    Args:
        path:      Path to a ``.kicad_sch`` file (modified in-place).
        reference: Reference designator to remove.

    Returns:
        ``{"removed": true}`` on success or an error.
    """
    try:
        sch = Schematic.load(path)
    except Exception as exc:  # noqa: BLE001
        return _err(str(exc))

    removed = sch.remove_symbol(reference)
    if not removed:
        return _err(f"Symbol '{reference}' not found")
    try:
        _safe_save(sch, path)
    except Exception as exc:  # noqa: BLE001
        return _err(f"Failed to save schematic: {exc}")

    return _ok({"removed": True})


@mcp.tool()
def schematic_modify_symbol(
    path: str,
    reference: str,
    value: Optional[str] = None,
    footprint: Optional[str] = None,
    properties: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """Update properties on a placed schematic symbol.

    Args:
        path:       Path to a ``.kicad_sch`` file (modified in-place).
        reference:  Reference designator of the symbol to modify.
        value:      New value string (optional).
        footprint:  New footprint string (optional).
        properties: Arbitrary property key→value pairs to set (optional).

    Returns:
        The updated symbol's basic info on success.
    """
    try:
        sch = Schematic.load(path)
    except Exception as exc:  # noqa: BLE001
        return _err(str(exc))

    syms = sch.find_symbols(reference=reference)
    if not syms:
        return _err(f"Symbol '{reference}' not found")

    sym = syms[0]
    prop_map = {p.key: p for p in sym.properties}

    if value is not None:
        if "Value" in prop_map:
            prop_map["Value"].value = value
        else:
            sym.properties.append(Property("Value", value, sym.position))

    if footprint is not None:
        if "Footprint" in prop_map:
            prop_map["Footprint"].value = footprint
        else:
            sym.properties.append(Property("Footprint", footprint, sym.position))

    if properties:
        for k, v in properties.items():
            if k in prop_map:
                prop_map[k].value = v
            else:
                sym.properties.append(Property(k, v, sym.position))

    # Clear raw_tree so the symbol is re-serialised from dataclass fields.
    sym.raw_tree = None  # type: ignore[assignment]
    try:
        _safe_save(sch, path)
    except Exception as exc:  # noqa: BLE001
        return _err(f"Failed to save schematic: {exc}")

    return _ok(
        {
            "reference": sym.reference,
            "value": sym.value,
            "footprint": sym.footprint,
        }
    )


@mcp.tool()
def schematic_add_wire(
    path: str, x1: float, y1: float, x2: float, y2: float
) -> Dict[str, Any]:
    """Add a wire segment between two points.

    Args:
        path:       Path to a ``.kicad_sch`` file (modified in-place).
        x1, y1:     Start coordinate in mm.
        x2, y2:     End coordinate in mm.

    Returns:
        ``{"added": true}`` on success.
    """
    try:
        sch = Schematic.load(path)
    except Exception as exc:  # noqa: BLE001
        return _err(str(exc))

    sch.add_wire(x1, y1, x2, y2)
    try:
        _safe_save(sch, path)
    except Exception as exc:  # noqa: BLE001
        return _err(f"Failed to save schematic: {exc}")

    return _ok({"added": True})


@mcp.tool()
def schematic_connect_pins(
    path: str, pin_a: str, pin_b: str
) -> Dict[str, Any]:
    """Auto-route a straight wire between two pin references.

    This draws a direct wire between the resolved positions of two pins.
    For non-aligned pins the wire goes first horizontally then vertically
    via an intermediate point.

    Args:
        path:  Path to a ``.kicad_sch`` file (modified in-place).
        pin_a: Pin reference string in ``"RefDes:PinNum"`` format (e.g. ``"R1:1"``).
        pin_b: Pin reference string in ``"RefDes:PinNum"`` format (e.g. ``"U1:5"``).

    Returns:
        Number of wire segments added.
    """
    try:
        sch = Schematic.load(path)
    except Exception as exc:  # noqa: BLE001
        return _err(str(exc))

    def _resolve_pin(pin_ref: str):
        parts = pin_ref.split(":", 1)
        if len(parts) != 2:
            return None
        ref, pin_num = parts
        positions = sch.get_pin_positions(ref)
        return positions.get(pin_num)

    pos_a = _resolve_pin(pin_a)
    if pos_a is None:
        return _err(f"Could not resolve pin position for '{pin_a}'")
    pos_b = _resolve_pin(pin_b)
    if pos_b is None:
        return _err(f"Could not resolve pin position for '{pin_b}'")

    segments = 0
    if abs(pos_a.x - pos_b.x) < 1e-4 or abs(pos_a.y - pos_b.y) < 1e-4:
        # Collinear — single segment
        sch.add_wire(pos_a.x, pos_a.y, pos_b.x, pos_b.y)
        segments = 1
    else:
        # L-shape via midpoint
        mid_x, mid_y = pos_b.x, pos_a.y
        sch.add_wire(pos_a.x, pos_a.y, mid_x, mid_y)
        sch.add_wire(mid_x, mid_y, pos_b.x, pos_b.y)
        segments = 2

    try:
        _safe_save(sch, path)
    except Exception as exc:  # noqa: BLE001
        return _err(f"Failed to save schematic: {exc}")

    return _ok({"segments_added": segments})


@mcp.tool()
def schematic_add_label(
    path: str,
    text: str,
    x: float,
    y: float,
    angle: float = 0.0,
    global_label: bool = False,
) -> Dict[str, Any]:
    """Add a net label (or global label) at a position.

    Args:
        path:         Path to a ``.kicad_sch`` file (modified in-place).
        text:         Label / net name.
        x, y:         Position in mm.
        angle:        Rotation angle in degrees.
        global_label: If ``True``, add a global label instead of a net label.

    Returns:
        ``{"added": true, "type": "label"|"global_label"}`` on success.
    """
    try:
        sch = Schematic.load(path)
    except Exception as exc:  # noqa: BLE001
        return _err(str(exc))

    if global_label:
        gl = GlobalLabel()
        gl.text = text
        gl.position = Position(x, y, angle)
        gl.uuid = KiUUID.new()
        sch.global_labels.append(gl)
        label_type = "global_label"
    else:
        sch.add_label(text, x, y, angle)
        label_type = "label"

    try:
        _safe_save(sch, path)
    except Exception as exc:  # noqa: BLE001
        return _err(f"Failed to save schematic: {exc}")

    return _ok({"added": True, "type": label_type})


@mcp.tool()
def schematic_get_nets(path: str) -> Dict[str, Any]:
    """List all nets and their connected pins.

    Args:
        path: Path to a ``.kicad_sch`` file.

    Returns:
        Dict mapping net name → list of ``"RefDes:PinNum"`` strings.
    """
    try:
        sch = Schematic.load(path)
    except Exception as exc:  # noqa: BLE001
        return _err(str(exc))

    nets = sch.get_connected_nets()
    return _ok(nets)


@mcp.tool()
def schematic_find_pins(
    path: str,
    reference: Optional[str] = None,
    pin_name: Optional[str] = None,
) -> Dict[str, Any]:
    """Find pins matching a reference designator or pin name pattern.

    Args:
        path:      Path to a ``.kicad_sch`` file.
        reference: Filter by reference designator prefix (substring match).
        pin_name:  Filter by pin name (substring match, case-insensitive).

    Returns:
        List of dicts with ``symbol_reference``, ``pin_number``, ``pin_name``,
        and ``position``.
    """
    try:
        sch = Schematic.load(path)
    except Exception as exc:  # noqa: BLE001
        return _err(str(exc))

    results = []
    for sym in sch.symbols:
        ref = sym.reference
        if reference is not None and reference.lower() not in ref.lower():
            continue
        for lib_sym in sch.lib_symbols:
            if lib_sym.name != sym.lib_id and not lib_sym.name.endswith(
                ":" + sym.lib_id.split(":")[-1]
            ):
                continue
            if lib_sym.raw_tree:
                for unit in _find_all(lib_sym.raw_tree, "symbol"):
                    for pin_tree in _find_all(unit, "pin"):
                        name_node = _find(pin_tree, "name")
                        num_node = _find(pin_tree, "number")
                        at_node = _find(pin_tree, "at")
                        p_name = str(name_node[1]) if name_node and len(name_node) > 1 else ""
                        p_num = str(num_node[1]) if num_node and len(num_node) > 1 else ""
                        if pin_name is not None and pin_name.lower() not in p_name.lower():
                            continue
                        pin_pos = None
                        if at_node:
                            raw_pos = _parse_position(at_node)
                            angle_rad = math.radians(sym.position.angle)
                            rx = raw_pos.x * math.cos(angle_rad) - raw_pos.y * math.sin(angle_rad)
                            ry = raw_pos.x * math.sin(angle_rad) + raw_pos.y * math.cos(angle_rad)
                            pin_pos = _pos_dict(
                                Position(sym.position.x + rx, sym.position.y + ry)
                            )
                        results.append(
                            {
                                "symbol_reference": ref,
                                "pin_number": p_num,
                                "pin_name": p_name,
                                "position": pin_pos,
                            }
                        )
    return _ok(results)


@mcp.tool()
def schematic_get_power_pins(path: str, reference: str) -> Dict[str, Any]:
    """Get all power pins for a specific symbol.

    Args:
        path:      Path to a ``.kicad_sch`` file.
        reference: Reference designator of the target symbol.

    Returns:
        List of dicts with ``pin_number``, ``pin_name``, and ``position``.
    """
    try:
        sch = Schematic.load(path)
    except Exception as exc:  # noqa: BLE001
        return _err(str(exc))

    syms = sch.find_symbols(reference=reference)
    if not syms:
        return _err(f"Symbol '{reference}' not found")

    sym = syms[0]
    results = []
    for lib_sym in sch.lib_symbols:
        if lib_sym.name != sym.lib_id and not lib_sym.name.endswith(
            ":" + sym.lib_id.split(":")[-1]
        ):
            continue
        if lib_sym.raw_tree:
            for unit in _find_all(lib_sym.raw_tree, "symbol"):
                for pin_tree in _find_all(unit, "pin"):
                    # Power pins have type "power_in" or "power_out"
                    pin_type = str(pin_tree[1]) if len(pin_tree) > 1 else ""
                    if "power" not in pin_type.lower():
                        continue
                    name_node = _find(pin_tree, "name")
                    num_node = _find(pin_tree, "number")
                    at_node = _find(pin_tree, "at")
                    p_name = str(name_node[1]) if name_node and len(name_node) > 1 else ""
                    p_num = str(num_node[1]) if num_node and len(num_node) > 1 else ""
                    pin_pos = None
                    if at_node:
                        raw_pos = _parse_position(at_node)
                        angle_rad = math.radians(sym.position.angle)
                        rx = raw_pos.x * math.cos(angle_rad) - raw_pos.y * math.sin(angle_rad)
                        ry = raw_pos.x * math.sin(angle_rad) + raw_pos.y * math.cos(angle_rad)
                        pin_pos = _pos_dict(
                            Position(sym.position.x + rx, sym.position.y + ry)
                        )
                    results.append(
                        {
                            "pin_number": p_num,
                            "pin_name": p_name,
                            "position": pin_pos,
                        }
                    )
    return _ok(results)


@mcp.tool()
def schematic_add_junction(path: str, x: float, y: float) -> Dict[str, Any]:
    """Add a junction marker at a coordinate.

    Args:
        path: Path to a ``.kicad_sch`` file (modified in-place).
        x:    X position in mm.
        y:    Y position in mm.

    Returns:
        ``{"added": true}`` on success.
    """
    try:
        sch = Schematic.load(path)
    except Exception as exc:  # noqa: BLE001
        return _err(str(exc))

    sch.add_junction(x, y)
    try:
        _safe_save(sch, path)
    except Exception as exc:  # noqa: BLE001
        return _err(f"Failed to save schematic: {exc}")

    return _ok({"added": True})


@mcp.tool()
def schematic_add_no_connect(path: str, x: float, y: float) -> Dict[str, Any]:
    """Add a no-connect marker at a coordinate.

    Args:
        path: Path to a ``.kicad_sch`` file (modified in-place).
        x:    X position in mm.
        y:    Y position in mm.

    Returns:
        ``{"added": true}`` on success.
    """
    try:
        sch = Schematic.load(path)
    except Exception as exc:  # noqa: BLE001
        return _err(str(exc))

    sch.add_no_connect(x, y)
    try:
        _safe_save(sch, path)
    except Exception as exc:  # noqa: BLE001
        return _err(f"Failed to save schematic: {exc}")

    return _ok({"added": True})


@mcp.tool()
def schematic_search(
    path: str, query: str
) -> Dict[str, Any]:
    """Fuzzy search for symbols across references, values, and properties.

    Args:
        path:  Path to a ``.kicad_sch`` file.
        query: Search string (case-insensitive substring match).

    Returns:
        List of matching symbol dicts.
    """
    try:
        sch = Schematic.load(path)
    except Exception as exc:  # noqa: BLE001
        return _err(str(exc))

    q = query.lower()
    results = []
    for sym in sch.symbols:
        props = {p.key: p.value for p in sym.properties}
        # Match if query appears in any field
        searchable = [sym.reference, sym.value, sym.footprint, sym.lib_id] + list(
            props.values()
        )
        if any(q in s.lower() for s in searchable):
            results.append(
                {
                    "reference": sym.reference,
                    "value": sym.value,
                    "footprint": sym.footprint,
                    "lib_id": sym.lib_id,
                    "position": _pos_dict(sym.position),
                    "properties": props,
                }
            )
    return _ok(results)


# ===========================================================================
# 2.3  Symbol Library Tools
# ===========================================================================


@mcp.tool()
def symbol_lib_open(path: str) -> Dict[str, Any]:
    """Open a symbol library and list its symbols.

    Args:
        path: Path to a ``.kicad_sym`` file.

    Returns:
        Summary with ``symbol_count`` and a list of symbol names.
    """
    try:
        lib = SymbolLibrary.load(path)
    except FileNotFoundError:
        return _err(f"File not found: {path}")
    except Exception as exc:  # noqa: BLE001
        return _err(str(exc))

    names = [s.name for s in lib.symbols]
    return _ok({"path": str(path), "symbol_count": len(names), "symbols": names})


@mcp.tool()
def symbol_lib_get_symbol(path: str, name: str) -> Dict[str, Any]:
    """Get the full definition of a symbol in a library.

    Args:
        path: Path to a ``.kicad_sym`` file.
        name: Symbol name.

    Returns:
        Dict with ``name``, ``extends``, ``properties``, and ``pin_count``.
    """
    try:
        lib = SymbolLibrary.load(path)
    except Exception as exc:  # noqa: BLE001
        return _err(str(exc))

    sym = lib.find_by_name(name)
    if sym is None:
        return _err(f"Symbol '{name}' not found in library")

    props = {p.key: p.value for p in sym.properties}
    pins = sym.pins()
    pin_list = [
        {
            "number": p.number,
            "name": p.name,
            "type": p.electrical_type,
            "position": _pos_dict(p.position),
        }
        for p in pins
    ]
    return _ok(
        {
            "name": sym.name,
            "extends": sym.extends,
            "properties": props,
            "pin_count": len(pin_list),
            "pins": pin_list,
        }
    )


@mcp.tool()
def symbol_lib_create_symbol(
    path: str,
    name: str,
    properties: Optional[Dict[str, str]] = None,
    pins: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Create a new symbol in a library.

    Args:
        path:       Path to a ``.kicad_sym`` file (modified in-place).
        name:       Unique symbol name.
        properties: Optional dict of property key→value pairs.
        pins:       Optional list of pin dicts, each with ``number``,
                    ``name``, ``type``, ``x``, ``y``, and optionally
                    ``length`` and ``angle``.

    Returns:
        ``{"created": true, "name": "<name>"}`` on success.
    """
    try:
        lib = SymbolLibrary.load(path)
    except FileNotFoundError:
        # Create a new empty library if the file does not exist
        lib = SymbolLibrary()
    except Exception as exc:  # noqa: BLE001
        return _err(str(exc))

    if lib.find_by_name(name) is not None:
        return _err(f"Symbol '{name}' already exists in the library")

    sym = SymbolDef(name=name)

    if properties:
        for k, v in properties.items():
            sym.properties.append(Property(k, v, Position(0.0, 0.0)))

    if pins:
        for p_dict in pins:
            pin = Pin(
                electrical_type=p_dict.get("type", "input"),
                graphic_style=p_dict.get("style", "line"),
                position=Position(
                    float(p_dict.get("x", 0.0)),
                    float(p_dict.get("y", 0.0)),
                    float(p_dict.get("angle", 0.0)),
                ),
                length=float(p_dict.get("length", 2.54)),
                name=p_dict.get("name", "~"),
                number=str(p_dict.get("number", "1")),
            )
            if not sym.units:
                sym.units.append(SymbolUnit())
            sym.units[0].pins.append(pin)

    lib.add_symbol(sym)
    try:
        _safe_save(lib, path)
    except Exception as exc:  # noqa: BLE001
        return _err(f"Failed to save library: {exc}")

    return _ok({"created": True, "name": name})


@mcp.tool()
def symbol_lib_modify_symbol(
    path: str,
    name: str,
    properties: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """Modify an existing symbol's properties.

    Args:
        path:       Path to a ``.kicad_sym`` file (modified in-place).
        name:       Name of the symbol to modify.
        properties: Property key→value pairs to update.

    Returns:
        ``{"modified": true}`` on success.
    """
    try:
        lib = SymbolLibrary.load(path)
    except Exception as exc:  # noqa: BLE001
        return _err(str(exc))

    if properties is None:
        properties = {}

    modified = lib.modify_symbol(name, **properties)
    if not modified:
        return _err(f"Symbol '{name}' not found")

    try:
        _safe_save(lib, path)
    except Exception as exc:  # noqa: BLE001
        return _err(f"Failed to save library: {exc}")

    return _ok({"modified": True})


@mcp.tool()
def symbol_lib_delete_symbol(path: str, name: str) -> Dict[str, Any]:
    """Remove a symbol from a library.

    Args:
        path: Path to a ``.kicad_sym`` file (modified in-place).
        name: Name of the symbol to remove.

    Returns:
        ``{"deleted": true}`` on success.
    """
    try:
        lib = SymbolLibrary.load(path)
    except Exception as exc:  # noqa: BLE001
        return _err(str(exc))

    removed = lib.remove_symbol(name)
    if not removed:
        return _err(f"Symbol '{name}' not found")

    try:
        _safe_save(lib, path)
    except Exception as exc:  # noqa: BLE001
        return _err(f"Failed to save library: {exc}")

    return _ok({"deleted": True})


@mcp.tool()
def symbol_lib_add_pin(
    path: str,
    symbol_name: str,
    pin_number: str,
    pin_name: str,
    electrical_type: str = "input",
    x: float = 0.0,
    y: float = 0.0,
    angle: float = 0.0,
    length: float = 2.54,
) -> Dict[str, Any]:
    """Add a pin to an existing symbol.

    Args:
        path:             Path to a ``.kicad_sym`` file (modified in-place).
        symbol_name:      Target symbol name.
        pin_number:       Pin number string (e.g. ``"3"``).
        pin_name:         Pin name (e.g. ``"CLK"``).
        electrical_type:  KiCad pin type (``"input"``, ``"output"``, ``"bidirectional"``,
                          ``"power_in"``, ``"power_out"``, etc.).
        x, y:             Pin position in mm (relative to symbol origin).
        angle:            Pin angle in degrees.
        length:           Pin length in mm.

    Returns:
        ``{"added": true}`` on success.
    """
    try:
        lib = SymbolLibrary.load(path)
    except Exception as exc:  # noqa: BLE001
        return _err(str(exc))

    sym = lib.find_by_name(symbol_name)
    if sym is None:
        return _err(f"Symbol '{symbol_name}' not found")

    new_pin = Pin(
        electrical_type=electrical_type,
        graphic_style="line",
        position=Position(x, y, angle),
        length=length,
        name=pin_name,
        number=pin_number,
    )
    if not sym.units:
        sym.units.append(SymbolUnit())
    sym.units[0].pins.append(new_pin)
    # Clear raw_tree to force re-serialisation
    sym.raw_tree = None  # type: ignore[assignment]

    try:
        _safe_save(lib, path)
    except Exception as exc:  # noqa: BLE001
        return _err(f"Failed to save library: {exc}")

    return _ok({"added": True})


@mcp.tool()
def symbol_lib_bulk_update(
    path: str,
    properties: Dict[str, str],
) -> Dict[str, Any]:
    """Apply a property update to every symbol in a library.

    Args:
        path:       Path to a ``.kicad_sym`` file (modified in-place).
        properties: Property key→value pairs to set on all symbols.

    Returns:
        ``{"updated_count": N}`` on success.
    """
    try:
        lib = SymbolLibrary.load(path)
    except Exception as exc:  # noqa: BLE001
        return _err(str(exc))

    count = 0
    for sym in lib.symbols:
        lib.modify_symbol(sym.name, **properties)
        count += 1

    try:
        _safe_save(lib, path)
    except Exception as exc:  # noqa: BLE001
        return _err(f"Failed to save library: {exc}")

    return _ok({"updated_count": count})


@mcp.tool()
def symbol_lib_list_libraries(
    project_dir: Optional[str] = None,
) -> Dict[str, Any]:
    """Discover all available symbol libraries.

    Args:
        project_dir: Optional project directory to include project-local
                     libraries in addition to global ones.

    Returns:
        List of dicts with ``nickname`` and ``path`` for each library.
    """
    try:
        disc = LibraryDiscovery(project_dir)
        entries = disc.list_symbol_libraries()
    except Exception as exc:  # noqa: BLE001
        return _err(str(exc))

    libs = [{"nickname": e.nickname, "path": str(e.uri)} for e in entries]
    return _ok(libs)


# ===========================================================================
# 2.4  Footprint Tools
# ===========================================================================


@mcp.tool()
def footprint_open(path: str) -> Dict[str, Any]:
    """Open a footprint file and return a summary.

    Args:
        path: Path to a ``.kicad_mod`` file.

    Returns:
        Summary with ``name``, ``description``, ``tags``, ``layer``,
        and ``pad_count``.
    """
    try:
        fp = Footprint.load(path)
    except FileNotFoundError:
        return _err(f"File not found: {path}")
    except Exception as exc:  # noqa: BLE001
        return _err(str(exc))

    return _ok(
        {
            "path": str(path),
            "name": fp.name,
            "description": fp.description,
            "tags": fp.tags,
            "layer": fp.layer,
            "pad_count": len(fp.pads),
        }
    )


@mcp.tool()
def footprint_get_details(path: str) -> Dict[str, Any]:
    """Get the full definition of a footprint.

    Args:
        path: Path to a ``.kicad_mod`` file.

    Returns:
        Full footprint dict with ``pads`` list and ``graphics`` count.
    """
    try:
        fp = Footprint.load(path)
    except Exception as exc:  # noqa: BLE001
        return _err(str(exc))

    pads = []
    for pad in fp.pads:
        pads.append(
            {
                "number": pad.number,
                "type": pad.type,
                "shape": pad.shape,
                "position": _pos_dict(pad.position),
                "size": {"w": pad.size[0], "h": pad.size[1]},
                "layers": pad.layers,
                "drill": pad.drill,
                "net_name": pad.net,
            }
        )
    return _ok(
        {
            "name": fp.name,
            "description": fp.description,
            "tags": fp.tags,
            "layer": fp.layer,
            "pad_count": len(pads),
            "pads": pads,
            "graphic_count": len(fp.graphics),
        }
    )


@mcp.tool()
def footprint_create(
    path: str,
    name: str,
    description: str = "",
    tags: str = "",
    layer: str = "F.Cu",
    pads: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Create a new footprint file.

    Args:
        path:        Destination ``.kicad_mod`` file path.
        name:        Footprint name.
        description: Human-readable description.
        tags:        Space-separated tag keywords.
        layer:       Primary copper layer (default ``"F.Cu"``).
        pads:        Optional list of pad dicts.  Each dict may contain
                     ``number``, ``type``, ``shape``, ``x``, ``y``,
                     ``width``, ``height``, and ``layers``.

    Returns:
        ``{"created": true, "name": "<name>"}`` on success.
    """
    fp = Footprint(name=name, description=description, tags=tags, layer=layer)

    if pads:
        for p_dict in pads:
            fp.add_pad(
                number=str(p_dict.get("number", "1")),
                pad_type=p_dict.get("type", "smd"),
                shape=p_dict.get("shape", "rect"),
                x=float(p_dict.get("x", 0.0)),
                y=float(p_dict.get("y", 0.0)),
                width=float(p_dict.get("width", 1.0)),
                height=float(p_dict.get("height", 1.0)),
                layers=p_dict.get("layers", [layer]),
            )

    try:
        _safe_save(fp, path)
    except Exception as exc:  # noqa: BLE001
        return _err(f"Failed to save footprint: {exc}")

    return _ok({"created": True, "name": name})


@mcp.tool()
def footprint_modify(
    path: str,
    description: Optional[str] = None,
    tags: Optional[str] = None,
) -> Dict[str, Any]:
    """Modify footprint top-level properties.

    Args:
        path:        Path to a ``.kicad_mod`` file (modified in-place).
        description: New description (optional).
        tags:        New tags string (optional).

    Returns:
        ``{"modified": true}`` on success.
    """
    try:
        fp = Footprint.load(path)
    except Exception as exc:  # noqa: BLE001
        return _err(str(exc))

    if description is not None:
        fp.description = description
    if tags is not None:
        fp.tags = tags

    # Clear raw_tree to force re-serialisation
    fp.raw_tree = None  # type: ignore[assignment]
    try:
        _safe_save(fp, path)
    except Exception as exc:  # noqa: BLE001
        return _err(f"Failed to save footprint: {exc}")

    return _ok({"modified": True})


@mcp.tool()
def footprint_add_pad(
    path: str,
    number: str,
    pad_type: str,
    shape: str,
    x: float,
    y: float,
    width: float,
    height: float,
    layers: Optional[List[str]] = None,
    drill: Optional[float] = None,
) -> Dict[str, Any]:
    """Add a pad to an existing footprint.

    Args:
        path:     Path to a ``.kicad_mod`` file (modified in-place).
        number:   Pad number string (e.g. ``"1"``).
        pad_type: Pad type: ``"smd"``, ``"thru_hole"``, ``"connect"``, or
                  ``"np_thru_hole"``.
        shape:    Pad shape: ``"rect"``, ``"circle"``, ``"oval"``, etc.
        x, y:     Center position in mm.
        width:    Pad width in mm.
        height:   Pad height in mm.
        layers:   Layer list (default ``["F.Cu", "F.Paste", "F.Mask"]``).
        drill:    Drill diameter in mm for through-hole pads (optional).

    Returns:
        ``{"added": true}`` on success.
    """
    try:
        fp = Footprint.load(path)
    except Exception as exc:  # noqa: BLE001
        return _err(str(exc))

    if layers is None:
        if pad_type == "thru_hole":
            layers = ["*.Cu", "*.Mask", "F.Paste"]
        else:
            layers = ["F.Cu", "F.Paste", "F.Mask"]

    fp.add_pad(
        number=number,
        pad_type=pad_type,
        shape=shape,
        x=x,
        y=y,
        width=width,
        height=height,
        layers=layers,
        drill=drill,
    )
    try:
        _safe_save(fp, path)
    except Exception as exc:  # noqa: BLE001
        return _err(f"Failed to save footprint: {exc}")

    return _ok({"added": True})


@mcp.tool()
def footprint_remove_pad(path: str, number: str) -> Dict[str, Any]:
    """Remove a pad from a footprint by pad number.

    Args:
        path:   Path to a ``.kicad_mod`` file (modified in-place).
        number: Pad number to remove.

    Returns:
        ``{"removed": true}`` on success.
    """
    try:
        fp = Footprint.load(path)
    except Exception as exc:  # noqa: BLE001
        return _err(str(exc))

    removed = fp.remove_pad(number)
    if not removed:
        return _err(f"Pad '{number}' not found")

    try:
        _safe_save(fp, path)
    except Exception as exc:  # noqa: BLE001
        return _err(f"Failed to save footprint: {exc}")

    return _ok({"removed": True})


@mcp.tool()
def footprint_renumber_pads(path: str, start: int = 1) -> Dict[str, Any]:
    """Renumber all pads sequentially starting from *start*.

    Args:
        path:  Path to a ``.kicad_mod`` file (modified in-place).
        start: First pad number (default ``1``).

    Returns:
        ``{"renumbered": N}`` where *N* is the total pad count.
    """
    try:
        fp = Footprint.load(path)
    except Exception as exc:  # noqa: BLE001
        return _err(str(exc))

    fp.renumber_pads(start)
    try:
        _safe_save(fp, path)
    except Exception as exc:  # noqa: BLE001
        return _err(f"Failed to save footprint: {exc}")

    return _ok({"renumbered": len(fp.pads)})


@mcp.tool()
def footprint_list_libraries(
    project_dir: Optional[str] = None,
) -> Dict[str, Any]:
    """Discover all available footprint libraries.

    Args:
        project_dir: Optional project directory to include project-local
                     libraries.

    Returns:
        List of dicts with ``nickname`` and ``path`` for each library.
    """
    try:
        disc = LibraryDiscovery(project_dir)
        entries = disc.list_footprint_libraries()
    except Exception as exc:  # noqa: BLE001
        return _err(str(exc))

    libs = [{"nickname": e.nickname, "path": str(e.uri)} for e in entries]
    return _ok(libs)


# ===========================================================================
# 2.5  IPC Bridge Tools
# ===========================================================================


@mcp.tool()
def kicad_list_instances() -> Dict[str, Any]:
    """List all currently running KiCad instances.

    Returns:
        List of instance dicts with ``project_name``, ``project_path``,
        and ``socket_path``.
    """
    try:
        instances = detect_kicad_instances()
    except Exception as exc:  # noqa: BLE001
        return _err(str(exc))

    return _ok(instances)


@mcp.tool()
def kicad_get_project_info(project_path: str) -> Dict[str, Any]:
    """Get information about an open KiCad project.

    Args:
        project_path: Path to the ``.kicad_pro`` project file.

    Returns:
        Dict with ``schematics``, ``pcb``, ``libraries``, and ``is_open``.
    """
    project_path_obj = Path(project_path)
    if not project_path_obj.exists():
        return _err(f"Project path not found: {project_path}")

    project_dir = project_path_obj.parent
    schematics = [str(p) for p in project_dir.rglob("*.kicad_sch")]
    pcb_files = [str(p) for p in project_dir.rglob("*.kicad_pcb")]

    is_open = False
    try:
        open_paths = get_open_project_paths()
        norm = os.path.normpath(os.path.abspath(project_path))
        is_open = any(os.path.normpath(os.path.abspath(p)) == norm for p in open_paths)
    except Exception:  # noqa: BLE001
        pass

    return _ok(
        {
            "project_path": str(project_path),
            "schematics": schematics,
            "pcb": pcb_files,
            "is_open": is_open,
        }
    )


@mcp.tool()
def kicad_save_schematic(file_path: str = "", window_title_hint: str = "") -> Dict[str, Any]:
    """Trigger a save in the KiCad schematic/PCB editor.

    Preferred path: when ``kicad-python`` (kipy) is installed and a running
    KiCad instance has *file_path* open, this calls the native IPC
    ``SaveDocument`` command — no keyboard automation required.

    Fallback path: when kipy is unavailable or the file is not found in any
    IPC socket, sends Ctrl+S to the focused KiCad window via the platform's
    keyboard automation facility (ctypes on Windows, xdotool on Linux,
    osascript on macOS).

    Args:
        file_path:         Path to the ``.kicad_sch`` or ``.kicad_pcb`` file
                           that should be saved.  When provided and kipy is
                           available the IPC path is used; when omitted only
                           keyboard automation is attempted.
        window_title_hint: Optional substring of the KiCad window title used
                           by the keyboard-automation fallback to target a
                           specific instance (currently unused on non-Windows
                           platforms; reserved for future use).

    Returns:
        ``{"triggered": true, "method": "<ipc|xdotool|osascript|ctypes_keybd_event>"}``
        on success, or an error dict.
    """
    # ── IPC path ─────────────────────────────────────────────────────────────
    if file_path:
        try:
            from .kicad_ipc import ipc_save_document
            ipc_result = ipc_save_document(file_path)
            if ipc_result.get("success"):
                return _ok({"triggered": True, "method": "ipc", "socket": ipc_result.get("socket", "")})
            # IPC not available or document not found; fall through to keyboard.
            logger.debug("IPC save failed (%s), falling back to keyboard.", ipc_result.get("error"))
        except Exception as exc:  # noqa: BLE001
            logger.debug("IPC save raised %s, falling back to keyboard.", exc)

    # ── Keyboard-automation fallback ─────────────────────────────────────────
    system = platform.system()
    try:
        if system == "Windows":
            import ctypes

            VK_CONTROL = 0x11
            VK_S = 0x53
            KEYEVENTF_KEYUP = 0x0002

            ctypes.windll.user32.keybd_event(VK_CONTROL, 0, 0, 0)
            ctypes.windll.user32.keybd_event(VK_S, 0, 0, 0)
            ctypes.windll.user32.keybd_event(VK_S, 0, KEYEVENTF_KEYUP, 0)
            ctypes.windll.user32.keybd_event(VK_CONTROL, 0, KEYEVENTF_KEYUP, 0)
            return _ok({"triggered": True, "method": "ctypes_keybd_event"})

        elif system in ("Linux", "Darwin"):
            # Try xdotool on Linux, osascript on macOS
            if system == "Linux":
                if shutil.which("xdotool"):
                    result = subprocess.run(
                        ["xdotool", "key", "--clearmodifiers", "ctrl+s"],
                        check=False,
                    )
                    if result.returncode != 0:
                        return _err(f"xdotool exited with code {result.returncode}")
                    return _ok({"triggered": True, "method": "xdotool"})
                return _err("xdotool not found; install xdotool for keyboard automation")
            else:
                result = subprocess.run(
                    [
                        "osascript",
                        "-e",
                        'tell application "System Events" to keystroke "s" using command down',
                    ],
                    check=False,
                )
                if result.returncode != 0:
                    return _err(f"osascript exited with code {result.returncode}")
                return _ok({"triggered": True, "method": "osascript"})
        else:
            return _err(f"Keyboard automation not supported on platform: {system}")
    except Exception as exc:  # noqa: BLE001
        return _err(f"Failed to send save shortcut: {exc}")


@mcp.tool()
def kicad_reload_schematic(file_path: str = "", window_title_hint: str = "") -> Dict[str, Any]:
    """Reload a KiCad schematic/PCB from disk into the live editor.

    Preferred path: when ``kicad-python`` (kipy) is installed and a running
    KiCad instance has *file_path* open, this calls the native IPC
    ``RevertDocument`` command (discard in-memory state, re-read from disk)
    followed by ``RefreshEditor`` (force a UI redraw).

    Fallback path: when kipy is unavailable or the file is not found in any
    IPC socket, sends Ctrl+Shift+R to the focused KiCad window via the
    platform's keyboard automation facility.

    Args:
        file_path:         Path to the ``.kicad_sch`` or ``.kicad_pcb`` file
                           that should be reloaded.  When provided and kipy
                           is available the IPC path is used; when omitted
                           only keyboard automation is attempted.
        window_title_hint: Optional window title hint used by the keyboard
                           fallback (reserved for future use).

    Returns:
        ``{"triggered": true, "method": "<ipc|xdotool|osascript|ctypes_keybd_event>"}``
        on success, or an error dict.
    """
    # ── IPC path ─────────────────────────────────────────────────────────────
    if file_path:
        try:
            from .kicad_ipc import ipc_revert_document
            ipc_result = ipc_revert_document(file_path)
            if ipc_result.get("success"):
                return _ok({"triggered": True, "method": "ipc", "socket": ipc_result.get("socket", "")})
            logger.debug("IPC reload failed (%s), falling back to keyboard.", ipc_result.get("error"))
        except Exception as exc:  # noqa: BLE001
            logger.debug("IPC reload raised %s, falling back to keyboard.", exc)

    # ── Keyboard-automation fallback ─────────────────────────────────────────
    system = platform.system()
    try:
        if system == "Windows":
            import ctypes

            VK_CONTROL = 0x11
            VK_SHIFT = 0x10
            VK_R = 0x52
            KEYEVENTF_KEYUP = 0x0002

            ctypes.windll.user32.keybd_event(VK_CONTROL, 0, 0, 0)
            ctypes.windll.user32.keybd_event(VK_SHIFT, 0, 0, 0)
            ctypes.windll.user32.keybd_event(VK_R, 0, 0, 0)
            ctypes.windll.user32.keybd_event(VK_R, 0, KEYEVENTF_KEYUP, 0)
            ctypes.windll.user32.keybd_event(VK_SHIFT, 0, KEYEVENTF_KEYUP, 0)
            ctypes.windll.user32.keybd_event(VK_CONTROL, 0, KEYEVENTF_KEYUP, 0)
            return _ok({"triggered": True, "method": "ctypes_keybd_event"})

        elif system in ("Linux", "Darwin"):
            if system == "Linux":
                if shutil.which("xdotool"):
                    result = subprocess.run(
                        ["xdotool", "key", "--clearmodifiers", "ctrl+shift+r"],
                        check=False,
                    )
                    if result.returncode != 0:
                        return _err(f"xdotool exited with code {result.returncode}")
                    return _ok({"triggered": True, "method": "xdotool"})
                return _err("xdotool not found; install xdotool for keyboard automation")
            else:
                result = subprocess.run(
                    [
                        "osascript",
                        "-e",
                        'tell application "System Events" to keystroke "r" '
                        "using {command down, shift down}",
                    ],
                    check=False,
                )
                if result.returncode != 0:
                    return _err(f"osascript exited with code {result.returncode}")
                return _ok({"triggered": True, "method": "osascript"})
        else:
            return _err(f"Keyboard automation not supported on platform: {system}")
    except Exception as exc:  # noqa: BLE001
        return _err(f"Failed to send reload shortcut: {exc}")


@mcp.tool()
def kicad_get_board_info(path: str) -> Dict[str, Any]:
    """Read board information from a PCB file (read-only).

    Args:
        path: Path to a ``.kicad_pcb`` file.

    Returns:
        Dict with ``net_count``, ``footprint_count``, ``track_count``,
        ``via_count``, and ``layer_stackup``.
    """
    try:
        board = PCBBoard.load(path)
    except FileNotFoundError:
        return _err(f"File not found: {path}")
    except Exception as exc:  # noqa: BLE001
        return _err(str(exc))

    return _ok(
        {
            "path": str(path),
            "net_count": len(board.nets),
            "footprint_count": len(board.footprints),
            "track_count": len(board.tracks),
            "via_count": len(board.vias),
            "layer_stackup": board.get_layer_stackup(),
        }
    )


# ===========================================================================
# 2.5b  Phase 5 — IPC Save/Reload Workflow Tools
# ===========================================================================


@mcp.tool()
def kicad_check_file_status(path: str) -> Dict[str, Any]:
    """Check whether a KiCad file is open in a live KiCad instance (Phase 5.1).

    Returns information about the file's current state on disk and whether it
    is currently open in a running KiCad editor.  Use this before any file edit
    to decide whether a save/reload cycle is needed.

    Args:
        path: Path to a ``.kicad_sch`` or ``.kicad_pcb`` file.

    Returns:
        Dict with:

        * ``path`` – resolved absolute path.
        * ``exists`` – ``true`` if the file exists on disk.
        * ``mtime`` – last-modified POSIX timestamp, or ``null``.
        * ``open_in_kicad`` – ``true`` if a running KiCad instance has the
          file open.
        * ``bak_exists`` – ``true`` if a ``.bak`` backup is present.
    """
    try:
        from .ipc_workflow import check_file_status
    except ImportError as exc:
        return _err(f"ipc_workflow module unavailable: {exc}")

    try:
        return _ok(check_file_status(path))
    except Exception as exc:  # noqa: BLE001
        return _err(str(exc))


@mcp.tool()
async def kicad_edit_file_pipeline(
    file_path: str,
    tool_name: str,
    tool_args: str = "{}",
    window_title_hint: str = "",
    save_before_edit: bool = True,
    reload_after_edit: bool = True,
) -> Dict[str, Any]:
    """Orchestrate the full IPC save → direct-file-edit → IPC reload pipeline (Phase 5.3).

    **When to use this tool:**  call it whenever you want to edit a KiCad file
    (``.kicad_sch`` or ``.kicad_pcb``) that may be open in a live KiCad
    instance.  It handles the full workflow automatically:

    1. **IPC detection** — checks via KiCad's IPC socket whether the file is
       currently open.  If it is not open, steps 2 and 6 are skipped.
    2. **IPC save** (if open) — calls ``SaveDocument`` via the kipy IPC API so
       any unsaved KiCad changes are flushed to disk.  Falls back to sending
       Ctrl+S via keyboard automation when kipy is unavailable.
    3. **Advisory file lock** — acquires an OS-level lock to prevent concurrent
       modifications from other processes.
    4. **Direct file edit** — invokes *tool_name* with *tool_args*; KiAssist's
       custom parsers write directly to the ``.kicad_sch`` / ``.kicad_pcb`` file
       on disk (this never modifies KiCad's in-memory state directly).
    5. **Rollback** — if the edit fails, the ``.bak`` backup created by
       ``_safe_save`` is restored automatically.
    6. **IPC reload** (if open and edit succeeded) — calls ``RevertDocument`` +
       ``RefreshEditor`` via kipy so KiCad re-reads the updated file from disk
       and refreshes its UI.  Falls back to Ctrl+Shift+R keyboard automation
       when kipy is unavailable.

    Args:
        file_path:         Path to the ``.kicad_sch`` or ``.kicad_pcb`` file
                           being edited.
        tool_name:         Name of the MCP tool that performs the actual edit
                           (e.g. ``"schematic_add_symbol"``).
        tool_args:         JSON-encoded dict of arguments for *tool_name*.
        window_title_hint: Optional KiCad window title fragment used by the
                           keyboard-automation fallback to target a specific
                           window.
        save_before_edit:  Trigger save before the edit (default ``true``).
        reload_after_edit: Trigger reload after the edit (default ``true``).

    Returns:
        The return value of *tool_name* annotated with a ``pipeline`` dict
        containing:

        * ``file_was_open_in_kicad`` — ``true`` if the file was detected open.
        * ``save_triggered`` — ``true`` if save succeeded (via IPC or keyboard).
        * ``reload_triggered`` — ``true`` if reload succeeded (via IPC or keyboard).
    """
    try:
        from .ipc_workflow import SchematicEditPipeline
    except ImportError as exc:
        return _err(f"ipc_workflow module unavailable: {exc}")

    try:
        args: Dict[str, Any] = json.loads(tool_args) if isinstance(tool_args, str) else tool_args
    except json.JSONDecodeError as exc:
        return _err(f"Invalid JSON in tool_args: {exc}")

    try:
        pipeline = SchematicEditPipeline(
            file_path,
            window_title_hint=window_title_hint,
            save_before_edit=save_before_edit,
            reload_after_edit=reload_after_edit,
        )
        result = await pipeline.run(tool_name, args)
        return result  # type: ignore[return-value]
    except Exception as exc:  # noqa: BLE001
        return _err(f"Pipeline execution failed: {exc}")


# ===========================================================================
# 2.6  PCB Editor Tools
# ===========================================================================


@mcp.tool()
def pcb_open(path: str) -> Dict[str, Any]:
    """Open a PCB file and return a full board summary.

    Args:
        path: Path to a ``.kicad_pcb`` file.

    Returns:
        Dict with ``version``, ``net_count``, ``footprint_count``,
        ``track_count``, ``via_count``, ``layer_stackup``, ``nets``,
        and ``footprints`` (reference + value + layer + position for each).
    """
    try:
        board = PCBBoard.load(path)
    except FileNotFoundError:
        return _err(f"File not found: {path}")
    except Exception as exc:  # noqa: BLE001
        return _err(str(exc))

    return _ok(
        {
            "path": str(path),
            "version": board.version,
            "net_count": len(board.nets),
            "footprint_count": len(board.footprints),
            "track_count": len(board.tracks),
            "via_count": len(board.vias),
            "layer_stackup": board.get_layer_stackup(),
            "nets": [{"number": n.number, "name": n.name} for n in board.nets],
            "footprints": [
                {
                    "reference": fp.reference,
                    "value": fp.value,
                    "name": fp.name,
                    "layer": fp.layer,
                    "position": _pos_dict(fp.position),
                }
                for fp in board.footprints
            ],
        }
    )


@mcp.tool()
def pcb_new(path: str) -> Dict[str, Any]:
    """Create a new, empty PCB file.

    Args:
        path: Destination ``.kicad_pcb`` file path (written immediately).

    Returns:
        ``{"created": true, "path": "<path>"}`` on success.
    """
    board = PCBBoard.new()
    try:
        _safe_save(board, path)
    except Exception as exc:  # noqa: BLE001
        return _err(f"Failed to write PCB file: {exc}")
    return _ok({"created": True, "path": str(path)})


@mcp.tool()
def pcb_get_layer_stackup(path: str) -> Dict[str, Any]:
    """Get the ordered list of copper layers in a PCB file.

    Args:
        path: Path to a ``.kicad_pcb`` file.

    Returns:
        ``{"layers": [...]}`` — ordered list of copper layer name strings.
    """
    try:
        board = PCBBoard.load(path)
    except FileNotFoundError:
        return _err(f"File not found: {path}")
    except Exception as exc:  # noqa: BLE001
        return _err(str(exc))
    return _ok({"layers": board.get_layer_stackup()})


@mcp.tool()
def pcb_list_nets(path: str) -> Dict[str, Any]:
    """List all nets defined in a PCB file.

    Args:
        path: Path to a ``.kicad_pcb`` file.

    Returns:
        List of dicts with ``number`` and ``name`` for each net.
    """
    try:
        board = PCBBoard.load(path)
    except FileNotFoundError:
        return _err(f"File not found: {path}")
    except Exception as exc:  # noqa: BLE001
        return _err(str(exc))
    return _ok([{"number": n.number, "name": n.name} for n in board.nets])


@mcp.tool()
def pcb_add_net(path: str, name: str) -> Dict[str, Any]:
    """Add a new net to a PCB file.

    Args:
        path: Path to a ``.kicad_pcb`` file (modified in-place).
        name: Net name string (e.g. ``"PWR_3V3"``).

    Returns:
        ``{"added": true, "number": N, "name": "<name>"}`` on success.
    """
    try:
        board = PCBBoard.load(path)
    except FileNotFoundError:
        return _err(f"File not found: {path}")
    except Exception as exc:  # noqa: BLE001
        return _err(str(exc))

    if board.get_net(name) is not None:
        return _err(f"Net '{name}' already exists")

    try:
        net = board.add_net(name)
    except ValueError as exc:
        return _err(str(exc))

    try:
        _safe_save(board, path)
    except Exception as exc:  # noqa: BLE001
        return _err(f"Failed to save PCB file: {exc}")

    return _ok({"added": True, "number": net.number, "name": net.name})


@mcp.tool()
def pcb_list_footprints(path: str) -> Dict[str, Any]:
    """List all footprint instances placed on the board.

    Args:
        path: Path to a ``.kicad_pcb`` file.

    Returns:
        List of dicts with ``reference``, ``value``, ``name``, ``layer``,
        and ``position`` for each footprint.
    """
    try:
        board = PCBBoard.load(path)
    except FileNotFoundError:
        return _err(f"File not found: {path}")
    except Exception as exc:  # noqa: BLE001
        return _err(str(exc))
    return _ok(
        [
            {
                "reference": fp.reference,
                "value": fp.value,
                "name": fp.name,
                "layer": fp.layer,
                "position": _pos_dict(fp.position),
            }
            for fp in board.footprints
        ]
    )


@mcp.tool()
def pcb_get_footprint(path: str, reference: str) -> Dict[str, Any]:
    """Get detailed information for a specific footprint on the board.

    Args:
        path:      Path to a ``.kicad_pcb`` file.
        reference: Reference designator (e.g. ``"R1"``).

    Returns:
        Dict with ``reference``, ``value``, ``name``, ``layer``,
        ``position``, ``pad_count``, and ``pads`` (number, type, shape,
        position, size, net for each pad).
    """
    try:
        board = PCBBoard.load(path)
    except FileNotFoundError:
        return _err(f"File not found: {path}")
    except Exception as exc:  # noqa: BLE001
        return _err(str(exc))

    fp = board.get_footprint(reference)
    if fp is None:
        return _err(f"Footprint '{reference}' not found")

    pads: List[Dict[str, Any]] = []
    if fp.raw_tree:
        for pad_node in _find_all(fp.raw_tree, "pad"):
            num = str(pad_node[1]) if len(pad_node) > 1 else ""
            pad_type = str(pad_node[2]) if len(pad_node) > 2 else ""
            pad_shape = str(pad_node[3]) if len(pad_node) > 3 else ""
            at_node = _find(pad_node, "at")
            size_node = _find(pad_node, "size")
            net_node = _find(pad_node, "net")
            pos = _parse_position(at_node) if at_node else None
            width = float(size_node[1]) if size_node and len(size_node) > 1 else 0.0
            height = float(size_node[2]) if size_node and len(size_node) > 2 else 0.0
            net_name = str(net_node[2]) if net_node and len(net_node) > 2 else ""
            pads.append(
                {
                    "number": num,
                    "type": pad_type,
                    "shape": pad_shape,
                    "position": _pos_dict(pos) if pos else None,
                    "size": {"width": width, "height": height},
                    "net": net_name,
                }
            )

    return _ok(
        {
            "reference": fp.reference,
            "value": fp.value,
            "name": fp.name,
            "layer": fp.layer,
            "position": _pos_dict(fp.position),
            "pad_count": len(pads),
            "pads": pads,
        }
    )


@mcp.tool()
def pcb_add_footprint(
    path: str,
    name: str,
    reference: str,
    value: str,
    layer: str = "F.Cu",
    x: float = 0.0,
    y: float = 0.0,
    angle: float = 0.0,
) -> Dict[str, Any]:
    """Place a footprint on the board.

    Args:
        path:      Path to a ``.kicad_pcb`` file (modified in-place).
        name:      Footprint library identifier (e.g.
                   ``"Resistor_SMD:R_0402_1005Metric"``).
        reference: Reference designator (e.g. ``"R2"``).
        value:     Component value string.
        layer:     Primary copper layer (``"F.Cu"`` or ``"B.Cu"``).
        x, y:      Position in mm.
        angle:     Rotation angle in degrees.

    Returns:
        ``{"added": true, "reference": "<ref>"}`` on success.
    """
    try:
        board = PCBBoard.load(path)
    except FileNotFoundError:
        return _err(f"File not found: {path}")
    except Exception as exc:  # noqa: BLE001
        return _err(str(exc))

    board.add_footprint(name, reference, value, layer, x, y, angle)
    try:
        _safe_save(board, path)
    except Exception as exc:  # noqa: BLE001
        return _err(f"Failed to save PCB file: {exc}")

    return _ok({"added": True, "reference": reference})


@mcp.tool()
def pcb_remove_footprint(path: str, reference: str) -> Dict[str, Any]:
    """Remove a footprint from the board by reference designator.

    Args:
        path:      Path to a ``.kicad_pcb`` file (modified in-place).
        reference: Reference designator to remove (e.g. ``"R1"``).

    Returns:
        ``{"removed": true}`` on success, or an error if not found.
    """
    try:
        board = PCBBoard.load(path)
    except FileNotFoundError:
        return _err(f"File not found: {path}")
    except Exception as exc:  # noqa: BLE001
        return _err(str(exc))

    if not board.remove_footprint(reference):
        return _err(f"Footprint '{reference}' not found")

    try:
        _safe_save(board, path)
    except Exception as exc:  # noqa: BLE001
        return _err(f"Failed to save PCB file: {exc}")

    return _ok({"removed": True})


@mcp.tool()
def pcb_move_footprint(
    path: str,
    reference: str,
    x: float,
    y: float,
    angle: float = 0.0,
) -> Dict[str, Any]:
    """Move and/or rotate a footprint to a new position.

    Pads, graphics, and all other sub-elements of the footprint are
    preserved because the position is updated in the raw S-expression
    tree rather than by re-serialising from dataclass fields.

    Args:
        path:      Path to a ``.kicad_pcb`` file (modified in-place).
        reference: Reference designator (e.g. ``"R1"``).
        x, y:      New position in mm.
        angle:     New rotation angle in degrees (default 0.0).

    Returns:
        ``{"moved": true, "reference": "<ref>", "x": x, "y": y, "angle": angle}``
        on success.
    """
    try:
        board = PCBBoard.load(path)
    except FileNotFoundError:
        return _err(f"File not found: {path}")
    except Exception as exc:  # noqa: BLE001
        return _err(str(exc))

    fp = board.get_footprint(reference)
    if fp is None:
        return _err(f"Footprint '{reference}' not found")

    # Update position in the raw tree in-place so all pads and sub-elements
    # are preserved (PCBFootprint.to_tree() only reconstructs a skeleton).
    if fp.raw_tree is not None:
        at_node = _find(fp.raw_tree, "at")
        if at_node is not None:
            at_node[1] = x
            at_node[2] = y
            if len(at_node) > 3:
                at_node[3] = angle
            elif angle != 0.0:
                at_node.append(angle)
    fp.position = Position(x, y, angle)

    try:
        _safe_save(board, path)
    except Exception as exc:  # noqa: BLE001
        return _err(f"Failed to save PCB file: {exc}")

    return _ok({"moved": True, "reference": reference, "x": x, "y": y, "angle": angle})


@mcp.tool()
def pcb_list_tracks(path: str) -> Dict[str, Any]:
    """List all copper track segments in a PCB file.

    Args:
        path: Path to a ``.kicad_pcb`` file.

    Returns:
        List of dicts with ``start``, ``end``, ``layer``, ``width``,
        and ``net`` (net number) for each track segment.
    """
    try:
        board = PCBBoard.load(path)
    except FileNotFoundError:
        return _err(f"File not found: {path}")
    except Exception as exc:  # noqa: BLE001
        return _err(str(exc))

    # Build a net-number-to-name lookup for richer output
    net_names: Dict[int, str] = {n.number: n.name for n in board.nets}

    return _ok(
        [
            {
                "start": {"x": t.start.x, "y": t.start.y},
                "end": {"x": t.end.x, "y": t.end.y},
                "layer": t.layer,
                "width": t.width,
                "net": t.net,
                "net_name": net_names.get(t.net, ""),
            }
            for t in board.tracks
        ]
    )


@mcp.tool()
def pcb_add_track(
    path: str,
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    layer: str = "F.Cu",
    width: float = 0.25,
    net: str = "",
) -> Dict[str, Any]:
    """Add a copper track segment to a PCB file.

    Args:
        path:   Path to a ``.kicad_pcb`` file (modified in-place).
        x1, y1: Start coordinate in mm.
        x2, y2: End coordinate in mm.
        layer:  Copper layer name (e.g. ``"F.Cu"``).
        width:  Track width in mm.
        net:    Net name string, numeric string (``"1"``), or ``""`` for
                unconnected.  Digit-only strings are converted to net numbers
                so that passing ``"1"`` resolves to net number 1, not a
                search for a net *named* ``"1"``.

    Returns:
        ``{"added": true, "net": <net_number>}`` on success.
    """
    try:
        board = PCBBoard.load(path)
    except FileNotFoundError:
        return _err(f"File not found: {path}")
    except Exception as exc:  # noqa: BLE001
        return _err(str(exc))

    # Resolve net: digit-only strings are treated as net numbers so that
    # passing "1" reaches net number 1, not a net named "1".
    net_arg: int | str = int(net) if net and net.isdigit() else (net or 0)
    track = board.add_track(x1, y1, x2, y2, layer, width, net_arg)
    try:
        _safe_save(board, path)
    except Exception as exc:  # noqa: BLE001
        return _err(f"Failed to save PCB file: {exc}")

    return _ok({"added": True, "net": track.net})


@mcp.tool()
def pcb_list_vias(path: str) -> Dict[str, Any]:
    """List all vias in a PCB file.

    Args:
        path: Path to a ``.kicad_pcb`` file.

    Returns:
        List of dicts with ``position``, ``drill``, ``size``,
        ``layer_from``, ``layer_to``, ``net``, and ``net_name``
        for each via.
    """
    try:
        board = PCBBoard.load(path)
    except FileNotFoundError:
        return _err(f"File not found: {path}")
    except Exception as exc:  # noqa: BLE001
        return _err(str(exc))

    net_names: Dict[int, str] = {n.number: n.name for n in board.nets}

    return _ok(
        [
            {
                "position": {"x": v.position.x, "y": v.position.y},
                "drill": v.drill,
                "size": v.size,
                "layer_from": v.layer_from,
                "layer_to": v.layer_to,
                "net": v.net,
                "net_name": net_names.get(v.net, ""),
            }
            for v in board.vias
        ]
    )


@mcp.tool()
def pcb_add_via(
    path: str,
    x: float,
    y: float,
    net: str = "",
    drill: float = 0.8,
    size: float = 1.6,
    layer_from: str = "F.Cu",
    layer_to: str = "B.Cu",
) -> Dict[str, Any]:
    """Add a copper via to a PCB file.

    Args:
        path:       Path to a ``.kicad_pcb`` file (modified in-place).
        x, y:       Via centre position in mm.
        net:        Net name string, numeric string (``"1"``), or ``""`` for
                    unconnected.  Digit-only strings are converted to net
                    numbers so that passing ``"1"`` resolves to net number 1,
                    not a search for a net *named* ``"1"``.
        drill:      Drill diameter in mm (default 0.8).
        size:       Pad diameter in mm (default 1.6).
        layer_from: Top copper layer (default ``"F.Cu"``).
        layer_to:   Bottom copper layer (default ``"B.Cu"``).

    Returns:
        ``{"added": true, "net": <net_number>}`` on success.
    """
    try:
        board = PCBBoard.load(path)
    except FileNotFoundError:
        return _err(f"File not found: {path}")
    except Exception as exc:  # noqa: BLE001
        return _err(str(exc))

    # Resolve net: digit-only strings are treated as net numbers so that
    # passing "1" reaches net number 1, not a net named "1".
    net_arg: int | str = int(net) if net and net.isdigit() else (net or 0)
    via = board.add_via(x, y, net_arg, drill, size, layer_from, layer_to)
    try:
        _safe_save(board, path)
    except Exception as exc:  # noqa: BLE001
        return _err(f"Failed to save PCB file: {exc}")

    return _ok({"added": True, "net": via.net})


# ===========================================================================
# 2.7  Project Context Tools
# ===========================================================================


@mcp.tool()
def project_get_context(project_path: str) -> Dict[str, Any]:
    """Get a project summary: schematics, libraries, BOM, and design rules.

    Args:
        project_path: Path to the ``.kicad_pro`` project file or project
                      directory.

    Returns:
        Dict with lists of schematic symbols, symbol/footprint library paths,
        design-rule files, and whether a KIASSIST.md memory file exists.
    """
    path_obj = Path(project_path)
    if path_obj.is_file():
        project_dir = path_obj.parent
    elif path_obj.is_dir():
        project_dir = path_obj
    else:
        return _err(f"Path not found: {project_path}")

    schematics = list(project_dir.rglob("*.kicad_sch"))

    bom: List[Dict[str, Any]] = []
    for sch_path in schematics:
        try:
            sch = Schematic.load(sch_path)
            for sym in sch.symbols:
                bom.append(
                    {
                        "reference": sym.reference,
                        "value": sym.value,
                        "footprint": sym.footprint,
                        "schematic": str(sch_path),
                    }
                )
        except Exception:  # noqa: BLE001
            pass

    # Collect design-rule files (.kicad_dru) in the project directory
    design_rule_files = [str(p) for p in project_dir.rglob("*.kicad_dru")]
    design_rules: List[Dict[str, str]] = []
    for dru_path in design_rule_files:
        try:
            content = Path(dru_path).read_text(encoding="utf-8")
            design_rules.append({"path": dru_path, "content": content})
        except Exception:  # noqa: BLE001
            design_rules.append({"path": dru_path, "content": ""})

    # Discover project-local symbol and footprint libraries
    sym_libs: List[Dict[str, str]] = []
    fp_libs: List[Dict[str, str]] = []
    try:
        disc = LibraryDiscovery(project_dir)
        sym_libs = [
            {"nickname": e.nickname, "path": str(e.uri)}
            for e in disc.list_symbol_libraries()
        ]
        fp_libs = [
            {"nickname": e.nickname, "path": str(e.uri)}
            for e in disc.list_footprint_libraries()
        ]
    except Exception:  # noqa: BLE001
        pass

    memory_path = project_dir / "KIASSIST.md"

    return _ok(
        {
            "project_dir": str(project_dir),
            "schematic_count": len(schematics),
            "bom_entries": bom,
            "symbol_libraries": sym_libs,
            "footprint_libraries": fp_libs,
            "design_rule_files": design_rule_files,
            "design_rules": design_rules,
            "has_memory_file": memory_path.exists(),
            "memory_file_path": str(memory_path),
        }
    )


@mcp.tool()
def project_read_memory(project_path: str) -> Dict[str, Any]:
    """Read the KIASSIST.md project memory file.

    Args:
        project_path: Path to the ``.kicad_pro`` project file or project
                      directory.

    Returns:
        Dict with ``content`` (the Markdown text) or an error if the file
        does not exist.
    """
    mem = ProjectMemory(project_path)
    try:
        content = mem.read()
    except Exception as exc:  # noqa: BLE001
        return _err(f"Failed to read KIASSIST.md: {exc}")
    if content is None:
        return _err("KIASSIST.md not found.  Use project_write_memory to create it.")
    return _ok({"path": str(mem.path), "content": content})


@mcp.tool()
def project_write_memory(project_path: str, content: str) -> Dict[str, Any]:
    """Write or update the KIASSIST.md project memory file.

    Args:
        project_path: Path to the ``.kicad_pro`` project file or project
                      directory.
        content:      Full Markdown text to write.

    Returns:
        ``{"written": true, "path": "<path>"}`` on success.
    """
    mem = ProjectMemory(project_path)
    if not mem.project_dir.exists():
        return _err(f"Project directory not found: {mem.project_dir}")
    try:
        mem.write(content)
    except Exception as exc:  # noqa: BLE001
        return _err(f"Failed to write KIASSIST.md: {exc}")
    return _ok({"written": True, "path": str(mem.path)})


# ===========================================================================
# In-process call entry point
# ===========================================================================


async def in_process_call(tool_name: str, args: Dict[str, Any]) -> Any:
    """Call an MCP tool in-process without going through the stdio transport.

    This is useful for integrating MCP tools directly into the KiAssistAPI
    chat loop without spawning a separate process.

    Args:
        tool_name: Name of the registered MCP tool (e.g. ``"schematic_open"``).
        args:      Tool arguments dict.

    Returns:
        The raw return value of the tool function (typically a dict).

    Raises:
        KeyError: If *tool_name* is not registered.
    """
    # Provide a clear error message when the requested tool does not exist.
    # Use the public mcp.list_tools() API instead of the private _tool_manager.
    registered = {t.name for t in await mcp.list_tools()}
    if tool_name not in registered:
        raise KeyError(
            f"MCP tool '{tool_name}' is not registered.  "
            f"Available tools: {sorted(registered)}"
        )
    raw = await mcp.call_tool(tool_name, args)
    # FastMCP 1.x returns a (list[ContentBlock], structured_result) tuple.
    # The structured_result dict contains a ``result`` key with the actual
    # return value.  Fall back to parsing the first text content block when
    # the structured result is unavailable.
    if isinstance(raw, tuple) and len(raw) == 2:
        content_blocks, structured = raw
        if isinstance(structured, dict) and "result" in structured:
            return structured["result"]
        # Fall back: parse first text content block as JSON
        if content_blocks and hasattr(content_blocks[0], "text"):
            try:
                return json.loads(content_blocks[0].text)
            except (json.JSONDecodeError, AttributeError):
                return content_blocks[0].text
    # Sequence of ContentBlock (older FastMCP versions)
    if raw and hasattr(raw[0], "text"):
        try:
            return json.loads(raw[0].text)
        except (json.JSONDecodeError, AttributeError):
            return raw[0].text
    return raw


# ===========================================================================
# Entry point
# ===========================================================================


def main() -> None:
    """Run the KiAssist MCP server on stdio."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
