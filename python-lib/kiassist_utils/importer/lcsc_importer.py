"""LCSC part importer via EasyEDA → KiCad conversion.

Requires the ``easyeda2kicad`` package::

    pip install easyeda2kicad

If the package is not installed a clear :class:`ImportError` is raised so
the caller can surface a helpful message to the user.
"""

from __future__ import annotations

import base64
import logging
import os
import shutil
from pathlib import Path

from .models import ImportedComponent, ImportMethod, ImportResult
from .field_normalizer import normalize_fields
from .format_upgrade import upgrade_footprint, upgrade_symbol_lib

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Optional dependency guard
# ---------------------------------------------------------------------------

try:
    from easyeda2kicad.easyeda.easyeda_api import EasyedaApi  # type: ignore
    from easyeda2kicad.easyeda.easyeda_importer import (  # type: ignore
        EasyedaSymbolImporter,
        EasyedaFootprintImporter,
        Easyeda3dModelImporter,
    )
    from easyeda2kicad.kicad.export_kicad_symbol import (  # type: ignore
        ExporterSymbolKicad,
        write_component_in_symbol_lib_file,
    )
    from easyeda2kicad.kicad.export_kicad_footprint import ExporterFootprintKicad  # type: ignore
    from easyeda2kicad.kicad.export_kicad_3d_model import Exporter3dModelKicad  # type: ignore

    _EASYEDA_AVAILABLE = True
except ImportError:
    _EASYEDA_AVAILABLE = False


def is_available() -> bool:
    """Return True when easyeda2kicad is installed."""
    return _EASYEDA_AVAILABLE


def import_lcsc(
    lcsc_id: str,
    output_dir: str | Path,
) -> ImportResult:
    """Fetch a component from LCSC/EasyEDA and convert it to KiCad format.

    Parameters
    ----------
    lcsc_id:
        LCSC part number, e.g. ``"C14663"`` (with or without the leading *C*).
    output_dir:
        Directory where the temporary files are written.  The caller is
        responsible for managing this directory's lifetime (e.g. via
        ``tempfile.TemporaryDirectory``).

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
                "Try restarting the application, or reinstall with: "
                "pip install easyeda2kicad"
            ),
        )

    # Normalise part number
    lcsc_id = lcsc_id.strip().upper()
    if not lcsc_id.startswith("C"):
        lcsc_id = "C" + lcsc_id

    tmp_dir = Path(output_dir)
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
            exporter = ExporterSymbolKicad(symbol=easyeda_symbol, version=6)
            sym_content = exporter.export(footprint_lib_name=lcsc_id)
            write_component_in_symbol_lib_file(
                lib_path=str(sym_path),
                component_name=easyeda_symbol.info.name,
                component_content=sym_content,
                version=6,
            )
        except Exception as exc:
            warnings.append(f"Symbol export failed: {exc}")
            sym_path = None  # type: ignore[assignment]

        # --- Footprint ---
        fp_path = tmp_dir / f"{lcsc_id}.kicad_mod"
        try:
            fp_importer = EasyedaFootprintImporter(easyeda_cp_cad_data=cad_data)
            easyeda_fp = fp_importer.get_footprint()
            kicad_fp = ExporterFootprintKicad(footprint=easyeda_fp)
            kicad_fp.export(
                footprint_full_path=str(fp_path),
                model_3d_path=str(tmp_dir),
            )
        except Exception as exc:
            warnings.append(f"Footprint export failed: {exc}")
            fp_path = None  # type: ignore[assignment]

        if sym_path is None and fp_path is None:
            return ImportResult(
                success=False,
                error="Both symbol and footprint export failed.",
                warnings=warnings,
            )

        # --- 3D Model (STEP) ---
        step_data: bytes | None = None
        model_paths: list[Path] = []
        try:
            model_imp = Easyeda3dModelImporter(
                easyeda_cp_cad_data=cad_data,
                download_raw_3d_model=True,
                api=api,
            )
            if model_imp.output:
                # Export WRL + STEP to disk
                exporter_3d = Exporter3dModelKicad(model_3d=model_imp.output)
                if exporter_3d.output:
                    shapes_dir = tmp_dir
                    exporter_3d.export(output_dir=str(shapes_dir), overwrite=True)
                    model_name = exporter_3d.output.name
                    wrl_path = shapes_dir / f"{model_name}.wrl"
                    step_path = shapes_dir / f"{model_name}.step"
                    if wrl_path.exists():
                        model_paths.append(wrl_path)
                    if step_path.exists():
                        model_paths.append(step_path)

                # Pass raw STEP binary for frontend 3D preview
                if model_imp.output.step:
                    step_data = model_imp.output.step
        except Exception as exc:
            warnings.append(f"3D model export failed: {exc}")

        # --- Extract datasheet URL from symbol info ---
        datasheet_url = ""
        try:
            if easyeda_symbol and easyeda_symbol.info.datasheet:  # type: ignore[possibly-undefined]
                datasheet_url = easyeda_symbol.info.datasheet
        except Exception:
            pass

        # --- Build field set from EasyEDA component info ---
        raw_fields: dict[str, str] = {}
        try:
            info = cad_data.get("dataStr", {}).get("head", {}).get("c_para", {})
            if info:
                for k, v in info.items():
                    raw_fields[k] = str(v)
        except Exception:
            pass

        # Force LCSC field to the part number used for import (override any
        # existing value — c_para doesn't usually contain it directly).
        raw_fields["LCSC"] = lcsc_id

        # Pull description from EasyEDA top-level data (not in c_para).
        if "Description" not in raw_fields and "description" not in raw_fields:
            top_desc = cad_data.get("description", "")
            if top_desc:
                raw_fields["Description"] = str(top_desc)

        fields = normalize_fields(raw_fields)
        if not fields.mpn:
            fields.mpn = lcsc_id

        # Value should mirror the Manufacturer Part Number
        if fields.mpn:
            fields.value = fields.mpn

        # Populate datasheet from symbol info if not already set
        if not fields.datasheet or fields.datasheet == "~":
            if datasheet_url:
                fields.datasheet = datasheet_url

        sym_text = sym_path.read_text(encoding="utf-8") if sym_path and sym_path.exists() else ""
        fp_text = fp_path.read_text(encoding="utf-8") if fp_path and fp_path.exists() else ""

        # Upgrade legacy formats to modern KiCad 8+ syntax
        if sym_text:
            try:
                sym_text = upgrade_symbol_lib(sym_text)
            except Exception as exc:
                warnings.append(f"Symbol format upgrade failed (kept original): {exc}")
        if fp_text:
            try:
                fp_text = upgrade_footprint(fp_text)
            except Exception as exc:
                warnings.append(f"Footprint format upgrade failed (kept original): {exc}")

        component = ImportedComponent(
            name=fields.mpn or lcsc_id,
            fields=fields,
            symbol_sexpr=sym_text,
            footprint_sexpr=fp_text,
            step_data=step_data,
            import_method=ImportMethod.LCSC,
            source_info=f"LCSC:{lcsc_id}",
        )
        if sym_path and sym_path.exists():
            component.symbol_path = sym_path
        if fp_path and fp_path.exists():
            component.footprint_path = fp_path
        component.model_paths = model_paths

        return ImportResult(success=True, component=component, warnings=warnings)

    except Exception as exc:
        logger.exception("LCSC import failed for %s", lcsc_id)
        return ImportResult(success=False, error=str(exc), warnings=warnings)
