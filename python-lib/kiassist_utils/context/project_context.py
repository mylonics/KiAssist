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
import re
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
    # Folders to skip: version-control, backups, history, caches
    _IGNORE_DIRS = {".history", ".git", "__pycache__", "backups", "_autosave"}

    def _is_ignored(path: Path) -> bool:
        return any(part.startswith(".") or part in _IGNORE_DIRS for part in path.relative_to(project_dir).parts[:-1])

    all_schematics = sorted(f for f in project_dir.rglob("*.kicad_sch") if not _is_ignored(f))
    all_boards = sorted(f for f in project_dir.rglob("*.kicad_pcb") if not _is_ignored(f))
    pro_files = sorted(f for f in project_dir.rglob("*.kicad_pro") if not _is_ignored(f))

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


def build_refine_questions_prompt(
    initial_answers: List[Dict[str, str]],
) -> Dict[str, Any]:
    """Build a lightweight prompt for refining wizard questions.

    This prompt is intentionally kept small (no raw context) so the LLM can
    respond quickly.  It receives the user's initial answers and the list of
    remaining default wizard questions.  The LLM decides which remaining
    questions are still relevant and may add new ones.

    Args:
        initial_answers: Q&A dicts from the first wizard questions.

    Returns:
        Dict with ``messages`` (list of :class:`AIMessage`) and
        ``system_prompt`` (str).
    """
    from ..ai.base import AIMessage
    from ..requirements_wizard import get_default_questions, INITIAL_QUESTIONS_COUNT

    # Format initial Q&A
    qa_lines = "\n".join(
        f"Q: {qa['question']}\nA: {qa['answer']}"
        for qa in initial_answers
    )

    # Remaining default wizard questions (used as reference categories only)
    remaining = get_default_questions()[INITIAL_QUESTIONS_COUNT:]
    category_hints = ", ".join(sorted({q["category"] for q in remaining}))

    prompt = (
        "A user is defining requirements for a PCB project.  They have "
        "answered these initial questions:\n\n"
        f"{qa_lines}\n\n"
        "Your job is to identify the **3 to 5 most important** unanswered "
        "requirements for this specific project.  Focus only on questions "
        "whose answers would materially affect the PCB design.\n\n"
        "## Rules\n"
        "- Do NOT re-ask anything the user already answered above.\n"
        "- Do NOT ask vague or generic questions.  Every question must be "
        "specific to what the user described.\n"
        "- Ask only about things that drive schematic/layout decisions: "
        "power architecture, key interfaces, physical constraints, or "
        "critical component choices.\n"
        f"- Categories to consider: {category_hints}.  Drop any category "
        "that does not apply to this project.\n"
        "- Limit yourself to **5 questions maximum**.\n\n"
        "## Suggestions\n"
        "For EACH question, provide 2-4 suggested answers that are:\n"
        "- Concrete and specific (include part numbers, values, or specs)\n"
        "- Realistically different options an engineer would weigh\n"
        "- Self-contained — the user should be able to pick one as-is\n"
        "Bad example: \"Depends on project needs\" — this is useless.\n"
        "Good example: \"3.3 V single rail via AMS1117-3.3 LDO from 5 V USB\" "
        "— this is specific and actionable.\n\n"
        "Return ONLY a JSON array of objects:\n"
        '  { "question": "<text>", "suggestions": ["<a1>", "<a2>", ...] }\n\n'
        "Return ONLY valid JSON — no markdown fences, no explanation."
    )

    return {
        "messages": [AIMessage(role="user", content=prompt)],
        "system_prompt": (
            "You are an experienced PCB design engineer helping define "
            "project requirements.  Ask only high-impact questions whose "
            "answers directly affect the board design.  Provide realistic, "
            "specific suggested answers with concrete specs or part numbers.  "
            "Return ONLY a valid JSON array — no prose."
        ),
    }

