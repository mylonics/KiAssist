"""Import / reuse components from existing KiCad symbol and footprint libraries.

Supports three modes:
- *Search*: list symbols or footprints matching a query string.
- *Import*: clone an existing symbol (and optionally its footprint) into a
  target library, with field normalisation.
- *Add variant*: clone a template symbol (e.g. a passive) in the same library
  with updated fields (Value, MPN, DKPN, LCSC, etc.) — ideal for quickly
  populating resistor/capacitor/inductor/diode libraries.
"""

from __future__ import annotations

import copy
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..kicad_parser.symbol_lib import SymbolLibrary, SymbolDef
from ..kicad_parser.footprint import Footprint
from ..kicad_parser.library import LibraryDiscovery
from ..kicad_parser.sexpr import QStr, SExpr
from .models import FieldSet, ImportedComponent, ImportMethod, ImportResult
from .field_normalizer import normalize_fields, build_raw_field_dict

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Search helpers
# ---------------------------------------------------------------------------


def search_symbols(
    query: str,
    library_name: Optional[str] = None,
    project_dir: Optional[str | Path] = None,
    max_results: int = 50,
) -> List[Dict[str, str]]:
    """Search for symbols across KiCad symbol libraries.

    Parameters
    ----------
    query:
        Case-insensitive substring to match against symbol names and
        descriptions.
    library_name:
        If given, search only that library nickname.  Otherwise all
        discovered libraries are searched.
    project_dir:
        Used by :class:`LibraryDiscovery` to find project-local tables.
    max_results:
        Hard cap on the number of results returned.

    Returns
    -------
    list of dicts
        Each dict has ``library``, ``name``, ``description``, ``value``,
        ``footprint`` keys.
    """
    disc = LibraryDiscovery(project_dir=str(project_dir) if project_dir else None)
    query_lower = query.strip().lower()
    results: List[Dict[str, str]] = []

    lib_names = [library_name] if library_name else [
        e.nickname for e in disc.list_symbol_libraries()
    ]

    for lib in lib_names:
        path_str = disc.resolve_symbol_library(lib)
        if not path_str:
            continue
        path = Path(path_str)
        if not path.exists():
            continue
        try:
            sym_lib = SymbolLibrary.load(path)
        except Exception:
            continue

        for sym in sym_lib.symbols:
            if _matches(sym.name, query_lower) or _prop_matches(sym.properties, query_lower):
                entry: Dict[str, str] = {
                    "library": lib,
                    "name": sym.name,
                    "description": _prop_val(sym.properties, "Description"),
                    "value": _prop_val(sym.properties, "Value"),
                    "footprint": _prop_val(sym.properties, "Footprint"),
                }
                results.append(entry)
                if len(results) >= max_results:
                    return results

    return results


def _normalize_for_search(text: str) -> str:
    """Lowercase and collapse common separators to spaces for matching."""
    return re.sub(r"[-_.,/\\:]+", " ", text.lower())


def _tokenize(text: str) -> List[str]:
    """Split *text* into non-empty lowercase tokens (separator-agnostic)."""
    return [t for t in re.split(r"[-_.,/\\:\s]+", text.lower()) if t]


def _find_primary_token(query_tokens: List[str]) -> str:
    """Return the most specific (longest) query token as the primary filter.

    Every result *must* contain this token somewhere in its name or library
    to be included at all.  This prevents diluted results where only a
    secondary token (like a library prefix) matches.
    """
    if not query_tokens:
        return ""
    return max(query_tokens, key=len)


def _token_in_any(token: str, targets: List[str]) -> bool:
    """Check if *token* is a substring of any string in *targets*."""
    return any(token in t for t in targets)


