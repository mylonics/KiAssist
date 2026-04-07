"""AI-assisted symbol operations for the importer.

Provides two capabilities:

1. **Symbol suggestion** — Given a component's name, MPN, description, and
   package, ask the AI to suggest the best matching KiCad native symbol to use
   as a graphical template.

2. **Pin mapping** — Given the pin list of an imported symbol and the pin list
   of a chosen KiCad base symbol, ask the AI to produce a mapping table that
   assigns each base pin to its corresponding imported pin number.  The result
   is then applied to produce a merged symbol with the native graphics but the
   correct pin numbers.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class SymbolSuggestion:
    """A single AI-generated symbol suggestion."""

    library: str
    name: str
    reason: str
    confidence: str = "medium"  # "high" | "medium" | "low"


@dataclass
class PinMapping:
    """Result of AI pin-mapping."""

    # Map of base-symbol pin-number → imported pin-number (None = unmapped)
    mapping: Dict[str, Optional[str]] = field(default_factory=dict)
    notes: str = ""
    warnings: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Prompt builders
# ---------------------------------------------------------------------------

_SUGGEST_SYSTEM = """\
You are a KiCad EDA expert.  Your job is to recommend the most suitable
KiCad built-in symbol to serve as a graphical template for an imported
component.  The user wants a visually clean, well-drawn symbol so they can
reuse its graphics rather than using a potentially ugly auto-imported one.

Respond ONLY with a JSON array (no markdown fences, no extra text).
Each element must be an object with exactly these keys:
  "library"     – KiCad library nickname (e.g. "Device", "Interface_USB")
  "name"        – symbol name within that library (e.g. "R", "NE555")
  "reason"      – one sentence explaining why this symbol is a good fit
  "confidence"  – one of "high", "medium", "low"

Return at most 5 suggestions, ordered best-first.
"""

_SUGGEST_USER_TMPL = """\
Component to match:
  MPN:         {mpn}
  Manufacturer: {manufacturer}
  Description: {description}
  Package:     {package}
  Pins:        {pin_summary}

Available KiCad libraries (sample — more exist):
{lib_sample}

Suggest KiCad symbols that could serve as a visual template for this component.
Focus on symbol topology (pin count, function) rather than exact part match.
"""

_MAPPING_SYSTEM = """\
You are a KiCad EDA expert.  Your job is to map the pins of an imported
component onto the pins of a chosen KiCad base symbol.

You will be given:
- "imported_pins": list of {number, name} for the imported component
- "base_pins":    list of {number, name} for the chosen KiCad symbol

Produce a mapping that assigns each BASE pin number to the corresponding
IMPORTED pin number.  Match by function / name similarity.  If there is no
good match for a base pin, leave its value as null.

Respond ONLY with a JSON object (no markdown, no extra text):
{
  "mapping": {"<base_pin_number>": "<imported_pin_number_or_null>", ...},
  "notes": "<optional brief explanation>",
  "warnings": ["<any mismatch or ambiguity warnings>"]
}
"""

_MAPPING_USER_TMPL = """\
Imported component: {mpn}
Imported pins:
{imported_pins_json}

KiCad base symbol: {lib}:{name}
Base symbol pins:
{base_pins_json}

Map each base pin to the corresponding imported pin by function/name.
"""

_GENERATE_SYSTEM = """\
You are a KiCad EDA expert.  Your job is to generate a KiCad 6+ symbol
S-expression for a component from its datasheet / description information.

Rules:
- Output ONLY valid KiCad 6 symbol S-expression (starting with "(symbol ..."),
  no markdown, no explanation.
- Include standard properties: Reference, Value, Footprint, Datasheet,
  Description, MPN, MF.
- Draw pins in a sensible arrangement (inputs left, outputs right,
  power pins top/bottom).
- Use standard electrical types: input, output, bidirectional, power_in,
  power_out, passive, no_connect.
- Coordinate system: 1 mil = 0.0254 mm, use 2.54 mm pin spacing.
"""

_GENERATE_USER_TMPL = """\
Generate a KiCad 6 symbol for:
  MPN:          {mpn}
  Manufacturer: {manufacturer}
  Description:  {description}
  Package:      {package}
  Reference:    {reference}
  Datasheet:    {datasheet}