def build_context_questions_prompt(
    raw_context: str,
    existing_requirements: str,
    wizard_answers: Optional[List[Dict[str, str]]] = None,
) -> Dict[str, Any]:
    """Build the messages and system prompt for context-question generation.

    Args:
        raw_context: Extracted project context text.
        existing_requirements: Contents of an existing ``requirements.md``, if
            any.
        wizard_answers: Optional list of Q&A dicts from the initial wizard
            questions the user answered before this LLM call.

    Returns:
        Dict with ``messages`` (list of :class:`AIMessage`) and
        ``system_prompt`` (str).
    """
    from ..ai.base import AIMessage

    requirements_section = ""
    if existing_requirements.strip():
        requirements_section = (
            "\n\n## Existing Requirements\n\n" + existing_requirements
        )

    wizard_section = ""
    if wizard_answers:
        qa_lines = "\n".join(
            f"Q: {qa['question']}\nA: {qa['answer']}\n"
            for qa in wizard_answers
        )
        wizard_section = (
            "\n\n## Already Answered (DO NOT re-ask)\n\n"
            "The user has already provided the following information.  "
            "Treat these as settled — do NOT ask about any topic already "
            "covered here.\n\n" + qa_lines
        )

    prompt = (
        "You are a KiCad PCB design assistant reviewing a real project."
        + requirements_section
        + wizard_section
        + "\n\nBelow is the raw project context extracted from KiCad "
        "schematic and board files.  Analyze it and ask **3 to 5** "
        "questions that address the most critical gaps preventing you "
        "from writing a complete requirements document.\n\n"
        "## Rules\n"
        "- Only ask questions whose answers **directly affect** the PCB "
        "schematic or layout.  Skip nice-to-know questions.\n"
        "- Never re-ask something already answered above or clearly "
        "visible in the raw context (e.g. don't ask about voltage rails "
        "that are already in the netlist).\n"
        "- Reference specific components, nets, or sheets from the context "
        "to make questions concrete.\n"
        "- Maximum **5 questions**.  Fewer is better.\n"
        "- If the context and existing answers are already sufficient to "
        "write a requirements document, return an **empty array** `[]`.\n\n"
        "## Suggestions\n"
        "For EACH question, provide 2-4 suggested answers that:\n"
        "- Are specific and actionable (include values, part numbers, or "
        "specs where possible)\n"
        "- Represent realistically different engineering trade-offs\n"
        "- Could be selected as-is without modification\n"
        "BAD: \"It depends on the application\" — useless.\n"
        "GOOD: \"100 mm × 80 mm, 4-layer, 1.6 mm FR4\" — actionable.\n\n"
        "IMPORTANT: If the context lists 'Unreferenced Sheets (not in "
        "hierarchy)', those sheets are NOT actively used.  Focus on the "
        "main hierarchy.\n\n"
        "Return ONLY a JSON array of objects:\n"
        '  { "question": "<text>", "suggestions": ["<a1>", "<a2>", ...] }\n\n'
        "Return an empty array `[]` if no questions are needed.\n\n"
        "---\n\n"
        f"{raw_context}"
    )

    return {
        "messages": [AIMessage(role="user", content=prompt)],
        "system_prompt": (
            "You are an experienced PCB design engineer.  Identify only "
            "the critical gaps in the project information.  Ask at most 5 "
            "questions.  Provide specific, actionable suggested answers "
            "with real specs/values.  Return ONLY valid JSON — no prose, "
            "no markdown fences."
        ),
    }


def _fix_json_escapes(text: str) -> str:
    r"""Fix invalid JSON escape sequences produced by LLMs.

    LLMs often emit Windows paths (``C:\Users\...``) or other bare
    backslashes inside JSON string values.  ``json.loads`` rejects
    ``\U``, ``\S``, etc. because only ``\" \\ \/ \b \f \n \r \t \uXXXX``
    are valid.  This function doubles any backslash that is NOT part of a
    recognised JSON escape so that the result is valid JSON.
    """
    # Protect already-escaped backslash pairs (\\) so the regex below
    # doesn't treat the second \ as a new escape start.
    _PH = "\x00_DBLBS_\x00"
    text = text.replace("\\\\", _PH)
    # Fix lone backslashes not followed by a valid JSON escape character.
    text = re.sub(r'\\(?!["\\/bfnrtu])', r'\\\\', text)
    # Restore the protected pairs.
    text = text.replace(_PH, "\\\\")
    return text


