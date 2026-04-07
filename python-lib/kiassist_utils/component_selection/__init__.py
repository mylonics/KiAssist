"""Component search and selection for KiAssist.

Provides :class:`ComponentSpec`, :class:`ComponentCandidate`,
:class:`SelectionResult`, and :class:`ComponentSelector` for searching KiCad
symbol libraries and selecting components for use in a design.
"""

from .models import ComponentCandidate, ComponentSpec, SelectionResult
from .selector import ComponentSelector

__all__ = [
    "ComponentCandidate",
    "ComponentSelector",
    "ComponentSpec",
    "SelectionResult",
]
