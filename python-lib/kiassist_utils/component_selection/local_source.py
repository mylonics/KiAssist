"""KiCad local symbol library data source for component selection.

Loads :class:`~kiassist_utils.kicad_parser.symbol_lib.SymbolDef` entries from
``.kicad_sym`` files, infers component types, extracts normalised electrical
specifications, and produces :class:`~.models.ComponentCandidate` objects.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from ..kicad_parser.symbol_lib import SymbolDef, SymbolLibrary
from .models import ComponentCandidate

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Component type keyword mapping
# ---------------------------------------------------------------------------

#: Maps canonical component-type names to lists of lowercase keyword substrings
#: that appear in symbol names, descriptions, or library names.
_TYPE_KEYWORDS: Dict[str, List[str]] = {
    "ADC": ["adc", "analog-to-digital", "analog to digital", "a/d converter", "a-d converter"],
    "DAC": ["dac", "digital-to-analog", "digital to analog", "d/a converter", "d-a converter"],
    "LDO": ["ldo", "low-dropout", "low dropout", "linear regulator"],
    "voltage_regulator": [
        "regulator",
        "vreg",
        "voltage reg",
        "buck",
        "boost",
        "switcher",
        "smps",
        "switching regulator",
    ],
    "opamp": ["op-amp", "opamp", "operational amplifier", "op amp"],
    "comparator": ["comparator"],
    "sensor": ["sensor", "detector", "transducer", "accelerometer", "gyro", "imu", "temperature sensor", "humidity"],
    "microcontroller": ["mcu", "microcontroller", "micro controller", "processor", "microprocessor"],
    "transistor": ["transistor", "mosfet", "bjt", "jfet"],
    "diode": ["diode", "rectifier", "zener", "schottky", "tvs"],
    "resistor": ["resistor"],
    "capacitor": ["capacitor"],
    "inductor": ["inductor", "choke", "ferrite bead"],
    "crystal": ["crystal", "xtal", "oscillator", "resonator"],
    "memory": ["eeprom", "flash", "sram", "sdram", "dram", "memory", "rom"],
    "interface": ["uart", "spi", "i2c", "usb", "can transceiver", "ethernet", "rs-232", "rs-485", "lvds"],
    "logic": ["logic gate", "flip-flop", "flipflop", "latch", "multiplexer", "demultiplexer", "decoder", "encoder"],
    "amplifier": ["amplifier", "audio amp", "buffer amplifier"],
    "filter": ["rc filter", "lc filter", "active filter"],
    "fuse": ["fuse", "polyfuse", "resettable fuse"],
    "connector": ["connector", "header", "socket", "plug", "terminal"],
    "switch": ["switch", "relay", "pushbutton", "tactile"],
    "led": ["led", "light emitting"],
    "transformer": ["transformer"],
    "power_management": ["power management", "pmic", "power ic"],
}

# ---------------------------------------------------------------------------
# SI prefix parsing
# ---------------------------------------------------------------------------

#: Mapping from SI prefix characters to their numeric multipliers.
_SI_MULTIPLIERS: Dict[str, float] = {
    "p": 1e-12,
    "n": 1e-9,
    "u": 1e-6,
    "µ": 1e-6,
    "m": 1e-3,
    "k": 1e3,
    "K": 1e3,
    "M": 1e6,
    "G": 1e9,
    "T": 1e12,
}

# Pattern: optional sign, decimal number, optional SI prefix, optional unit text.
_NUMERIC_RE = re.compile(
    r"^([+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)\s*([pnuµmkKMGT]?)\s*[a-zA-ZΩ°%]*$"
)

# Property keys that are KiCad-internal metadata and should be skipped when
# extracting electrical specifications.
_META_KEYS = frozenset(
    {
        "Reference",
        "Value",
        "Footprint",
        "Datasheet",
        "ki_keywords",
        "ki_description",
        "ki_fp_filters",
        "ki_locked",
        "Keywords",
    }
)


def _parse_numeric_value(value_str: str) -> Optional[float]:
    """Parse a numeric value with an optional SI unit prefix.

    Examples::

        _parse_numeric_value("3.3V")    # → 3.3
        _parse_numeric_value("100mA")   # → 0.1
        _parse_numeric_value("4.7k")    # → 4700.0
        _parse_numeric_value("abc")     # → None

    Args:
        value_str: Raw property value string.

    Returns:
        Numeric float value, or ``None`` if the string cannot be parsed.
    """
    if not value_str:
        return None
    m = _NUMERIC_RE.match(value_str.strip())
    if not m:
        return None
    num = float(m.group(1))
    multiplier = _SI_MULTIPLIERS.get(m.group(2), 1.0)
    return num * multiplier


def _get_property(symbol: SymbolDef, key: str) -> str:
    """Return the value of the named property from *symbol*, or empty string."""
    for prop in symbol.properties:
        if prop.key == key:
            return prop.value
    return ""


def _infer_component_type(symbol: SymbolDef, lib_name: str) -> str:
    """Infer the component type category from symbol metadata.

    Checks the symbol name, ``Description`` and ``ki_description`` properties,
    and the library file stem against each entry in :data:`_TYPE_KEYWORDS`.

    Args:
        symbol:   The symbol definition to classify.
        lib_name: Stem of the library file (e.g. ``"Device"``).

    Returns:
        A canonical component-type string from :data:`_TYPE_KEYWORDS`, or
        ``"other"`` when no keyword matches.
    """
    text = " ".join(
        [
            symbol.name.lower(),
            _get_property(symbol, "Description").lower(),
            _get_property(symbol, "ki_description").lower(),
            lib_name.lower(),
        ]
    )
    for comp_type, keywords in _TYPE_KEYWORDS.items():
        for kw in keywords:
            if kw in text:
                return comp_type
    return "other"


def _extract_specifications(symbol: SymbolDef) -> Dict[str, Any]:
    """Extract normalised electrical specifications from symbol properties.

    Non-metadata properties whose values can be parsed as numeric SI quantities
    are stored as floats; remaining non-empty string values are kept as-is.

    Args:
        symbol: The symbol definition to extract specifications from.

    Returns:
        Dict mapping property names to their normalised values.
    """
    specs: Dict[str, Any] = {}
    for prop in symbol.properties:
        if prop.key in _META_KEYS:
            continue
        numeric = _parse_numeric_value(prop.value)
        if numeric is not None:
            specs[prop.key] = numeric
        elif prop.value:
            specs[prop.key] = prop.value
    return specs


# ---------------------------------------------------------------------------
# Library loading
# ---------------------------------------------------------------------------


def load_candidates_from_library(
    lib_path: Path,
    component_type_filter: str = "",
) -> List[ComponentCandidate]:
    """Load component candidates from a single KiCad symbol library file.

    Symbols that *extend* another symbol (i.e. they are derived aliases) are
    skipped because their specifications are inherited and would duplicate the
    parent entry.

    Args:
        lib_path:             Path to a ``.kicad_sym`` file.
        component_type_filter: Optional component-type string.  When non-empty
                               only symbols whose inferred type or description
                               matches are returned, reducing work for the
                               scoring stage.

    Returns:
        List of :class:`~.models.ComponentCandidate` objects for symbols that
        pass the pre-filter.
    """
    try:
        lib = SymbolLibrary.load(lib_path)
    except Exception as exc:  # noqa: BLE001
        _log.warning("Failed to load symbol library %s: %s", lib_path, exc)
        return []

    lib_name = lib_path.stem
    candidates: List[ComponentCandidate] = []
    requested_type = component_type_filter.lower().strip().replace(" ", "_").replace("-", "_")

    for sym in lib.symbols:
        # Skip alias/derived symbols; they don't add unique components.
        if sym.extends:
            continue

        comp_type = _infer_component_type(sym, lib_name)

        # Pre-filter by requested component type when specified.
        if requested_type:
            type_kws = _TYPE_KEYWORDS.get(requested_type, [requested_type])
            if comp_type != requested_type:
                text = f"{sym.name} {_get_property(sym, 'Description')}".lower()
                if not any(kw in text for kw in type_kws):
                    continue

        # Collect raw properties as a plain dict for easy access.
        props: Dict[str, str] = {p.key: p.value for p in sym.properties}

        candidate = ComponentCandidate(
            symbol=f"{lib_name}:{sym.name}",
            description=props.get(
                "Description", props.get("ki_description", sym.name)
            ),
            component_type=comp_type,
            footprint=props.get("Footprint", ""),
            datasheet_url=props.get("Datasheet", ""),
            specifications=_extract_specifications(sym),
            properties=props,
            source="kicad_lib",
            score=0.0,
        )
        candidates.append(candidate)

    return candidates


def find_library_files(search_paths: List[Path]) -> List[Path]:
    """Recursively find all ``.kicad_sym`` files under *search_paths*.

    Args:
        search_paths: Paths to search.  Each entry may be a file or directory.
                      Directories are searched recursively.

    Returns:
        Deduplicated list of ``.kicad_sym`` :class:`~pathlib.Path` objects.
    """
    seen: Set[Path] = set()
    libs: List[Path] = []
    for root in search_paths:
        if root.is_file() and root.suffix == ".kicad_sym":
            if root not in seen:
                seen.add(root)
                libs.append(root)
        elif root.is_dir():
            for lib in sorted(root.rglob("*.kicad_sym")):
                if lib not in seen:
                    seen.add(lib)
                    libs.append(lib)
    return libs
