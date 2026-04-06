"""Component selection pipeline for KiAssist.

Provides candidate discovery, filtering, ranking, and shortlisting of
electronic components based on structured project requirements.

Design separation of responsibilities:

* **Code layer** (this package): data retrieval from local KiCad symbol
  libraries, constraint-based filtering, score-based ranking, shortlisting.
* **Model layer** (LLM): interprets higher-level requirements, reasons about
  tradeoffs, and makes final recommendations from the returned shortlist.

Supported data sources
~~~~~~~~~~~~~~~~~~~~~~

* **Local KiCad symbol libraries** (``.kicad_sym`` files) — fast, deterministic,
  no network access required.

Example usage::

    from kiassist_utils.component_selection import ComponentSelector, ComponentSpec

    spec = ComponentSpec(
        component_type="LDO",
        description="3.3 V output for MCU supply",
        constraints={"voltage": 3.3},
        max_candidates=5,
    )
    selector = ComponentSelector(library_paths=["path/to/KiCad/libraries"])
    result = selector.select(spec)
    for candidate in result.candidates:
        print(candidate.symbol, candidate.score)
"""

from .models import ComponentCandidate, ComponentSpec, SelectionResult
from .selector import ComponentSelector

__all__ = [
    "ComponentCandidate",
    "ComponentSelector",
    "ComponentSpec",
    "SelectionResult",
]