def _score_footprint(
    name_norm: str,
    name_tokens: List[str],
    lib_norm: str,
    lib_tokens: List[str],
    query_norm: str,
    query_tokens: List[str],
    primary_token: str,
) -> int:
    """Score how well a footprint matches the query.

    Higher is better.  Returns 0 for no match.

    **Hard filter**: the *primary_token* (longest query token) MUST appear
    somewhere in the footprint name or library name.  If it doesn't, the
    result is excluded (score 0).

    Scoring tiers:
      100  — exact normalised name match
       80  — full query is a substring of the normalised name
       60  — all query tokens found in the name
       50  — all query tokens found across name + library combined
       30  — primary token in name, additional tokens partially match
       20  — primary token only in name (no other tokens match)

    Bonuses (cumulative):
       +8  — primary token found in the *name* (not just library)
       +4  — additional (non-primary) token found in *library*
       +2  — additional (non-primary) token found in *name*
    """
    total = len(query_tokens)
    if total == 0:
        return 0

    # --- Hard filter: primary token must be present ---
    primary_in_name = primary_token in name_norm
    primary_in_lib = primary_token in lib_norm
    if not primary_in_name and not primary_in_lib:
        return 0

    # --- Exact / substring match on full query ---
    if query_norm == name_norm:
        return 100
    if query_norm in name_norm:
        return 80

    # --- Token-based matching ---
    name_matched = sum(1 for qt in query_tokens if _token_in_any(qt, name_tokens))
    combined_targets = name_tokens + lib_tokens
    combined_matched = sum(1 for qt in query_tokens if _token_in_any(qt, combined_targets))

    # All tokens in name
    if name_matched == total:
        return 60 + (8 if primary_in_name else 0)

    # All tokens across name + library
    if combined_matched == total:
        score = 50
        if primary_in_name:
            score += 8  # primary in name is better than only in lib
        return score

    # Partial matches — primary is guaranteed present so base score is > 0
    score = 0
    if combined_matched > 1:
        score = 30
    elif primary_in_name:
        score = 20
    else:
        score = 10  # primary only in library, no other tokens match

    # Bonuses
    if primary_in_name:
        score += 8
    non_primary = [qt for qt in query_tokens if qt != primary_token]
    for qt in non_primary:
        if _token_in_any(qt, lib_tokens):
            score += 4
        if _token_in_any(qt, name_tokens):
            score += 2

    return score


def search_footprints(
    query: str,
    library_name: Optional[str] = None,
    project_dir: Optional[str | Path] = None,
    max_results: int = 50,
) -> List[Dict[str, str]]:
    """Search footprint libraries with thorough tokenised matching.

    The search proceeds in two phases:

    1. **Fast filename pass** — iterate ``.kicad_mod`` filenames (no I/O)
       and score each against the tokenised query.  This covers the vast
       majority of useful matches because KiCad footprint names encode
       package, pin-count, pitch, and dimensions.

    2. **Deep metadata pass** — if the fast pass produced fewer than
       *max_results* hits, re-scan unmatched footprints by loading their
       description, tags, and properties from the file.  This catches
       footprints where the relevant information is only in metadata.

    Tokens are matched separator-agnostically: ``"SOIC 8"`` matches
    ``"SOIC-8_3.9x4.9mm_P1.27mm"`` because both normalise to tokens
    ``["soic", "8", ...]``.
    """
    disc = LibraryDiscovery(project_dir=str(project_dir) if project_dir else None)
    query_norm = _normalize_for_search(query.strip())
    query_tokens = _tokenize(query.strip())

    if not query_tokens:
        return []

    primary_token = _find_primary_token(query_tokens)

    lib_names = [library_name] if library_name else [
        e.nickname for e in disc.list_footprint_libraries()
    ]

    # Phase 1 — fast filename-only scan
    # Each candidate: (score, lib, name, path)
    scored: List[tuple] = []
    # Track unmatched files for phase 2: (lib, name, path)
    unmatched: List[tuple] = []

    for lib in lib_names:
        path_str = disc.resolve_footprint_library(lib)
        if not path_str:
            continue
        lib_dir = Path(path_str)
        if not lib_dir.is_dir():
            continue
        lib_norm = _normalize_for_search(lib)
        lib_tokens = _tokenize(lib)
        for mod_file in lib_dir.glob("*.kicad_mod"):
            name = mod_file.stem
            name_norm = _normalize_for_search(name)
            name_tokens = _tokenize(name)
            score = _score_footprint(
                name_norm, name_tokens, lib_norm, lib_tokens,
                query_norm, query_tokens, primary_token,
            )
            if score > 0:
                scored.append((score, lib, name, str(mod_file)))
            else:
                unmatched.append((lib, name, mod_file))

    # Phase 2 — deep metadata search for remaining candidates
    # Only inspect footprints whose library at least contains the primary
    # token, or where we haven't found enough results yet.
    if len(scored) < max_results and unmatched:
        for lib, name, mod_file in unmatched:
            # Pre-filter: primary token must appear *somewhere* (name,
            # lib, or metadata).  We already know it's not in name/lib
            # (phase 1 returned 0), so we must check the file contents.
            try:
                fp = Footprint.load(mod_file)
            except Exception:
                continue
            # Build a combined searchable string from metadata
            meta_parts = [
                getattr(fp, "description", "") or "",
                getattr(fp, "tags", "") or "",
            ]
            if hasattr(fp, "properties"):
                for prop in fp.properties:
                    meta_parts.append(getattr(prop, "key", "") or "")
                    meta_parts.append(getattr(prop, "value", "") or "")
            meta_text = " ".join(meta_parts)
            meta_norm = _normalize_for_search(meta_text)
            meta_tokens = _tokenize(meta_text)

            # Hard filter: primary token must be present in metadata
            if not _token_in_any(primary_token, meta_tokens) and primary_token not in meta_norm:
                continue

            # Check if full query appears in metadata
            if query_norm in meta_norm:
                scored.append((35, lib, name, str(mod_file)))
                continue
            # Token matching against metadata + name combined
            all_tokens = _tokenize(name) + meta_tokens
            matched = sum(
                1 for qt in query_tokens if _token_in_any(qt, all_tokens)
            )
            if matched == len(query_tokens):
                scored.append((30, lib, name, str(mod_file)))
            elif matched >= 1:
                # Primary is guaranteed present; only include if useful
                scored.append((15, lib, name, str(mod_file)))

            if len(scored) >= max_results * 4:
                # Enough candidates for ranking
                break

    # Sort by score descending, then alphabetically
    scored.sort(key=lambda x: (-x[0], x[1], x[2]))

    results: List[Dict[str, str]] = []
    for _score, lib, name, path in scored[:max_results]:
        results.append({"library": lib, "name": name, "path": path})

    return results


