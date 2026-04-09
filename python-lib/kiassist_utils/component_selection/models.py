"""Data models for the component selection pipeline.

Provides :class:`ComponentSpec` (query input), :class:`ComponentCandidate`
(normalized result), and :class:`SelectionResult` (full pipeline output).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class ComponentSpec:
    """Structured specification mapping project requirements to component search criteria.

    Attributes:
        component_type:       Component category (e.g. ``'ADC'``, ``'LDO'``,
                              ``'resistor'``).
        description:          Human-readable description of the component's role.
        constraints:          Key/value electrical constraints for filtering
                              (e.g. ``{"voltage": 3.3, "current": 0.1}``).
                              Numeric values are compared within a 10 % tolerance.
                              Range constraints can be expressed as
                              ``{"voltage": {"min": 2.7, "max": 5.5}}``.
        preferred_footprints: Footprint substrings that boost the ranking score
                              (e.g. ``['0402', 'SOT-23']``).
        max_candidates:       Maximum number of shortlisted results to return.
    """

    component_type: str
    description: str = ""
    constraints: Dict[str, Any] = field(default_factory=dict)
    preferred_footprints: List[str] = field(default_factory=list)
    max_candidates: int = 5

    def to_dict(self) -> Dict[str, Any]:
        """Serialise to a plain dict."""
        return {
            "component_type": self.component_type,
            "description": self.description,
            "constraints": self.constraints,
            "preferred_footprints": self.preferred_footprints,
            "max_candidates": self.max_candidates,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ComponentSpec":
        """Deserialise from a plain dict."""
        return cls(
            component_type=data.get("component_type", ""),
            description=data.get("description", ""),
            constraints=data.get("constraints", {}),
            preferred_footprints=data.get("preferred_footprints", []),
            max_candidates=int(data.get("max_candidates", 5)),
        )


@dataclass
class ComponentCandidate:
    """Normalized component candidate from any data source.

    Provides a consistent structure that enables comparison and ranking across
    sources, satisfying the data-normalization requirement of the pipeline.

    Attributes:
        symbol:        KiCad symbol identifier in ``'Library:SymbolName'``
                       notation.
        description:   Human-readable component description.
        component_type: Inferred or explicit component type category.
        footprint:     Default footprint in ``'Library:Footprint'`` notation.
        datasheet_url: URL to the component datasheet (empty string if absent).
        specifications: Normalized electrical specifications extracted from
                        symbol properties.
        properties:    Raw key/value properties from the source.
        source:        Data source identifier (e.g. ``'kicad_lib'``).
        score:         Match quality score in the range ``[0.0, 1.0]``.
                       Higher is better.
    """

    symbol: str
    description: str
    component_type: str
    footprint: str = ""
    datasheet_url: str = ""
    specifications: Dict[str, Any] = field(default_factory=dict)
    properties: Dict[str, str] = field(default_factory=dict)
    source: str = "kicad_lib"
    score: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        """Serialise to a full detail dict."""
        return {
            "symbol": self.symbol,
            "description": self.description,
            "component_type": self.component_type,
            "footprint": self.footprint,
            "datasheet_url": self.datasheet_url,
            "specifications": self.specifications,
            "properties": self.properties,
            "source": self.source,
            "score": round(self.score, 4),
        }

    def to_summary_dict(self) -> Dict[str, Any]:
        """Return a token-efficient summary suitable for LLM consumption.

        Only includes fields that are directly useful for model reasoning,
        omitting bulky ``properties`` and low-signal fields to minimise
        token usage.
        """
        summary: Dict[str, Any] = {
            "symbol": self.symbol,
            "description": self.description,
            "footprint": self.footprint,
            "score": round(self.score, 2),
        }
        if self.datasheet_url:
            summary["datasheet"] = self.datasheet_url
        if self.specifications:
            summary["specs"] = self.specifications
        return summary


@dataclass
class SelectionResult:
    """Result of a complete component selection pipeline run.

    Attributes:
        query:          The original :class:`ComponentSpec` query.
        candidates:     Shortlisted :class:`ComponentCandidate` objects sorted
                        by score descending (best first).
        total_found:    Total number of components scanned before filtering.
        filtered_count: Number of components that passed type/constraint filters.
        source_stats:   Breakdown of shortlisted candidates by source.
    """

    query: ComponentSpec
    candidates: List[ComponentCandidate] = field(default_factory=list)
    total_found: int = 0
    filtered_count: int = 0
    source_stats: Dict[str, int] = field(default_factory=dict)

    def to_dict(self, summary: bool = True) -> Dict[str, Any]:
        """Serialise to a plain dict.

        Args:
            summary: When ``True`` (default) each candidate is serialised via
                     :meth:`ComponentCandidate.to_summary_dict` for token
                     efficiency.  Pass ``False`` to include full detail.
        """
        serialise = (
            ComponentCandidate.to_summary_dict
            if summary
            else ComponentCandidate.to_dict
        )
        return {
            "query": self.query.to_dict(),
            "candidates": [serialise(c) for c in self.candidates],
            "total_found": self.total_found,
            "filtered_count": self.filtered_count,
            "source_stats": self.source_stats,
        }
