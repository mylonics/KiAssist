"""Data models for component search and selection."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class ComponentSpec:
    """Specification for a component search query.

    Attributes:
        query:          Free-text search string (case-insensitive).
        library_filter: Optional library nickname to restrict the search to a
                        single library.
        max_results:    Maximum number of candidates to return across all
                        searched libraries.
    """

    query: str
    library_filter: Optional[str] = None
    max_results: int = 50


@dataclass
class ComponentCandidate:
    """A component candidate returned by a search.

    Attributes:
        library_name: Nickname of the source library (e.g. ``"Device"``).
        symbol_name:  Symbol name inside the library (e.g. ``"R"``).
        lib_id:       Full KiCad library identifier ``"<library>:<symbol>"``
                      (e.g. ``"Device:R"``).
        description:  Human-readable description from ``ki_description`` property.
        keywords:     Search keyword list from ``ki_keywords`` property.
        properties:   All symbol properties as a ``{key: value}`` dict.
        pin_count:    Total number of pins on the symbol.
    """

    library_name: str
    symbol_name: str
    lib_id: str
    description: str
    keywords: List[str]
    properties: Dict[str, str]
    pin_count: int

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-serialisable representation."""
        return {
            "library_name": self.library_name,
            "symbol_name": self.symbol_name,
            "lib_id": self.lib_id,
            "description": self.description,
            "keywords": self.keywords,
            "properties": self.properties,
            "pin_count": self.pin_count,
        }


@dataclass
class SelectionResult:
    """Result of a component search operation.

    Attributes:
        candidates:      Matching :class:`ComponentCandidate` objects.
        library_names:   Names of libraries that were searched.
        total_searched:  Total number of symbols examined (before filtering).
        query:           The original search query string.
    """

    candidates: List[ComponentCandidate]
    library_names: List[str]
    total_searched: int
    query: str

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-serialisable representation."""
        return {
            "candidates": [c.to_dict() for c in self.candidates],
            "library_names": self.library_names,
            "total_searched": self.total_searched,
            "query": self.query,
        }
