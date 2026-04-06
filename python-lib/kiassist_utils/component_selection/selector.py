"""Component selector: filtering, scoring, ranking, and shortlisting.

This module implements the **code layer** of the component selection pipeline.
It is responsible for:

1. **Candidate discovery** — loading components from local KiCad symbol
   libraries via :func:`~.local_source.load_candidates_from_library`.
2. **Type-based pre-filtering** — discarding symbols that clearly do not match
   the requested component type before the scoring stage.
3. **Constraint-based scoring** — evaluating how well each candidate satisfies
   the electrical constraints specified in the :class:`~.models.ComponentSpec`.
4. **Ranking** — sorting candidates by their composite score.
5. **Shortlisting** — returning only the top *N* candidates to keep the
   response compact and token-efficient for the model layer.

The **model layer** (LLM) remains responsible for higher-level reasoning:
interpreting ambiguous requirements, explaining tradeoffs, and making final
recommendations from the shortlist.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from .local_source import find_library_files, load_candidates_from_library
from .models import ComponentCandidate, ComponentSpec, SelectionResult

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internal scoring helpers
# ---------------------------------------------------------------------------


def _normalise_type(type_str: str) -> str:
    """Normalise a component type string for comparison."""
    return type_str.lower().strip().replace(" ", "_").replace("-", "_")


def _text_similarity(query: str, target: str) -> float:
    """Compute a simple token-overlap similarity score in ``[0.0, 1.0]``.

    The score equals the fraction of query tokens that also appear in *target*.
    Both strings are lower-cased and split on non-word characters before
    comparison.

    Args:
        query:  The search/query string.
        target: The candidate string to compare against.

    Returns:
        Similarity score in ``[0.0, 1.0]``.  Returns ``0.0`` for empty inputs.
    """
    if not query or not target:
        return 0.0
    q_tokens = set(re.split(r"\W+", query.lower()))
    t_tokens = set(re.split(r"\W+", target.lower()))
    q_tokens.discard("")
    t_tokens.discard("")
    if not q_tokens:
        return 0.0
    return len(q_tokens & t_tokens) / len(q_tokens)


def _score_candidate(candidate: ComponentCandidate, spec: ComponentSpec) -> float:
    """Compute a composite match score for *candidate* against *spec*.

    Score components (weights sum to 1.0):

    * **0.40** — Component type match.
    * **0.30** — Description text similarity.
    * **0.20** — Constraint satisfaction ratio.
    * **0.10** — Footprint preference match.

    Args:
        candidate: The normalised component candidate to evaluate.
        spec:      The component specification (requirements).

    Returns:
        Score in ``[0.0, 1.0]``.
    """
    score = 0.0

    # 1. Type match (0–0.40) ------------------------------------------------
    norm_spec = _normalise_type(spec.component_type)
    norm_cand = _normalise_type(candidate.component_type)
    if norm_spec == norm_cand:
        score += 0.40
    elif norm_spec in norm_cand or norm_cand in norm_spec:
        score += 0.20

    # 2. Description similarity (0–0.30) ------------------------------------
    query_text = f"{spec.component_type} {spec.description}"
    score += _text_similarity(query_text, candidate.description) * 0.30

    # 3. Constraint satisfaction (0–0.20) -----------------------------------
    if spec.constraints:
        satisfied = 0
        for key, required in spec.constraints.items():
            # Case-insensitive property lookup.
            cand_val: Any = None
            for ckey, cval in candidate.specifications.items():
                if ckey.lower() == key.lower():
                    cand_val = cval
                    break

            if cand_val is None:
                continue

            if isinstance(required, dict):
                # Range constraint: {"min": x, "max": y}
                mn: Optional[float] = required.get("min")
                mx: Optional[float] = required.get("max")
                if isinstance(cand_val, (int, float)):
                    if (mn is None or cand_val >= mn) and (mx is None or cand_val <= mx):
                        satisfied += 1
            elif isinstance(required, (int, float)) and isinstance(cand_val, (int, float)):
                # Exact numeric: within 10 % tolerance.
                tolerance = abs(float(required)) * 0.10 + 1e-9
                if abs(cand_val - float(required)) <= tolerance:
                    satisfied += 1
            else:
                # String: substring match.
                if str(required).lower() in str(cand_val).lower():
                    satisfied += 1

        score += (satisfied / len(spec.constraints)) * 0.20

    # 4. Footprint preference (0–0.10) --------------------------------------
    if spec.preferred_footprints and candidate.footprint:
        fp_lower = candidate.footprint.lower()
        if any(pref.lower() in fp_lower for pref in spec.preferred_footprints):
            score += 0.10

    return min(score, 1.0)


# ---------------------------------------------------------------------------
# ComponentSelector
# ---------------------------------------------------------------------------


class ComponentSelector:
    """Select and rank component candidates from local KiCad symbol libraries.

    This class forms the **code layer** of the component selection pipeline:
    it handles data loading, filtering, normalisation, and ranking.  The AI
    model layer is responsible for interpreting higher-level requirements and
    reasoning about the returned shortlist.

    Example::

        selector = ComponentSelector(library_paths=["path/to/libs"])
        spec = ComponentSpec(
            component_type="LDO",
            constraints={"voltage": 3.3},
            max_candidates=3,
        )
        result = selector.select(spec)
        for c in result.candidates:
            print(c.symbol, c.score)

    Args:
        library_paths: Paths to search for ``.kicad_sym`` files.  Each entry
                       may be a file path or a directory that is searched
                       recursively.  Accepts both :class:`str` and
                       :class:`~pathlib.Path` objects.
    """

    def __init__(
        self,
        library_paths: Optional[List[Union[str, Path]]] = None,
    ) -> None:
        self._library_paths: List[Path] = (
            [Path(p) for p in library_paths] if library_paths else []
        )

    def select(self, spec: ComponentSpec) -> SelectionResult:
        """Run the full component selection pipeline for *spec*.

        Pipeline stages:

        1. **Candidate discovery**: Load symbols from all configured library
           files, applying an inexpensive type-keyword pre-filter.
        2. **Scoring**: Compute a composite match score for each candidate.
        3. **Filtering**: Discard candidates with a zero score (no type match).
        4. **Ranking**: Sort by score descending.
        5. **Shortlisting**: Return at most ``spec.max_candidates`` results.

        Args:
            spec: The component specification to search for.

        Returns:
            :class:`~.models.SelectionResult` containing the shortlisted
            candidates and pipeline statistics.
        """
        lib_files = find_library_files(self._library_paths)

        all_candidates: List[ComponentCandidate] = []
        for lib_path in lib_files:
            all_candidates.extend(
                load_candidates_from_library(lib_path, spec.component_type)
            )

        total_found = len(all_candidates)

        # Score every candidate.
        for candidate in all_candidates:
            candidate.score = _score_candidate(candidate, spec)

        # Filter out zero-score entries (no type overlap whatsoever).
        scored = [c for c in all_candidates if c.score > 0.0]
        filtered_count = len(scored)

        # Rank best-first.
        scored.sort(key=lambda c: c.score, reverse=True)

        # Shortlist.
        shortlisted = scored[: spec.max_candidates]

        # Source breakdown statistics.
        source_stats: Dict[str, int] = {}
        for c in shortlisted:
            source_stats[c.source] = source_stats.get(c.source, 0) + 1

        return SelectionResult(
            query=spec,
            candidates=shortlisted,
            total_found=total_found,
            filtered_count=filtered_count,
            source_stats=source_stats,
        )
