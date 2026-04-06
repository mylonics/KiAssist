"""Rich project context extraction for KiCad projects.

Provides two levels of context:

1. **Raw context** (:func:`get_raw_context`) — Structured text listing all
   schematic/board files, which sheets are referenced by the main schematic,
   a component BOM (reference, description, value, footprint), and a netlist
   showing how pins are connected.

2. **Synthesized context** (:func:`get_llm_synthesized_context`) — The raw
   context is sent to the LLM which produces a compact, digestible summary
   that fits within the model's context window for ongoing use.

The Gemma 4 model family supports a 32 768-token context window.  We allocate
up to ~55 000 chars (~27 500 tokens at ~2 chars/token) for the raw context to
leave headroom for conversation history and the system prompt.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

# Maximum characters for raw context text.  Gemma 4 supports 32 768 tokens;
# at a conservative 2 chars/token ratio this gives ~16k tokens for context,
# leaving ~16k tokens for conversation history + system prompt + response.
_MAX_RAW_CONTEXT_CHARS = 55_000


def get_raw_context(project_path: str | Path) -> str:
    """Build a comprehensive raw context string for the KiCad project.

    Includes:
    - List of schematic and board files
    - Hierarchical sheet references (which sheets the main schematic uses)
    - Unreferenced sheets (old/orphan sheets that may provide info)
    - Component BOM (reference, value, description, footprint)
    - Netlist showing how component pins are connected

    Args:
        project_path: Path to the ``.kicad_pro`` file or project directory.

    Returns:
        Multi-line text string with the full project context.
    """
    p = Path(project_path)
    project_dir = p.parent if p.is_file() else p

    lines: List[str] = ["# KiCad Project Context"]
    lines.append(f"\n**Project directory:** `{project_dir}`")

    # -----------------------------------------------------------------
    # 1. Discover all schematic and board files
    # -----------------------------------------------------------------
    all_schematics = sorted(project_dir.rglob("*.kicad_sch"))
    all_boards = sorted(project_dir.rglob("*.kicad_pcb"))
    pro_files = sorted(project_dir.rglob("*.kicad_pro"))

    if pro_files:
        lines.append(f"\n## Project Files")
        for pf in pro_files:
            lines.append(f"- `{pf.relative_to(project_dir)}`")

    if all_boards:
        lines.append(f"\n## Board Files ({len(all_boards)})")
        for bf in all_boards:
            lines.append(f"- `{bf.relative_to(project_dir)}`")

    if all_schematics:
        lines.append(f"\n## Schematic Files ({len(all_schematics)})")
        for sf in all_schematics:
            lines.append(f"- `{sf.relative_to(project_dir)}`")

    # -----------------------------------------------------------------
    # 2. Parse main schematic and find referenced sub-sheets
    # -----------------------------------------------------------------
    main_schematic = _find_main_schematic(project_dir, all_schematics)
    referenced_sheets: Set[Path] = set()
    unreferenced_sheets: Set[Path] = set()

    all_symbols: List[Dict[str, str]] = []
    all_nets: Dict[str, List[str]] = {}

    if main_schematic:
        lines.append(f"\n## Schematic Hierarchy")
        lines.append(f"\n**Main schematic:** `{main_schematic.relative_to(project_dir)}`")

        try:
            from ..kicad_parser.schematic import Schematic

            referenced_sheets.add(main_schematic.resolve())
            _collect_hierarchy(
                project_dir, main_schematic, referenced_sheets,
                all_symbols, all_nets, lines, depth=0,
            )
        except Exception as exc:
            logger.debug("Failed to parse main schematic hierarchy: %s", exc)
            lines.append(f"\n*(Error parsing hierarchy: {exc})*")

        # Identify unreferenced sheets
        for sf in all_schematics:
            if sf.resolve() not in referenced_sheets:
                unreferenced_sheets.add(sf)

        if unreferenced_sheets:
            lines.append(f"\n### Unreferenced Sheets (not in hierarchy)")
            lines.append("*These sheets are not used by the main schematic but may contain useful information.*")
            for uf in sorted(unreferenced_sheets):
                lines.append(f"- `{uf.relative_to(project_dir)}`")
                # Still parse them for components
                try:
                    from ..kicad_parser.schematic import Schematic
                    sch = Schematic.load(uf)
                    if sch.symbols:
                        lines.append(f"  ({len(sch.symbols)} symbol(s))")
                except Exception:
                    pass

    # -----------------------------------------------------------------
    # 3. Component BOM
    # -----------------------------------------------------------------
    if all_symbols:
        lines.append(f"\n## Component List ({len(all_symbols)} components)")
        lines.append("")
        lines.append("| Reference | Value | Description | Footprint |")
        lines.append("|-----------|-------|-------------|-----------|")
        # Sort by reference designator
        all_symbols.sort(key=lambda s: _ref_sort_key(s.get("reference", "")))
        for sym in all_symbols:
            ref = sym.get("reference", "?")
            val = sym.get("value", "")
            desc = sym.get("description", "")
            fp = sym.get("footprint", "")
            # Shorten footprint for readability
            fp_short = fp.split(":")[-1] if ":" in fp else fp
            lines.append(f"| {ref} | {val} | {desc} | {fp_short} |")

    # -----------------------------------------------------------------
    # 4. Netlist
    # -----------------------------------------------------------------
    if all_nets:
        lines.append(f"\n## Netlist ({len(all_nets)} nets)")
        lines.append("")
        for net_name in sorted(all_nets.keys()):
            pins = all_nets[net_name]
            if pins:
                lines.append(f"- **{net_name}**: {', '.join(sorted(pins))}")
            else:
                lines.append(f"- **{net_name}**: *(no connections)*")

    # -----------------------------------------------------------------
    # 5. PCB board info (if available)
    # -----------------------------------------------------------------
    for board_path in all_boards:
        try:
            from ..kicad_parser.pcb import PCBBoard
            board = PCBBoard.load(board_path)
            lines.append(f"\n## PCB Board: `{board_path.relative_to(project_dir)}`")
            lines.append(f"- Footprints: {len(board.footprints)}")
            lines.append(f"- Nets: {len(board.nets)}")
            lines.append(f"- Tracks: {len(board.tracks)}")
            lines.append(f"- Vias: {len(board.vias)}")
            stackup = board.get_layer_stackup()
            if stackup:
                lines.append(f"- Layer stackup: {len(stackup)} layers")
                for layer in stackup[:8]:
                    lines.append(f"  - {layer}")
                if len(stackup) > 8:
                    lines.append(f"  - ... ({len(stackup) - 8} more)")
        except Exception as exc:
            logger.debug("Failed to parse PCB %s: %s", board_path, exc)

    # -----------------------------------------------------------------
    # Assemble and enforce size limit
    # -----------------------------------------------------------------
    result = "\n".join(lines)
    if len(result) > _MAX_RAW_CONTEXT_CHARS:
        result = result[:_MAX_RAW_CONTEXT_CHARS].rsplit("\n", 1)[0]
        result += "\n\n*(context truncated to fit token budget)*"

    return result


def get_llm_synthesized_context(
    raw_context: str,
    provider: Any,
    model: Optional[str] = None,
) -> str:
    """Use the LLM to synthesize a compact, structured summary of the raw context.

    The LLM reads the raw context (schematic hierarchy, BOM, netlist) and
    produces a clean, organized summary that is more digestible for ongoing
    conversation use.

    Args:
        raw_context: The raw context string from :func:`get_raw_context`.
        provider: An :class:`~kiassist_utils.ai.base.AIProvider` instance.
        model: Optional model identifier override.

    Returns:
        The LLM-synthesized context string.
    """
    from ..ai.base import AIMessage

    synthesis_prompt = (
        "You are a KiCad PCB design assistant. Analyze the following raw project context "
        "and produce a clean, organized summary. Your summary should:\n\n"
        "1. **Project Overview**: Brief description of what this project appears to be\n"
        "2. **Schematic Structure**: Which sheets are in the hierarchy and what each contains\n"
        "3. **Key Components**: Organized by function (power, MCU, connectors, passives, etc.) "
        "with reference, value, and footprint\n"
        "4. **Power Nets**: List all power rails and their connections\n"
        "5. **Signal Nets**: List important signal nets and what they connect\n"
        "6. **Design Notes**: Any observations about the design (missing connections, "
        "potential issues, design patterns used)\n\n"
        "Be concise but thorough. Use markdown formatting. "
        "This summary will be used as context for future conversations about this project.\n\n"
        "---\n\n"
        f"{raw_context}"
    )

    messages = [AIMessage(role="user", content=synthesis_prompt)]
    system_prompt = (
        "You are analyzing a KiCad electronics project. "
        "Produce a structured, concise summary of the project context. "
        "Focus on information that would be useful for an engineer working on this project."
    )

    try:
        response = provider.chat(messages, system_prompt=system_prompt)
        return response.content
    except Exception as exc:
        logger.error("LLM synthesis failed: %s", exc)
        return f"*(Synthesis failed: {exc})*\n\n{raw_context}"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _find_main_schematic(
    project_dir: Path,
    all_schematics: List[Path],
) -> Optional[Path]:
    """Find the main/root schematic for the project.

    Heuristics:
    1. A ``.kicad_sch`` file whose stem matches a ``.kicad_pro`` file stem.
    2. A file named after the project directory.
    3. The first schematic file found.
    """
    pro_files = sorted(project_dir.glob("*.kicad_pro"))
    if pro_files:
        pro_stem = pro_files[0].stem
        for sf in all_schematics:
            if sf.stem == pro_stem and sf.parent == project_dir:
                return sf

    # Fallback: file named after directory
    dir_name = project_dir.name
    for sf in all_schematics:
        if sf.stem == dir_name and sf.parent == project_dir:
            return sf

    # Fallback: first schematic in the project root
    root_schematics = [sf for sf in all_schematics if sf.parent == project_dir]
    if root_schematics:
        return root_schematics[0]

    return all_schematics[0] if all_schematics else None


def _collect_hierarchy(
    project_dir: Path,
    schematic_path: Path,
    referenced_sheets: Set[Path],
    all_symbols: List[Dict[str, str]],
    all_nets: Dict[str, List[str]],
    lines: List[str],
    depth: int = 0,
) -> None:
    """Recursively parse a schematic and its sub-sheets.

    Populates *referenced_sheets*, *all_symbols*, *all_nets*, and appends
    descriptive lines for the hierarchy output.
    """
    from ..kicad_parser.schematic import Schematic

    indent = "  " * depth
    rel_path = schematic_path.relative_to(project_dir)

    try:
        sch = Schematic.load(schematic_path)
    except Exception as exc:
        lines.append(f"{indent}- `{rel_path}` *(parse error: {exc})*")
        return

    # Count symbols (excluding power symbols which have refs starting with #)
    real_symbols = [s for s in sch.symbols if not s.reference.startswith("#")]
    lines.append(
        f"{indent}- `{rel_path}` — {len(real_symbols)} component(s), "
        f"{len(sch.wires)} wire(s), {len(sch.sheets)} sub-sheet(s)"
    )

    # Collect components
    for sym in real_symbols:
        desc = ""
        for prop in sym.properties:
            if prop.key.lower() in ("description", "desc"):
                desc = prop.value
                break
        all_symbols.append({
            "reference": sym.reference,
            "value": sym.value,
            "footprint": sym.footprint,
            "description": desc,
            "sheet": str(rel_path),
        })

    # Collect nets from this sheet
    try:
        sheet_nets = sch.get_connected_nets()
        for net_name, pin_refs in sheet_nets.items():
            if net_name not in all_nets:
                all_nets[net_name] = []
            for pr in pin_refs:
                if pr not in all_nets[net_name]:
                    all_nets[net_name].append(pr)
    except Exception as exc:
        logger.debug("Failed to extract nets from %s: %s", schematic_path, exc)

    # Recurse into sub-sheets
    for sheet in sch.sheets:
        sheet_filename = None
        for prop in sheet.properties:
            if prop.key.lower() in ("sheetfile", "sheet file"):
                sheet_filename = prop.value
                break
            # Also check "Sheetfile" (KiCad 8+ capitalisation)
            if prop.key == "Sheetfile":
                sheet_filename = prop.value
                break

        if not sheet_filename:
            continue

        # Resolve relative to the parent schematic's directory
        sub_path = (schematic_path.parent / sheet_filename).resolve()
        if not sub_path.is_file():
            # Try relative to project dir
            sub_path = (project_dir / sheet_filename).resolve()

        if sub_path.is_file() and sub_path not in referenced_sheets:
            referenced_sheets.add(sub_path)
            _collect_hierarchy(
                project_dir, sub_path, referenced_sheets,
                all_symbols, all_nets, lines, depth=depth + 1,
            )
        elif not sub_path.is_file():
            lines.append(f"{indent}  - `{sheet_filename}` *(file not found)*")


def _ref_sort_key(ref: str) -> Tuple[str, int]:
    """Sort key for reference designators: prefix alphabetically, number numerically."""
    prefix = ""
    num_str = ""
    for ch in ref:
        if ch.isdigit():
            num_str += ch
        else:
            if num_str:
                break
            prefix += ch
    return (prefix, int(num_str) if num_str else 0)