# ---------------------------------------------------------------------------
# Import helpers
# ---------------------------------------------------------------------------


def import_from_symbol_lib(
    symbol_name: str,
    library_name: str,
    project_dir: Optional[str | Path] = None,
) -> ImportResult:
    """Clone a symbol from an existing library into an ImportedComponent.

    The symbol S-expression is preserved verbatim so it can be written to
    a target library by :mod:`library_writer`.

    Parameters
    ----------
    symbol_name:
        KiCad symbol name within the library (e.g. ``"R"``).
    library_name:
        Library nickname as registered in the library table.
    project_dir:
        Used to locate project-local library tables.

    Returns
    -------
    ImportResult
        On success ``result.component.symbol_sexpr`` contains the raw symbol
        S-expression block extracted from the source library.
    """
    disc = LibraryDiscovery(project_dir=str(project_dir) if project_dir else None)
    path_str = disc.resolve_symbol_library(library_name)
    if not path_str:
        return ImportResult(
            success=False,
            error=f"Symbol library '{library_name}' not found",
        )

    path = Path(path_str)
    if not path.exists():
        return ImportResult(
            success=False,
            error=f"Symbol library file not found: {path}",
        )

    try:
        sym_lib = SymbolLibrary.load(path)
    except Exception as exc:
        return ImportResult(success=False, error=f"Failed to load library: {exc}")

    sym = sym_lib.find_by_name(symbol_name)
    if sym is None:
        return ImportResult(
            success=False,
            error=f"Symbol '{symbol_name}' not found in library '{library_name}'",
        )

    # Extract properties for field normalisation
    raw_fields = {
        p.key: p.value
        for p in sym.properties
    }
    fields = normalize_fields(raw_fields)
    if not fields.mpn:
        fields.mpn = symbol_name
    if not fields.value:
        fields.value = symbol_name

    # Serialise just the symbol block for later writing
    from ..kicad_parser.sexpr import serialize
    sym_text = serialize(sym.to_tree())

    component = ImportedComponent(
        name=fields.mpn or symbol_name,
        fields=fields,
        symbol_sexpr=sym_text,
        import_method=ImportMethod.KICAD_LIB,
        source_info=f"{library_name}:{symbol_name}",
    )
    component.symbol_path = path

    return ImportResult(success=True, component=component)


def import_from_footprint_lib(
    footprint_name: str,
    library_name: str,
    project_dir: Optional[str | Path] = None,
) -> ImportResult:
    """Clone a footprint from an existing library into an ImportedComponent."""
    disc = LibraryDiscovery(project_dir=str(project_dir) if project_dir else None)
    path_str = disc.resolve_footprint_library(library_name)
    if not path_str:
        return ImportResult(
            success=False,
            error=f"Footprint library '{library_name}' not found",
        )

    lib_dir = Path(path_str)
    mod_file = lib_dir / f"{footprint_name}.kicad_mod"
    if not mod_file.exists():
        return ImportResult(
            success=False,
            error=f"Footprint '{footprint_name}' not found in '{library_name}'",
        )

    try:
        fp = Footprint.load(mod_file)
    except Exception as exc:
        return ImportResult(success=False, error=f"Failed to load footprint: {exc}")

    fields = normalize_fields({"Value": footprint_name})

    component = ImportedComponent(
        name=footprint_name,
        fields=fields,
        footprint_sexpr=mod_file.read_text(encoding="utf-8"),
        import_method=ImportMethod.KICAD_LIB,
        source_info=f"{library_name}:{footprint_name}",
    )
    component.footprint_path = mod_file

    return ImportResult(success=True, component=component)


