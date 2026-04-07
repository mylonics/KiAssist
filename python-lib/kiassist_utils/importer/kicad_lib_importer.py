"""Import / reuse components from existing KiCad symbol and footprint libraries.

Supports two modes:
- *Search*: list symbols or footprints matching a query string.
- *Import*: clone an existing symbol (and optionally its footprint) into a
  target library, with field normalisation.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Dict, List, Optional

from ..kicad_parser.symbol_lib import SymbolLibrary, SymbolDef
from ..kicad_parser.footprint import Footprint
from ..kicad_parser.library import LibraryDiscovery
from .models import ImportedComponent, ImportMethod, ImportResult
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
        e["nickname"] for e in disc.list_symbol_libraries()
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


def search_footprints(
    query: str,
    library_name: Optional[str] = None,
    project_dir: Optional[str | Path] = None,
    max_results: int = 50,
) -> List[Dict[str, str]]:
    """Search footprint libraries; similar to :func:`search_symbols`."""
    disc = LibraryDiscovery(project_dir=str(project_dir) if project_dir else None)
    query_lower = query.strip().lower()
    results: List[Dict[str, str]] = []

    lib_names = [library_name] if library_name else [
        e["nickname"] for e in disc.list_footprint_libraries()
    ]

    for lib in lib_names:
        path_str = disc.resolve_footprint_library(lib)
        if not path_str:
            continue
        lib_dir = Path(path_str)
        if not lib_dir.is_dir():
            continue
        for mod_file in sorted(lib_dir.glob("*.kicad_mod")):
            name = mod_file.stem
            if _matches(name, query_lower):
                results.append({"library": lib, "name": name, "path": str(mod_file)})
                if len(results) >= max_results:
                    return results

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
