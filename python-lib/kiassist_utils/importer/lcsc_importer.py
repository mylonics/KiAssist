"""LCSC part importer via EasyEDA → KiCad conversion.

Requires the ``easyeda2kicad`` package::

    pip install easyeda2kicad

If the package is not installed a clear :class:`ImportError` is raised so
the caller can surface a helpful message to the user.
"""

from __future__ import annotations

import logging
import os
import shutil
import tempfile
from pathlib import Path
from typing import Optional

from .models import ImportedComponent, ImportMethod, ImportResult
from .field_normalizer import normalize_fields

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Optional dependency guard
# ---------------------------------------------------------------------------

try:
    from easyeda2kicad.easyeda.easyeda_api import EasyedaApi  # type: ignore
    from easyeda2kicad.easyeda.easyeda_importer import (  # type: ignore
        EasyedaSymbolImporter,
        EasyedaFootprintImporter,
    )
    from easyeda2kicad.kicad.export_kicad_symbol import ExporterKicadSymbol  # type: ignore
    from easyeda2kicad.kicad.export_kicad_footprint import ExporterKicadFootprint  # type: ignore

    _EASYEDA_AVAILABLE = True
except ImportError:
    _EASYEDA_AVAILABLE = False


def is_available() -> bool:
    """Return True when easyeda2kicad is installed."""
    return _EASYEDA_AVAILABLE


def import_lcsc(
    lcsc_id: str,
    output_dir: Optional[str | Path] = None,
) -> ImportResult:
    """Fetch a component from LCSC/EasyEDA and convert it to KiCad format.

    Parameters
    ----------
    lcsc_id:
        LCSC part number, e.g. ``"C14663"`` (with or without the leading *C*).
    output_dir:
        Directory where the temporary files are written.  If *None* a system
        temp directory is used.  The caller (library_writer) is responsible for
        copying files to the final library location.

    Returns
    -------
    ImportResult
        On success, ``result.component`` is populated with raw S-expression
        strings and a pre-normalised :class:`FieldSet`.
    """
    if not _EASYEDA_AVAILABLE:
        return ImportResult(
            success=False,
            error=(
                "easyeda2kicad is not installed. "
                "Install it with: pip install easyeda2kicad"
            ),
        )

    # Normalise part number
    lcsc_id = lcsc_id.strip().upper()
    if not lcsc_id.startswith("C"):
        lcsc_id = "C" + lcsc_id

    tmp_dir = Path(output_dir) if output_dir else Path(tempfile.mkdtemp(prefix="kiassist_lcsc_"))
    warnings: list[str] = []

    try:
        api = EasyedaApi()
        cad_data = api.get_cad_data_of_component(lcsc_id=lcsc_id)
        if not cad_data:
            return ImportResult(
                success=False,
                error=f"No data returned for LCSC part {lcsc_id}",
            )

        # --- Symbol ---
        sym_path = tmp_dir / f"{lcsc_id}.kicad_sym"
        try:
            sym_importer = EasyedaSymbolImporter(easyeda_cp_cad_data=cad_data)
            easyeda_symbol = sym_importer.get_symbol()
            kicad_symbol = ExporterKicadSymbol(
                symbol=easyeda_symbol, kicad_version=6
            )
            kicad_symbol.export(lib_path=str(sym_path))
        except Exception as exc:
            warnings.append(f"Symbol export failed: {exc}")
            sym_path = None  # type: ignore[assignment]

        # --- Footprint ---
        fp_path = tmp_dir / f"{lcsc_id}.kicad_mod"
        try:
            fp_importer = EasyedaFootprintImporter(easyeda_cp_cad_data=cad_data)
            easyeda_fp = fp_importer.get_footprint()
            kicad_fp = ExporterKicadFootprint(footprint=easyeda_fp)
            kicad_fp.export(lib_path=str(fp_path))
        except Exception as exc:
            warnings.append(f"Footprint export failed: {exc}")
            fp_path = None  # type: ignore[assignment]

        if sym_path is None and fp_path is None:
            return ImportResult(
                success=False,
                error="Both symbol and footprint export failed.",
                warnings=warnings,
            )

        # --- Build field set from EasyEDA component info ---
        raw_fields: dict[str, str] = {}
        try:
            info = cad_data.get("dataStr", {}).get("head", {}).get("c_para", {})
            if info:
                for k, v in info.items():
                    raw_fields[k] = str(v)
        except Exception:
            pass

        # Ensure LCSC field is set
        raw_fields.setdefault("LCSC", lcsc_id)

        fields = normalize_fields(raw_fields)
        if not fields.mpn:
            fields.mpn = lcsc_id

        sym_text = sym_path.read_text(encoding="utf-8") if sym_path and sym_path.exists() else ""
        fp_text = fp_path.read_text(encoding="utf-8") if fp_path and fp_path.exists() else ""

        component = ImportedComponent(
            name=fields.mpn or lcsc_id,
            fields=fields,
            symbol_sexpr=sym_text,
            footprint_sexpr=fp_text,
            import_method=ImportMethod.LCSC,
            source_info=f"LCSC:{lcsc_id}",
        )
        if sym_path and sym_path.exists():
            component.symbol_path = sym_path
        if fp_path and fp_path.exists():
            component.footprint_path = fp_path

        return ImportResult(success=True, component=component, warnings=warnings)

    except Exception as exc:
        logger.exception("LCSC import failed for %s", lcsc_id)
        return ImportResult(success=False, error=str(exc), warnings=warnings)