# ---------------------------------------------------------------------------
# Add variant (clone template symbol with updated fields)
# ---------------------------------------------------------------------------


def _rename_symbol_tree(tree: List[SExpr], old_name: str, new_name: str) -> None:
    """Rename the symbol and all its sub-symbol names in a raw S-expression tree.

    KiCad symbol trees have structure like::

        (symbol "R_100k_0603_0.1W_1%"
            ...
            (symbol "R_100k_0603_0.1W_1%_0_1" ...)
            (symbol "R_100k_0603_0.1W_1%_1_1" ...)
        )

    This function renames both the top-level and sub-symbol names.
    """
    if not isinstance(tree, list) or len(tree) < 2:
        return
    # Rename the top-level symbol name
    if tree[0] == "symbol" and isinstance(tree[1], (str, QStr)):
        name = str(tree[1])
        if name == old_name:
            tree[1] = QStr(new_name)
        elif name.startswith(old_name + "_"):
            suffix = name[len(old_name):]
            tree[1] = QStr(new_name + suffix)
    # Recurse into children
    for child in tree:
        if isinstance(child, list):
            _rename_symbol_tree(child, old_name, new_name)


def _update_property_in_tree(tree: List[SExpr], key: str, value: str) -> bool:
    """Find and update a property value in a raw S-expression tree.

    Returns True if the property was found and updated.
    """
    for item in tree:
        if isinstance(item, list) and item and item[0] == "property":
            if len(item) > 2 and str(item[1]) == key:
                item[2] = QStr(value)
                return True
    return False


def _add_property_to_tree(
    tree: List[SExpr],
    key: str,
    value: str,
    hidden: bool = True,
) -> None:
    """Append a new property node to a symbol S-expression tree."""
    prop_node: List[SExpr] = [
        "property",
        QStr(key),
        QStr(value),
        ["at", 0, 0, 0],
    ]
    if hidden:
        prop_node.append(["effects", ["font", ["size", 1.27, 1.27]], ["hide", "yes"]])
    else:
        prop_node.append(["effects", ["font", ["size", 1.27, 1.27]]])
    tree.append(prop_node)


def _set_property_in_tree(
    tree: List[SExpr],
    key: str,
    value: str,
    hidden: bool = True,
) -> None:
    """Update an existing property or add it if missing."""
    if not _update_property_in_tree(tree, key, value):
        _add_property_to_tree(tree, key, value, hidden=hidden)


