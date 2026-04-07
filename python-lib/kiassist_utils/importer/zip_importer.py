"""ZIP file importer for Ultra Librarian and SnapEDA packages.

Supported ZIP contents
----------------------
* ``.kicad_sym``   — KiCad 6+ symbol library
* ``.lib``         — KiCad 5 / EasyEDA legacy symbol (converted)
* ``.kicad_mod``   — KiCad footprint
* ``.mod``         — Legacy footprint (passed through verbatim)
* ``.step / .stp`` — STEP 3-D model
* ``.wrl``         — VRML 3-D model
* Metadata text files (``*.txt``, ``*.csv``) for field extraction
"""

from __future__ import annotations

import io
import logging
import os
import re
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .models import ImportedComponent, ImportMethod, ImportResult
from .field_normalizer import normalize_fields, build_raw_field_dict

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# KiCad 5 legacy symbol-lib → KiCad 6 kicad_sym conversion (minimal)
# ---------------------------------------------------------------------------

_LEGACY_COMPONENT_RE = re.compile(
    r"DEF\s+(\S+)\s+(\S+).*?(?=\nDEF|\nENDLIB)", re.DOTALL
)


def _convert_legacy_sym(lib_text: str) -> str:  # noqa: C901
    """Very lightweight KiCad-5 ``.lib`` → ``.kicad_sym`` converter.

    Produces a minimal but valid KiCad 6 symbol library that preserves
    properties and passes them through ``field_normalizer``.  Graphics fidelity
    is not guaranteed; users are encouraged to verify in KiCad.
    """
    lines = lib_text.splitlines()
    symbols: List[str] = []

    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if line.startswith("DEF "):
            parts = line.split()
            sym_name = parts[1] if len(parts) > 1 else "Unknown"
            props: List[Tuple[str, str]] = []
            # Scan forward to ENDDEF collecting F fields
            while i < len(lines):
                l = lines[i].strip()
                if l.startswith("F "):
                    tokens = l.split('"')
                    if len(tokens) >= 3:
                        idx_str = l.split()[1] if len(l.split()) > 1 else ""
                        field_value = tokens[1]
                        field_name_tok = tokens[3] if len(tokens) > 3 else ""
                        idx = 0
                        try:
                            idx = int(idx_str)
                        except ValueError:
                            pass
                        default_names = {
                            0: "Reference",
                            1: "Value",
                            2: "Footprint",
                            3: "Datasheet",
                        }
                        fname = field_name_tok if field_name_tok else default_names.get(idx, f"F{idx}")
                        props.append((fname, field_value))
                if l == "ENDDEF":
                    break
                i += 1

            prop_lines = []
            for idx, (fname, fval) in enumerate(props):
                prop_lines.append(
                    f'    (property "{fname}" "{fval}" (at 0 {idx * -2.54} 0)\n'
                    f'      (effects (font (size 1.27 1.27))))\n'
                )
            sym_block = (
                f'  (symbol "{sym_name}"\n'
                f'    (in_bom yes) (on_board yes)\n'
                + "".join(prop_lines)
                + "  )\n"
            )
            symbols.append(sym_block)
        i += 1

    header = "(kicad_symbol_lib (version 20231120) (generator kiassist_importer)\n"
    return header + "".join(symbols) + ")\n"


# ---------------------------------------------------------------------------
# Field extraction from plain-text metadata files
# ---------------------------------------------------------------------------

_META_PATTERNS = [
    # "Key: Value" or "Key = Value"
    re.compile(r"^([A-Za-z_/ -]{2,40})\s*[=:]\s*(.+)$"),
    # CSV header + single row: first row = headers, second = values
]


def _extract_meta_fields(text: str) -> Dict[str, str]:
    """Parse a text/CSV metadata file into a raw field dict."""
    fields: Dict[str, str] = {}
    lines = text.splitlines()

    # Try CSV (two rows: header + data)
    if lines and "," in lines[0]:
        headers = [h.strip().strip('"') for h in lines[0].split(",")]
        data_line = next((l for l in lines[1:] if l.strip()), None)
        if data_line:
            values = [v.strip().strip('"') for v in data_line.split(",")]
            for h, v in zip(headers, values):
                if h and v:
                    fields[h] = v
        return fields

    # Try key: value
    for line in lines:
        m = _META_PATTERNS[0].match(line.strip())
        if m:
            fields[m.group(1).strip()] = m.group(2).strip()

    return fields


# ---------------------------------------------------------------------------
# Main ZIP importer
# ---------------------------------------------------------------------------


