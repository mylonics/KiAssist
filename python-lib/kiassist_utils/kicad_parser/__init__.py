"""KiCad S-Expression Parser Package.

Custom parser for KiCad file formats (.kicad_sch, .kicad_sym, .kicad_mod, .kicad_pcb).
Provides full control over parsing, modification, and serialization of KiCad files
with round-trip fidelity.
"""

from .sexpr import parse, serialize, parse_file, serialize_to_file
from .models import Position, Stroke, Effects, Property, Pts, Color
from .schematic import Schematic, SchematicSymbol, Wire, Bus, Junction, NoConnect
from .schematic import BusEntry, Label, GlobalLabel, HierarchicalLabel, Sheet
from .symbol_lib import SymbolLibrary, SymbolDef, SymbolUnit, Pin
from .footprint import Footprint, Pad, FootprintGraphic
from .pcb import PCBBoard
from .library import LibraryDiscovery

__all__ = [
    # S-expression parser
    "parse",
    "serialize",
    "parse_file",
    "serialize_to_file",
    # Base models
    "Position",
    "Stroke",
    "Effects",
    "Property",
    "Pts",
    "Color",
    # Schematic
    "Schematic",
    "SchematicSymbol",
    "Wire",
    "Bus",
    "Junction",
    "NoConnect",
    "BusEntry",
    "Label",
    "GlobalLabel",
    "HierarchicalLabel",
    "Sheet",
    # Symbol library
    "SymbolLibrary",
    "SymbolDef",
    "SymbolUnit",
    "Pin",
    # Footprint
    "Footprint",
    "Pad",
    "FootprintGraphic",
    # PCB
    "PCBBoard",
    # Library discovery
    "LibraryDiscovery",
]
