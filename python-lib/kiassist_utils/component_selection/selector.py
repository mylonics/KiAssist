"""Component search and selection logic backed by KiCad symbol libraries."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import List, Optional

from ..kicad_parser.library import LibraryDiscovery
from ..kicad_parser.symbol_lib import SymbolDef, SymbolLibrary
from .models import ComponentCandidate, ComponentSpec, SelectionResult

logger = logging.getLogger(__name__)


class ComponentSelector:
    """Searches KiCad symbol libraries for components that match a query.

    Wraps :class:`~kiassist_utils.kicad_parser.library.LibraryDiscovery` and
    :class:`~kiassist_utils.kicad_parser.symbol_lib.SymbolLibrary` to provide
    a unified component-search interface.

    Args:
        project_dir: Optional path to a KiCad project directory.  When
                     provided, project-local library tables are searched first.
    """

    def __init__(self, project_dir: Optional[str | os.PathLike] = None) -> None:
        self._project_dir = Path(project_dir) if project_dir else None
        self._discovery = LibraryDiscovery(project_dir)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def list_libraries(self) -> List[str]:
        """Return the nicknames of all known symbol libraries.

        Returns:
            Sorted list of library nickname strings.
        """
        return sorted(e.nickname for e in self._discovery.list_symbol_libraries())

    def search(self, spec: ComponentSpec) -> SelectionResult:
        """Search across all (or a filtered subset of) symbol libraries.

        Iterates every resolved symbol library, loads it, and checks each
        symbol definition against *spec.query*.  The search is
        case-insensitive and matches any substring within the symbol name,
        description, keywords, or any property value.

        Args:
            spec: :class:`ComponentSpec` describing what to look for.

        Returns:
            :class:`SelectionResult` with up to *spec.max_results* candidates.
        """
        entries = self._discovery.list_symbol_libraries()
        if spec.library_filter:
            entries = [e for e in entries if e.nickname == spec.library_filter]

        candidates: List[ComponentCandidate] = []
        searched_names: List[str] = []
        total_searched = 0

        env = (
            {"KIPRJMOD": str(self._project_dir)} if self._project_dir else None
        )

        for entry in entries:
            lib_path = entry.resolved_path(env=env)
            if lib_path is None:
                continue
            searched_names.append(entry.nickname)
            try:
                lib = SymbolLibrary.load(str(lib_path))
            except Exception:  # noqa: BLE001
                logger.debug("Could not load library %s", lib_path)
                continue

            for sym in lib.symbols:
                total_searched += 1
                candidate = self._make_candidate(entry.nickname, sym)
                if candidate is None:
                    continue
                if self._matches(candidate, spec.query):
                    candidates.append(candidate)
                    if len(candidates) >= spec.max_results:
                        break

            if len(candidates) >= spec.max_results:
                break

        return SelectionResult(
            candidates=candidates,
            library_names=searched_names,
            total_searched=total_searched,
            query=spec.query,
        )

    def get_candidates(
        self,
        library_name: str,
        spec: ComponentSpec,
    ) -> List[ComponentCandidate]:
        """Search within a single named library and return full candidate details.

        Args:
            library_name: Nickname of the library to search.
            spec:         :class:`ComponentSpec` with the search query and
                          *max_results* limit.

        Returns:
            List of :class:`ComponentCandidate` objects (up to *spec.max_results*).
        """
        lib_path = self._discovery.resolve_symbol_library(library_name)
        if lib_path is None:
            return []

        try:
            lib = SymbolLibrary.load(str(lib_path))
        except Exception:  # noqa: BLE001
            logger.debug("Could not load library %s", library_name)
            return []

        candidates: List[ComponentCandidate] = []
        for sym in lib.symbols:
            candidate = self._make_candidate(library_name, sym)
            if candidate is None:
                continue
            if self._matches(candidate, spec.query):
                candidates.append(candidate)
                if len(candidates) >= spec.max_results:
                    break
        return candidates

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _make_candidate(
        library_name: str, sym: SymbolDef
    ) -> Optional[ComponentCandidate]:
        """Build a :class:`ComponentCandidate` from a :class:`SymbolDef`.

        Returns ``None`` when the symbol has no name (malformed entry).
        """
        if not sym.name:
            return None

        props: dict = {p.key: p.value for p in sym.properties}
        description = props.get("ki_description", "")
        keywords_raw = props.get("ki_keywords", "")
        keywords = keywords_raw.split() if keywords_raw else []
        pin_count = len(sym.pins())

        return ComponentCandidate(
            library_name=library_name,
            symbol_name=sym.name,
            lib_id=f"{library_name}:{sym.name}",
            description=description,
            keywords=keywords,
            properties=props,
            pin_count=pin_count,
        )

    @staticmethod
    def _matches(candidate: ComponentCandidate, query: str) -> bool:
        """Return ``True`` if *candidate* matches *query* (case-insensitive)."""
        q = query.lower()
        searchable = (
            [candidate.symbol_name, candidate.description]
            + candidate.keywords
            + list(candidate.properties.values())
        )
        return any(q in field.lower() for field in searchable if field)