def import_zip(
    zip_path: str | Path,
    output_dir: Optional[str | Path] = None,
) -> ImportResult:
    """Extract and parse a SnapEDA / Ultra Librarian ZIP package.

    Parameters
    ----------
    zip_path:
        Path to the ``.zip`` file.
    output_dir:
        Directory where extracted files are placed.  A temp dir is used if
        *None*.

    Returns
    -------
    ImportResult
        Populated :class:`ImportedComponent` on success.
    """
    zip_path = Path(zip_path)
    if not zip_path.exists():
        return ImportResult(success=False, error=f"ZIP file not found: {zip_path}")

    if not zipfile.is_zipfile(zip_path):
        return ImportResult(success=False, error=f"Not a valid ZIP file: {zip_path}")

    if output_dir is None:
        raise ValueError(
            "import_zip requires an explicit output_dir; callers should manage "
            "the temp directory lifetime (e.g. via tempfile.TemporaryDirectory)."
        )

    tmp_dir = Path(output_dir)
    warnings: list[str] = []

    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            # --- Zip Slip protection: reject entries that escape tmp_dir ---
            resolved_base = tmp_dir.resolve()
            for member in zf.infolist():
                member_path = (resolved_base / member.filename).resolve()
                if not (
                    str(member_path).startswith(str(resolved_base) + os.sep)
                    or member_path == resolved_base
                ):
                    return ImportResult(
                        success=False,
                        error=f"Unsafe ZIP entry rejected (path traversal): {member.filename}",
                        warnings=warnings,
                    )
            zf.extractall(tmp_dir)

        # Discover files
        sym_path: Optional[Path] = None
        fp_path: Optional[Path] = None
        model_paths: List[Path] = []
        meta_fields: Dict[str, str] = {}

        for f in sorted(tmp_dir.rglob("*")):
            if not f.is_file():
                continue
            ext = f.suffix.lower()

            if ext == ".kicad_sym" and sym_path is None:
                sym_path = f
            elif ext == ".lib" and sym_path is None:
                # Convert legacy format
                try:
                    converted = _convert_legacy_sym(f.read_text(encoding="utf-8", errors="replace"))
                    sym_path = tmp_dir / (f.stem + ".kicad_sym")
                    sym_path.write_text(converted, encoding="utf-8")
                except Exception as exc:
                    warnings.append(f"Legacy symbol conversion failed for {f.name}: {exc}")
            elif ext == ".kicad_mod" and fp_path is None:
                fp_path = f
            elif ext in (".mod",) and fp_path is None:
                fp_path = f  # use legacy mod as-is
            elif ext in (".step", ".stp", ".wrl"):
                model_paths.append(f)
            elif ext in (".txt", ".csv"):
                try:
                    text = f.read_text(encoding="utf-8", errors="replace")
                    extracted = _extract_meta_fields(text)
                    if extracted:
                        meta_fields.update(extracted)
                except Exception:
                    pass

        if sym_path is None and fp_path is None:
            return ImportResult(
                success=False,
                error="ZIP contains no .kicad_sym/.lib or .kicad_mod files",
                warnings=warnings,
            )

        # Determine component name from file names
        name_hint = (sym_path or fp_path).stem  # type: ignore[union-attr]

        # Build field set
        fields = normalize_fields(meta_fields)
        if not fields.mpn:
            fields.mpn = name_hint
        if not fields.value:
            fields.value = fields.mpn

        sym_text = sym_path.read_text(encoding="utf-8") if sym_path else ""
        fp_text = fp_path.read_text(encoding="utf-8") if fp_path else ""

        component = ImportedComponent(
            name=fields.mpn or name_hint,
            fields=fields,
            symbol_sexpr=sym_text,
            footprint_sexpr=fp_text,
            import_method=ImportMethod.ZIP,
            source_info=str(zip_path.name),
        )
        component.symbol_path = sym_path
        component.footprint_path = fp_path
        component.model_paths = model_paths

        return ImportResult(success=True, component=component, warnings=warnings)

    except Exception as exc:
        logger.exception("ZIP import failed: %s", zip_path)
        return ImportResult(success=False, error=str(exc), warnings=warnings)


def import_zip_bytes(
    zip_bytes: bytes,
    filename: str = "upload.zip",
    output_dir: Optional[str | Path] = None,
) -> ImportResult:
    """Import from raw ZIP bytes (e.g. uploaded via the UI).

    Writes the bytes to a temporary file then delegates to :func:`import_zip`.
    The caller must supply *output_dir* (or accept that model file paths in the
    returned component will only be valid until this function returns and the
    temporary directory is removed).
    """
    tmp_zip = tempfile.NamedTemporaryFile(suffix=".zip", delete=False)
    try:
        tmp_zip.write(zip_bytes)
        tmp_zip.close()
        if output_dir is not None:
            return import_zip(tmp_zip.name, output_dir=output_dir)
        # No output_dir: create a temporary extract dir that persists until
        # the caller is done (caller is responsible for cleanup).
        tmp_extract_dir = tempfile.mkdtemp(prefix="kiassist_zip_")
        return import_zip(tmp_zip.name, output_dir=tmp_extract_dir)
    finally:
        try:
            os.unlink(tmp_zip.name)
        except OSError:
            pass