def _fix_raw_newlines_in_json_strings(text: str) -> str:
    r"""Escape literal newlines / carriage-returns that sit inside JSON string values.

    LLMs frequently produce multi-line content (e.g. Markdown) as the value
    of a JSON key and forget to escape the newlines.  ``json.loads`` then
    fails with ``Expecting ',' delimiter`` because a raw newline terminates
    the string token.

    Strategy: walk through the text character-by-character, tracking whether
    we are inside a JSON string (between unescaped double-quotes).  Any raw
    ``\n`` or ``\r`` found inside a string is replaced with ``\\n`` / ``\\r``.
    """
    out: list[str] = []
    in_string = False
    i = 0
    length = len(text)
    while i < length:
        ch = text[i]
        if ch == '\\' and in_string and i + 1 < length:
            # Escaped character — emit both and skip next
            out.append(ch)
            out.append(text[i + 1])
            i += 2
            continue
        if ch == '"':
            in_string = not in_string
            out.append(ch)
        elif in_string and ch == '\n':
            out.append('\\n')
        elif in_string and ch == '\r':
            # skip \r if next char is \n (will be handled next iteration)
            if i + 1 < length and text[i + 1] == '\n':
                pass  # swallow bare \r before \n
            else:
                out.append('\\r')
        elif in_string and ch == '\t':
            out.append('\\t')
        else:
            out.append(ch)
        i += 1
    return "".join(out)


def _clean_llm_json(text: str) -> str:
    """Strip thinking tags, code fences, and surrounding prose from LLM output.

    Models like Gemma emit ``<think>...</think>`` reasoning blocks and may
    wrap JSON in markdown fences or add explanatory text.  This helper
    progressively cleans the text so that ``json.loads`` has the best chance
    of succeeding.
    """
    # 1. Strip <think>...</think> blocks (defence-in-depth; main.py also does this)
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()

    # 2. Extract content from markdown code fences (```json ... ``` etc.)
    fence_match = re.search(r"```(?:\w*)\s*\n(.*?)```", text, flags=re.DOTALL)
    if fence_match:
        text = fence_match.group(1).strip()

    # 3. If the text still doesn't start with [ or {, try to find the first
    #    JSON array or object within the text.
    if text and text[0] not in ("[", "{"):
        arr = re.search(r"(\[.*\])", text, flags=re.DOTALL)
        obj = re.search(r"(\{.*\})", text, flags=re.DOTALL)
        if arr:
            text = arr.group(1)
        elif obj:
            text = obj.group(1)

    # 4. Fix literal newlines/tabs inside JSON string values
    text = _fix_raw_newlines_in_json_strings(text)

    # 5. Fix invalid backslash escapes (e.g. Windows paths like C:\Users)
    text = _fix_json_escapes(text)

    # 6. Remove trailing commas before ] or } (common LLM mistake)
    text = re.sub(r',\s*([}\]])', r'\1', text)

    return text.strip()


def parse_context_questions_response(
    response_text: str,
) -> List[Dict[str, Any]]:
    """Parse the LLM response into a list of question dicts.

    Each dict has ``question`` (str) and ``suggestions`` (list of str).
    Falls back to the old plain-string format for backwards compatibility.
    """
    import json as _json

    text = _clean_llm_json(response_text)
    if not text:
        logger.error("Context questions response was empty after cleaning")
        return []

    try:
        parsed = _json.loads(text)
    except Exception as exc:
        logger.error(
            "Failed to parse context questions: %s  (cleaned text: %.200s)",
            exc,
            text,
        )
        return []

    if not isinstance(parsed, list) or not parsed:
        logger.warning("LLM returned non-list for questions: %s", type(parsed))
        return []

    result: List[Dict[str, Any]] = []
    for item in parsed:
        if isinstance(item, str):
            # Legacy plain-string format
            result.append({"question": item, "suggestions": []})
        elif isinstance(item, dict) and "question" in item:
            suggestions = item.get("suggestions", [])
            if not isinstance(suggestions, list):
                suggestions = []
            result.append({
                "question": str(item["question"]),
                "suggestions": [str(s) for s in suggestions[:4]],
            })
        else:
            logger.warning("Skipping unrecognised question item: %s", item)
    return result


