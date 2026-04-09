"""KiCad file parser package.

Provides a custom S-expression parser and typed data models for all four
KiCad file types: .kicad_sch, .kicad_sym, .kicad_mod, and .kicad_pcb.

Modules:
    sexpr      -- Generic S-expression tokenizer, parser, and serializer.
    models     -- Shared dataclasses (Position, Stroke, Effects, etc.).
    schematic  -- Schematic (.kicad_sch) file model.
    symbol_lib -- Symbol library (.kicad_sym) file model.
    footprint  -- Footprint (.kicad_mod) file model.
    pcb        -- PCB (.kicad_pcb) read-only stub model.
    library    -- KiCad library discovery (sym-lib-table / fp-lib-table).
"""

from .sexpr import parse, serialize, SExpr
from .models import Position, Stroke, Effects, Property, KiUUID, Pts
from .schematic import TitleBlock
from .analyzer import (
    LibraryAnalyzer, AnalysisReport, Issue, Severity, IssueCategory,
    StructuralValidationError, _RawTextAnalysis,
)

__all__ = [
    "parse",
    "serialize",
    "SExpr",
    "Position",
    "Stroke",
    "Effects",
    "Property",
    "KiUUID",
    "Pts",
    "TitleBlock",
    "LibraryAnalyzer",
    "AnalysisReport",
    "Issue",
    "Severity",
    "IssueCategory",
    "StructuralValidationError",
    "_RawTextAnalysis",
]
