"""Symbol/Footprint Importer package.

Supports three import sources:

- **LCSC** (via EasyEDA → KiCad): fetch symbol + footprint by LCSC part number.
- **ZIP** (Ultra Librarian / SnapEDA): extract symbol, footprint, and 3-D models
  from a provider ZIP archive.
- **KiCad Library**: reuse or clone components from existing installed libraries.

Typical usage::

    import tempfile
    from kiassist_utils.importer import import_lcsc, import_zip, commit_import

    with tempfile.TemporaryDirectory() as tmp_dir:
        result = import_lcsc("C14663", output_dir=tmp_dir)
        if result.success:
            final = commit_import(
                result.component,
                target_sym_lib="MyProject.kicad_sym",
                target_fp_lib_dir="MyProject.pretty",
            )
"""

from .models import CadSource, FieldSet, ImportedComponent, ImportMethod, ImportResult
from .field_normalizer import normalize_fields
from .lcsc_importer import import_lcsc, is_available as lcsc_available
from .zip_importer import import_zip, import_zip_bytes, import_raw_file, RAW_FILE_EXTS
from .part_lookup import lookup_part, import_by_part
from .kicad_lib_importer import (
    search_symbols,
    search_footprints,
    import_from_symbol_lib,
    import_from_footprint_lib,
    add_variant,
)
from .library_index import LibraryIndex
from .library_writer import commit_import
from .ai_symbol import (
    SymbolSuggestion,
    PinMapping,
    suggest_symbol,
    map_pins,
    generate_symbol,
    extract_pins_from_symbol,
    apply_pin_mapping,
)

__all__ = [
    # Models
    "FieldSet",
    "ImportedComponent",
    "ImportMethod",
    "ImportResult",
    # Normalisation
    "normalize_fields",
    # Import methods
    "import_lcsc",
    "lcsc_available",
    "import_zip",
    "import_zip_bytes",
    "import_raw_file",
    "RAW_FILE_EXTS",
    "lookup_part",
    "import_by_part",
    "search_symbols",
    "search_footprints",
    "import_from_symbol_lib",
    "import_from_footprint_lib",
    "add_variant",
    # Index
    "LibraryIndex",
    # Writing
    "commit_import",
    # AI helpers
    "SymbolSuggestion",
    "PinMapping",
    "suggest_symbol",
    "map_pins",
    "generate_symbol",
    "extract_pins_from_symbol",
    "apply_pin_mapping",
]