def add_variant(
    template_library: str,
    template_symbol: str,
    new_symbol_name: str,
    fields: Dict[str, str],
    project_dir: Optional[str | Path] = None,
    target_library: Optional[str] = None,
) -> Dict[str, Any]:
    """Clone a template symbol and create a new variant with updated fields.

    This is designed for passive component libraries (e.g. pcb-club-res,
    pcb-club-cap) where all symbols share the same graphics/pins and only
    differ in their property values.

    Parameters
    ----------
    template_library:
        Library nickname containing the template symbol.
    template_symbol:
        Symbol name to clone as a template (e.g. ``"R_10k_0603_0.1W_1%"``).
    new_symbol_name:
        Name for the new symbol (e.g. ``"R_4.7k_0603_0.1W_1%"``).
    fields:
        Dict of property values to set.  Recognised keys: ``Value``,
        ``Footprint``, ``Datasheet``, ``Description``, ``MF``, ``MPN``,
        ``DKPN``, ``MSPN``, ``LCSC``, ``Package``, ``Status``.
    project_dir:
        Used to locate project-local library tables.
    target_library:
        Library nickname to write the new symbol to.  If *None*, writes
        back to *template_library*.

    Returns
    -------
    dict
        ``success``, ``name`` (final symbol name), ``library`` nickname,
        ``library_path``, ``error``.
    """
    disc = LibraryDiscovery(project_dir=str(project_dir) if project_dir else None)

    # Resolve template library
    src_path_str = disc.resolve_symbol_library(template_library)
    if not src_path_str:
        return {"success": False, "error": f"Library '{template_library}' not found"}

    src_path = Path(src_path_str)
    if not src_path.exists():
        return {"success": False, "error": f"Library file not found: {src_path}"}

    try:
        lib = SymbolLibrary.load(src_path)
    except Exception as exc:
        return {"success": False, "error": f"Failed to load library: {exc}"}

    # Find the template symbol
    template = lib.find_by_name(template_symbol)
    if template is None:
        return {
            "success": False,
            "error": f"Symbol '{template_symbol}' not found in '{template_library}'",
        }

    # Determine target library
    target_lib = target_library or template_library
    if target_lib != template_library:
        tgt_path_str = disc.resolve_symbol_library(target_lib)
        if not tgt_path_str:
            return {"success": False, "error": f"Target library '{target_lib}' not found"}
        tgt_path = Path(tgt_path_str)
        if not tgt_path.exists():
            return {"success": False, "error": f"Target library file not found: {tgt_path}"}
        try:
            tgt_lib_obj = SymbolLibrary.load(tgt_path)
        except Exception as exc:
            return {"success": False, "error": f"Failed to load target library: {exc}"}
    else:
        tgt_lib_obj = lib
        tgt_path = src_path

    # Sanitise the new name
    safe_name = re.sub(r"[^A-Za-z0-9_\-./%()+]", "_", new_symbol_name.strip())
    if not safe_name:
        safe_name = "Component"

    # Check for name conflict
    if tgt_lib_obj.find_by_name(safe_name) is not None:
        return {
            "success": False,
            "error": f"Symbol '{safe_name}' already exists in '{target_lib}'",
        }

    # Deep-clone the template symbol's raw tree
    if template.raw_tree is None:
        # If no raw_tree, serialise and re-parse to get one
        from ..kicad_parser.sexpr import serialize, parse
        template.raw_tree = parse(serialize(template.to_tree()))

    new_tree = copy.deepcopy(template.raw_tree)

    # Rename the symbol and all sub-symbols
    old_name = template.name
    _rename_symbol_tree(new_tree, old_name, safe_name)

    # Update properties in the raw tree
    # Standard KiCad fields — always visible fields
    if "Value" in fields:
        _set_property_in_tree(new_tree, "Value", fields["Value"], hidden=False)

    # Hidden metadata fields
    hidden_keys = [
        "Footprint", "Datasheet", "Description", "MF", "MPN",
        "DKPN", "MSPN", "LCSC", "Package",
    ]
    for key in hidden_keys:
        if key in fields and fields[key]:
            _set_property_in_tree(new_tree, key, fields[key], hidden=True)

    # Status field (visible)
    if "Status" in fields:
        _set_property_in_tree(new_tree, "Status", fields["Status"], hidden=False)
    else:
        _set_property_in_tree(new_tree, "Status", "NEW", hidden=False)

    # Any extra fields
    known_keys = {"Value", "Footprint", "Datasheet", "Description", "MF", "MPN",
                  "DKPN", "MSPN", "LCSC", "Package", "Status", "Reference"}
    for key, value in fields.items():
        if key not in known_keys and value:
            _set_property_in_tree(new_tree, key, value, hidden=True)

    # Create a new SymbolDef from the cloned tree
    new_sym = SymbolDef.from_tree(new_tree)
    new_sym.raw_tree = new_tree  # Preserve the raw tree for round-trip fidelity

    # Add to the target library
    tgt_lib_obj.add_symbol(new_sym)

    # Save with backup
    import shutil
    import tempfile
    if tgt_path.exists():
        shutil.copy2(tgt_path, str(tgt_path) + ".bak")
    tmp_fd, tmp_path = tempfile.mkstemp(dir=tgt_path.parent, suffix=".tmp")
    try:
        os.close(tmp_fd)
        tgt_lib_obj.save(tmp_path)
        os.replace(tmp_path, tgt_path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise

    logger.info("Added variant '%s' to library '%s' (%s)", safe_name, target_lib, tgt_path)
    return {
        "success": True,
        "name": safe_name,
        "library": target_lib,
        "library_path": str(tgt_path),
    }


# ---------------------------------------------------------------------------
# Private utilities
# ---------------------------------------------------------------------------


def _matches(text: str, query: str) -> bool:
    return query in text.lower()


def _prop_matches(props, query: str) -> bool:
    return any(query in (p.value or "").lower() for p in props)


def _prop_val(props, name: str) -> str:
    for p in props:
        if p.key == name:
            return p.value or ""
    return ""
