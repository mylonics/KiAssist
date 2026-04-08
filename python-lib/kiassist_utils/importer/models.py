"""Data models for the symbol/footprint importer."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional


class ImportMethod(str, Enum):
    """Supported import source types."""

    LCSC = "lcsc"
    ZIP = "zip"
    KICAD_LIB = "kicad_lib"


@dataclass
class FieldSet:
    """Normalised component field set.

    Standard fields are stored as explicit attributes; any additional fields
    (e.g. package-specific metadata) are accumulated in *extra*.
    """

    # Core required field
    mpn: str = ""

    # Standard named fields
    manufacturer: str = ""
    digikey_pn: str = ""
    mouser_pn: str = ""
    lcsc_pn: str = ""

    # Preserved metadata
    value: str = ""
    reference: str = ""
    footprint: str = ""
    datasheet: str = ""
    description: str = ""
    package: str = ""

    # Any extra fields preserved for manual review
    extra: Dict[str, str] = field(default_factory=dict)

    def to_kicad_properties(self) -> List[Dict[str, str]]:
        """Return a list of ``{name, value}`` dicts in KiCad property order."""
        props: List[Dict[str, str]] = []

        def _add(name: str, value: str) -> None:
            if value:
                props.append({"name": name, "value": value})

        # Always-present KiCad built-ins come first
        _add("Reference", self.reference or "U")
        _add("Value", self.value or self.mpn)
        _add("Footprint", self.footprint)
        _add("Datasheet", self.datasheet or "~")
        _add("Description", self.description)

        # Standard part-identification fields
        _add("MPN", self.mpn)
        _add("MF", self.manufacturer)
        _add("DKPN", self.digikey_pn)
        _add("MSPN", self.mouser_pn)
        _add("LCSC", self.lcsc_pn)
        _add("Package", self.package)

        # Extra preserved metadata
        for k, v in sorted(self.extra.items()):
            _add(k, v)

        return props


@dataclass
class ImportedComponent:
    """Result of a single component import operation."""

    name: str
    fields: FieldSet = field(default_factory=FieldSet)

    # Paths to output files (filled in by library_writer)
    symbol_path: Optional[Path] = None
    footprint_path: Optional[Path] = None
    model_paths: List[Path] = field(default_factory=list)

    # Raw S-expression strings (before writing)
    symbol_sexpr: str = ""
    footprint_sexpr: str = ""

    # Raw STEP 3D model binary data (base64-encoded for transport)
    step_data: Optional[bytes] = None

    # Source method
    import_method: ImportMethod = ImportMethod.ZIP
    source_info: str = ""


@dataclass
class ImportResult:
    """Overall result returned from an import operation."""

    success: bool
    component: Optional[ImportedComponent] = None
    warnings: List[str] = field(default_factory=list)
    error: str = ""