Pin list (number, name, direction):
{pin_list}

Output a single (symbol "...") S-expression block only.
"""


# ---------------------------------------------------------------------------
# AI caller type
# ---------------------------------------------------------------------------

# A callable that takes a system prompt + user prompt and returns the response
# text.  Injected by the caller (KiAssistAPI) to avoid a circular import.
AICaller = Callable[[str, str], str]


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


def suggest_symbol(
    mpn: str,
    manufacturer: str,
    description: str,
    package: str,
    pin_summary: str,
    available_libraries: List[str],
    call_ai: AICaller,
) -> Tuple[List[SymbolSuggestion], str]:
    """Ask the AI to suggest KiCad native symbols as graphical templates.

    Parameters
    ----------
    mpn, manufacturer, description, package, pin_summary:
        Component metadata used to build the prompt.
    available_libraries:
        List of KiCad library nicknames discovered on the system.
    call_ai:
        Callable ``(system_prompt, user_prompt) → response_text``.

    Returns
    -------
    (suggestions, raw_response)
        *suggestions* is a list of :class:`SymbolSuggestion`; *raw_response*
        is the AI text for debugging.
    """
    lib_sample = "\n".join(f"  - {lib}" for lib in available_libraries[:40])
    user_prompt = _SUGGEST_USER_TMPL.format(
        mpn=mpn or "(unknown)",
        manufacturer=manufacturer or "(unknown)",
        description=description or "(unknown)",
        package=package or "(unknown)",
        pin_summary=pin_summary or "(unknown)",
        lib_sample=lib_sample or "  (none discovered)",
    )
    raw = call_ai(_SUGGEST_SYSTEM, user_prompt)
    suggestions = _parse_suggestions(raw)
    return suggestions, raw


def map_pins(
    mpn: str,
    imported_pins: List[Dict[str, str]],
    base_symbol_lib: str,
    base_symbol_name: str,
    base_pins: List[Dict[str, str]],
    call_ai: AICaller,
) -> Tuple[PinMapping, str]:
    """Ask the AI to map imported component pins onto a base KiCad symbol's pins.

    Parameters
    ----------
    mpn:
        Imported component part number (for context).
    imported_pins:
        List of ``{"number": "...", "name": "..."}`` dicts from the imported symbol.
    base_symbol_lib, base_symbol_name:
        Source library and name of the chosen KiCad base symbol.
    base_pins:
        List of ``{"number": "...", "name": "..."}`` dicts from the base symbol.
    call_ai:
        Callable ``(system_prompt, user_prompt) → response_text``.

    Returns
    -------
    (mapping, raw_response)
    """
    user_prompt = _MAPPING_USER_TMPL.format(
        mpn=mpn or "(unknown)",
        imported_pins_json=json.dumps(imported_pins, indent=2),
        lib=base_symbol_lib,
        name=base_symbol_name,
        base_pins_json=json.dumps(base_pins, indent=2),
    )
    raw = call_ai(_MAPPING_SYSTEM, user_prompt)
    pin_map = _parse_pin_mapping(raw)
    return pin_map, raw


def generate_symbol(
    mpn: str,
    manufacturer: str,
    description: str,
    package: str,
    reference: str,
    datasheet: str,
    pins: List[Dict[str, str]],
    call_ai: AICaller,
) -> Tuple[str, str]:
    """Ask the AI to generate a KiCad 6 symbol S-expression from scratch.

    Parameters
    ----------
    pins:
        List of ``{"number": "...", "name": "...", "direction": "..."}`` dicts.
    call_ai:
        Callable ``(system_prompt, user_prompt) → response_text``.

    Returns
    -------
    (symbol_sexpr, raw_response)
        *symbol_sexpr* is the extracted S-expression (may be empty on failure).
    """
    pin_list = "\n".join(
        f"  pin {p.get('number', '?')}: {p.get('name', '?')} ({p.get('direction', 'bidirectional')})"
        for p in pins
    )
    user_prompt = _GENERATE_USER_TMPL.format(
        mpn=mpn or "(unknown)",
        manufacturer=manufacturer or "(unknown)",
        description=description or "(unknown)",
        package=package or "(unknown)",
        reference=reference or "U",
        datasheet=datasheet or "~",
        pin_list=pin_list or "  (none provided)",
    )
    raw = call_ai(_GENERATE_SYSTEM, user_prompt)
    sym_sexpr = _extract_sexpr(raw)
    return sym_sexpr, raw


def extract_pins_from_symbol(symbol_sexpr: str) -> List[Dict[str, str]]:
    """Parse a symbol S-expression and return a list of pin dicts.

    Returns a list of ``{"number": "...", "name": "...", "type": "..."}``.
    Uses a lightweight regex approach that works without full S-expr parsing.
    """
    pins: List[Dict[str, str]] = []
    # Match: (pin <type> <style> ... (name "...") ... (number "...") ...)
    for m in re.finditer(
        r'\(pin\s+(\S+)\s+\S+.*?\(name\s+"([^"]*)".*?\(number\s+"([^"]*)"',
        symbol_sexpr,
        re.DOTALL,
    ):
        pins.append({"type": m.group(1), "name": m.group(2), "number": m.group(3)})
    return pins


def apply_pin_mapping(
    base_symbol_sexpr: str,
    pin_mapping: PinMapping,
) -> str:
    """Rewrite pin numbers in *base_symbol_sexpr* using *pin_mapping*.

    For each ``(number "<base_num>")`` in the S-expression that appears in
    ``pin_mapping.mapping``, replace it with the mapped imported pin number.

    Parameters
    ----------
    base_symbol_sexpr:
        KiCad 6 symbol S-expression text.
    pin_mapping:
        Mapping produced by :func:`map_pins`.

    Returns
    -------
    str
        The modified symbol S-expression.
    """
    result = base_symbol_sexpr
    for base_num, imported_num in pin_mapping.mapping.items():
        if imported_num is None:
            continue
        # Replace (number "<base_num>") with (number "<imported_num>")
        result = re.sub(
            r'\(number\s+"' + re.escape(str(base_num)) + r'"',
            f'(number "{imported_num}"',
            result,
        )
    return result


# ---------------------------------------------------------------------------
# Private parsers
# ---------------------------------------------------------------------------


def _parse_suggestions(raw: str) -> List[SymbolSuggestion]:
    """Extract a list of :class:`SymbolSuggestion` from the AI response."""
    text = _strip_fences(raw)
    try:
        data = json.loads(text)
        if not isinstance(data, list):
            data = [data]
        suggestions = []
        for item in data:
            if not isinstance(item, dict):
                continue
            suggestions.append(
                SymbolSuggestion(
                    library=str(item.get("library", "")),
                    name=str(item.get("name", "")),
                    reason=str(item.get("reason", "")),
                    confidence=str(item.get("confidence", "medium")),
                )
            )
        return suggestions
    except json.JSONDecodeError:
        logger.warning("AI suggestion response could not be parsed as JSON: %r", raw[:200])
        return []


def _parse_pin_mapping(raw: str) -> PinMapping:
    """Extract a :class:`PinMapping` from the AI response."""
    text = _strip_fences(raw)
    try:
        data = json.loads(text)
        mapping = {str(k): (str(v) if v is not None else None) for k, v in data.get("mapping", {}).items()}
        return PinMapping(
            mapping=mapping,
            notes=str(data.get("notes", "")),
            warnings=list(data.get("warnings", [])),
        )
    except json.JSONDecodeError:
        logger.warning("AI pin-mapping response could not be parsed as JSON: %r", raw[:200])
        return PinMapping(mapping={}, notes="", warnings=["AI response could not be parsed"])


def _extract_sexpr(raw: str) -> str:
    """Extract the first top-level S-expression starting with ``(symbol``."""
    # Remove any markdown fences
    text = _strip_fences(raw)
    start = text.find("(symbol")
    if start == -1:
        return ""
    # Find matching closing paren
    depth = 0
    for i, ch in enumerate(text[start:], start=start):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return text[start:]


def _strip_fences(text: str) -> str:
    """Remove markdown code fences from *text*."""
    text = re.sub(r"```[a-zA-Z]*\n?", "", text)
    text = re.sub(r"```", "", text)
    return text.strip()