def build_requirements_and_context_prompt(
    raw_context: str,
    existing_requirements: str,
    questions_and_answers: List[Dict[str, str]],
    *,
    force_complete: bool = False,
) -> Dict[str, Any]:
    """Build the messages and system prompt for requirements/context generation.

    Args:
        raw_context: Raw context text from KiCad files.
        existing_requirements: Existing ``requirements.md`` content (may be
            empty).
        questions_and_answers: List of Q&A dicts the user provided.
        force_complete: When ``True`` the LLM is told it **must** produce a
            final result — ``"needs_more_info"`` is not an option.

    Returns:
        Dict with ``messages`` (list of :class:`AIMessage`) and
        ``system_prompt`` (str).
    """
    from ..ai.base import AIMessage

    qa_text = "\n".join(
        f"Q: {qa['question']}\nA: {qa['answer']}\n"
        for qa in questions_and_answers
    )

    requirements_section = ""
    if existing_requirements.strip():
        requirements_section = (
            "\n\n## Existing Requirements\n\n" + existing_requirements
        )

    if force_complete:
        decision_block = (
            "You MUST produce the final output now.  Do NOT return "
            '"needs_more_info".  Use reasonable engineering defaults for '
            "any information you do not have.  Mark assumptions clearly "
            "in the requirements document with *(assumed)*."
        )
    else:
        decision_block = (
            "Decide whether you have enough information:\n"
            "- **Strongly prefer producing a result.**  If you can write a "
            "reasonable requirements document with the information provided, "
            "do so — even if some details are missing.  Mark assumptions "
            "with *(assumed)* in the document.\n"
            '- Only return "needs_more_info" if a **critical** piece of '
            "information is completely missing AND would fundamentally "
            "change the design (e.g. you don't know the purpose of the "
            "board at all).  Nice-to-have details are NOT critical."
        )

    prompt = (
        "You are a KiCad PCB design assistant.  You have:\n"
        "1. Raw project context extracted from KiCad files\n"
        "2. The user's answers to clarifying questions\n"
        + (
            "3. Existing requirements for this project\n"
            if existing_requirements.strip()
            else ""
        )
        + "\n" + decision_block + "\n\n"
        "When producing the result, create:\n"
        "a) A concise technical **requirements document** (Markdown) — "
        "organised into clear sections, written as an engineer would write "
        "it.  Use standard ASCII only, no emojis.\n"
        "b) A **synthesized project context** — a compact summary of the "
        "design (project overview, schematic structure, key components by "
        "function, power nets, signal nets, design notes).\n\n"
        "Return ONLY a JSON object:\n"
        '{"status": "done", "requirements": "<markdown>", '
        '"synthesized_context": "<markdown>"}\n\n'
        + (
            ""
            if force_complete
            else (
                "Or, ONLY if truly critical info is missing:\n"
                '{"status": "needs_more_info", "questions": ["Q1", "Q2"]}\n'
                "(maximum 3 questions)\n\n"
            )
        )
        + "Return ONLY valid JSON — no markdown fences around the entire "
        "response, no extra text.\n\n"
        "---\n\n"
        f"## Raw Project Context\n\n{raw_context}\n\n"
        f"## User Q&A\n\n{qa_text}"
        + requirements_section
    )

    return {
        "messages": [AIMessage(role="user", content=prompt)],
        "system_prompt": (
            "You are an experienced PCB design engineer producing a "
            "requirements document and project summary.  Strongly prefer "
            "producing a final result over asking more questions.  "
            "Return ONLY valid JSON — no prose, no markdown fences."
        ),
    }


def parse_requirements_and_context_response(response_text: str) -> Dict[str, Any]:
    """Parse the LLM response text into a requirements/context result dict.

    Returns:
        Dict with ``status`` (``"done"`` | ``"needs_more_info"`` | ``"error"``)
        and associated data.
    """
    import json as _json

    text = _clean_llm_json(response_text)
    if not text:
        logger.error("Requirements/context response was empty after cleaning")
        return {"status": "error", "error": "Empty response from LLM"}

    try:
        result = _json.loads(text)
        if isinstance(result, dict):
            status = result.get("status", "")
            if status == "done":
                return {
                    "status": "done",
                    "requirements": result.get("requirements", ""),
                    "synthesized_context": result.get("synthesized_context", ""),
                }
            elif status == "needs_more_info":
                questions = result.get("questions", [])
                if isinstance(questions, list):
                    return {
                        "status": "needs_more_info",
                        "questions": [str(q) for q in questions],
                    }
        logger.warning("Unexpected LLM response structure: %s", text[:200])
        return {"status": "done", "requirements": "", "synthesized_context": text}
    except Exception as exc:
        logger.error(
            "Requirements/context parse failed: %s  (cleaned text: %.200s)",
            exc,
            text,
        )
        return {"status": "error", "error": str(exc)}


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
