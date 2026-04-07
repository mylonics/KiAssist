"""Field normalization and cleanup for imported KiCad components.

Handles:
- Mapping known field-name aliases to standard names
- MPN fallback logic (explicit MPN → Value field)
- Removal of junk/redundant fields
- Consistent field schema across all import sources
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

from .models import FieldSet

# ---------------------------------------------------------------------------
# Field-name alias maps
# ---------------------------------------------------------------------------

# Maps any known alias → standard field name.
# Keys are lower-cased for case-insensitive matching.
_FIELD_ALIASES: Dict[str, str] = {
    # MPN variants
    "mpn": "MPN",
    "mfr_pn": "MPN",
    "mfr pn": "MPN",
    "part_number": "MPN",
    "part number": "MPN",
    "manufacturer_part_number": "MPN",
    "manufacturer part number": "MPN",
    "mfr#": "MPN",
    # Manufacturer variants
    "mf": "MF",
    "manufacturer": "MF",
    "mfr": "MF",
    "mfg": "MF",
    "mfgr": "MF",
    "brand": "MF",
    # Digi-Key variants
    "dkpn": "DKPN",
    "dk_pn": "DKPN",
    "digikey": "DKPN",
    "digi-key": "DKPN",
    "digikey_pn": "DKPN",
    "digikey part number": "DKPN",
    "dk#": "DKPN",
    # Mouser variants
    "mspn": "MSPN",
    "mouser": "MSPN",
    "mouser_pn": "MSPN",
    "mouser part number": "MSPN",
    "ms#": "MSPN",
    # LCSC variants
    "lcsc": "LCSC",
    "lcsc_pn": "LCSC",
    "lcsc part number": "LCSC",
    "lcsc#": "LCSC",
    # Standard KiCad fields
    "reference": "Reference",
    "ref": "Reference",
    "value": "Value",
    "footprint": "Footprint",
    "datasheet": "Datasheet",
    "description": "Description",
    "desc": "Description",
    "package": "Package",
    "pkg": "Package",
    "case": "Package",
}

# Fields that are clearly redundant/junk and should be dropped.
_JUNK_FIELDS: frozenset = frozenset(
    {
        "ki_keywords",
        "ki_description",
        "ki_fp_filters",
        "sim.enable",
        "sim.type",
        "sim.device",
        "sim.pins",
        "sim.params",
        "sim.ibis.model",
        "dnp",
        "~",
        "",
    }
)


def _normalize_field_name(raw: str) -> str:
    """Return the canonical field name for *raw*, or *raw* unchanged."""
    return _FIELD_ALIASES.get(raw.strip().lower(), raw.strip())


def normalize_fields(raw_fields: Dict[str, str]) -> FieldSet:
    """Convert a raw ``{name: value}`` dict into a normalised :class:`FieldSet`.

    Steps
    -----
    1. Remap field names through the alias table.
    2. Remove junk / empty fields.
    3. Populate FieldSet standard attributes.
    4. Apply MPN fallback: if MPN is empty, use Value.
    5. Collect remaining fields in *extra*.

    Parameters
    ----------
    raw_fields:
        Arbitrary field names (as they come from EasyEDA, SnapEDA, etc.)

    Returns
    -------
    FieldSet
        Normalised field set ready for library writing.
    """
    canonical: Dict[str, str] = {}
    for name, value in raw_fields.items():
        if not value or not value.strip():
            continue
        norm_name = _normalize_field_name(name)
        # Skip junk fields
        if norm_name.lower() in _JUNK_FIELDS or norm_name.lower() == "~":
            continue
        # Keep the last non-empty value if we see the same canonical name twice
        canonical[norm_name] = value.strip()

    fs = FieldSet()
    fs.reference = canonical.pop("Reference", "U")
    fs.value = canonical.pop("Value", "")
    fs.footprint = canonical.pop("Footprint", "")
    fs.datasheet = canonical.pop("Datasheet", "~")
    fs.description = canonical.pop("Description", "")
    fs.package = canonical.pop("Package", "")

    fs.mpn = canonical.pop("MPN", "")
    fs.manufacturer = canonical.pop("MF", "")
    fs.digikey_pn = canonical.pop("DKPN", "")
    fs.mouser_pn = canonical.pop("MSPN", "")
    fs.lcsc_pn = canonical.pop("LCSC", "")

    # MPN fallback: use Value if MPN is still empty
    if not fs.mpn:
        fs.mpn = fs.value

    fs.extra = canonical
    return fs


def build_raw_field_dict(fields_list: List[Tuple[str, str]]) -> Dict[str, str]:
    """Convert a list of ``(name, value)`` pairs into a dict, preserving last value."""
    result: Dict[str, str] = {}
    for name, value in fields_list:
        result[name] = value
    return result
