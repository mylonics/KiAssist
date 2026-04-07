"""Write imported components into KiCad symbol and footprint libraries.

Handles:
- Creating or opening existing target libraries
- Updating normalised fields on symbol/footprint
- Copying 3D model files to the target models directory
- Linking the 3D model to the footprint
- Resolving name conflicts (rename if already present)
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import List, Optional, Tuple

from ..kicad_parser.symbol_lib import SymbolLibrary, SymbolDef
from ..kicad_parser.footprint import Footprint
from ..kicad_parser.models import Position, Effects, Property
from ..kicad_parser.sexpr import parse, serialize, QStr
from .models import ImportedComponent, ImportResult

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Property helpers
# ---------------------------------------------------------------------------

_DEFAULT_EFFECTS = Effects(font_size=(1.27, 1.27), hide=False)
_HIDDEN_EFFECTS = Effects(font_size=(1.27, 1.27), hide=True)


def _make_property(name: str, value: str, *, hidden: bool = False) -> Property:
    """Return a :class:`Property` with sensible defaults."""
    return Property(
        key=name,
        value=value,
        position=Position(0.0, 0.0, 0.0),
        effects=_HIDDEN_EFFECTS if hidden else _DEFAULT_EFFECTS,
    )


def _update_or_append(sym: SymbolDef, name: str, value: str, *, hidden: bool = False) -> None:
    """Set a property by name, adding it if not already present."""
    for prop in sym.properties:
        if prop.key == name:
            prop.value = value
            return
    sym.properties.append(_make_property(name, value, hidden=hidden))


def _apply_fields_to_symbol(sym: SymbolDef, component: ImportedComponent) -> None:
    """Overwrite / insert all normalised fields onto *sym*."""
    sym.raw_tree = None  # Force re-serialisation from dataclass fields

    fs = component.fields

    # KiCad built-in fields (always present)
    _update_or_append(sym, "Reference", fs.reference or "U", hidden=False)
    _update_or_append(sym, "Value", fs.value or fs.mpn, hidden=False)
    _update_or_append(sym, "Footprint", fs.footprint, hidden=True)
    _update_or_append(sym, "Datasheet", fs.datasheet or "~", hidden=True)
    _update_or_append(sym, "Description", fs.description, hidden=True)

    # Standard KiAssist part-id fields
    if fs.mpn:
        _update_or_append(sym, "MPN", fs.mpn, hidden=True)
    if fs.manufacturer:
        _update_or_append(sym, "MF", fs.manufacturer, hidden=True)
    if fs.digikey_pn:
        _update_or_append(sym, "DKPN", fs.digikey_pn, hidden=True)
    if fs.mouser_pn:
        _update_or_append(sym, "MSPN", fs.mouser_pn, hidden=True)
    if fs.lcsc_pn:
        _update_or_append(sym, "LCSC", fs.lcsc_pn, hidden=True)
    if fs.package:
        _update_or_append(sym, "Package", fs.package, hidden=True)

    # Extra preserved fields
    for k, v in sorted(fs.extra.items()):
        _update_or_append(sym, k, v, hidden=True)


# ---------------------------------------------------------------------------
# Symbol library writer
# ---------------------------------------------------------------------------


def write_symbol_to_library(
    component: ImportedComponent,
    target_lib_path: str | Path,
    overwrite: bool = False,
) -> Tuple[bool, str]:
    """Write (or update) a symbol in the target ``.kicad_sym`` library.

    Parameters
    ----------
    component:
        The imported component whose *symbol_sexpr* / *fields* are used.
    target_lib_path:
        Path to the destination ``.kicad_sym`` file.  Created if absent.
    overwrite:
        If ``True`` and a symbol with the same name already exists, it is
        replaced.  If ``False`` a ``_2``, ``_3`` … suffix is appended.

    Returns
    -------
    (success, message)
        *message* is the final symbol name written.
    """
    target = Path(target_lib_path)

    # Load or create library
    if target.exists():
        try:
            lib = SymbolLibrary.load(target)
        except Exception as exc:
            return False, f"Failed to load target library: {exc}"
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        lib = SymbolLibrary(version=20231120, generator="kiassist_importer")

    # Determine the symbol object to use
    sym: Optional[SymbolDef] = None

    if component.symbol_sexpr:
        # Parse the raw symbol S-expression
        try:
            tree = parse(component.symbol_sexpr)
            # If it's a full library file, extract first symbol
            if tree and tree[0] == "kicad_symbol_lib":
                sub_syms = [t for t in tree[1:] if isinstance(t, list) and t and t[0] == "symbol"]
                if sub_syms:
                    sym = SymbolDef.from_tree(sub_syms[0])
            elif tree and tree[0] == "symbol":
                sym = SymbolDef.from_tree(tree)
        except Exception as exc:
            logger.warning("Symbol parse failed (%s), creating minimal stub", exc)

    if sym is None:
        # Create a minimal single-pin symbol stub
        sym = SymbolDef(name=component.name)

    # Resolve name
    desired_name = _safe_sym_name(component.name)
    sym.name = _resolve_name(lib, desired_name, overwrite)

    # Apply normalised fields
    _apply_fields_to_symbol(sym, component)

    # Remove & re-add to apply overwrite behaviour
    lib.remove_symbol(sym.name)
    lib.symbols.append(sym)

    _safe_save_lib(lib, target)
    return True, sym.name


def _safe_sym_name(raw: str) -> str:
    """Sanitise a symbol name for KiCad: no spaces or special chars."""
    name = re.sub(r"[^A-Za-z0-9_\-.]", "_", raw.strip())
    return name or "Component"


def _resolve_name(lib: SymbolLibrary, desired: str, overwrite: bool) -> str:
    if overwrite or lib.find_by_name(desired) is None:
        return desired
    i = 2
    while lib.find_by_name(f"{desired}_{i}") is not None:
        i += 1
    return f"{desired}_{i}"


def _safe_save_lib(lib: SymbolLibrary, target: Path) -> None:
    if target.exists():
        shutil.copy2(target, str(target) + ".bak")
    tmp_fd, tmp_path = tempfile.mkstemp(dir=target.parent, suffix=".tmp")
    try:
        os.close(tmp_fd)
        lib.save(tmp_path)
        os.replace(tmp_path, target)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# Footprint library writer
# ---------------------------------------------------------------------------


def write_footprint_to_library(
    component: ImportedComponent,
    target_lib_dir: str | Path,
    models_dir: Optional[str | Path] = None,
    overwrite: bool = False,
) -> Tuple[bool, str, List[Path]]:
    """Write a ``.kicad_mod`` footprint into a ``*.pretty`` library directory.

    Parameters
    ----------
    component:
        The imported component.
    target_lib_dir:
        Path to the ``.pretty`` directory.  Created if absent.
    models_dir:
        Directory to copy 3-D model files into.  If *None* a ``3dmodels``
        subdirectory inside *target_lib_dir* is used.
    overwrite:
        If ``True``, replace an existing ``.kicad_mod`` with the same name.

    Returns
    -------
    (success, final_footprint_path, copied_model_paths)
    """
    lib_dir = Path(target_lib_dir)
    lib_dir.mkdir(parents=True, exist_ok=True)

    fp_name = _safe_fp_name(component.name)
    fp_file = lib_dir / f"{fp_name}.kicad_mod"

    # Resolve name conflict
    if fp_file.exists() and not overwrite:
        i = 2
        while (lib_dir / f"{fp_name}_{i}.kicad_mod").exists():
            i += 1
        fp_name = f"{fp_name}_{i}"
        fp_file = lib_dir / f"{fp_name}.kicad_mod"

    # Determine models destination
    if models_dir is None:
        m_dir = lib_dir / "3dmodels"
    else:
        m_dir = Path(models_dir)

    # Copy 3-D models
    copied_models: List[Path] = []
    for src in component.model_paths:
        m_dir.mkdir(parents=True, exist_ok=True)
        dst = m_dir / src.name
        if dst.exists() and not overwrite:
            i = 2
            stem, suffix = src.stem, src.suffix
            while (m_dir / f"{stem}_{i}{suffix}").exists():
                i += 1
            dst = m_dir / f"{stem}_{i}{suffix}"
        shutil.copy2(src, dst)
        copied_models.append(dst)

    # Prepare footprint text
    fp_text = component.footprint_sexpr

    if fp_text:
        # Patch 3-D model references into the footprint text if we copied models
        if copied_models:
            fp_text = _inject_3d_models(fp_text, copied_models)
    else:
        # Create a minimal empty footprint
        fp_text = (
            f'(footprint "{fp_name}" (layer "F.Cu")\n'
            f'  (property "Reference" "REF**" (at 0 -1.5 0) (layer "F.SilkS")\n'
            f'    (effects (font (size 1 1) (thickness 0.15))))\n'
            f'  (property "Value" "{fp_name}" (at 0 1.5 0) (layer "F.Fab")\n'
            f'    (effects (font (size 1 1) (thickness 0.15))))\n'
            f')\n'
        )

    fp_file.write_text(fp_text, encoding="utf-8")
    component.footprint_path = fp_file

    return True, str(fp_file), copied_models


def _safe_fp_name(raw: str) -> str:
    name = re.sub(r"[^A-Za-z0-9_\-.]", "_", raw.strip())
    return name or "Footprint"


def _inject_3d_models(fp_text: str, model_paths: List[Path]) -> str:
    """Append / replace ``(model …)`` nodes in *fp_text* for the given paths."""
    # Remove any existing model nodes for cleanliness
    cleaned = re.sub(
        r"\(model\s+[^\)]*(?:\([^\)]*\)\s*)*\)",
        "",
        fp_text,
        flags=re.DOTALL,
    )
    # Remove the trailing closing paren of the footprint
    cleaned = cleaned.rstrip()
    if cleaned.endswith(")"):
        cleaned = cleaned[:-1].rstrip()

    model_blocks = []
    for mp in model_paths:
        model_blocks.append(
            f'  (model "{mp}"\n'
            f"    (offset (xyz 0 0 0))\n"
            f"    (scale (xyz 1 1 1))\n"
            f"    (rotate (xyz 0 0 0))\n"
            f"  )"
        )
    return cleaned + "\n" + "\n".join(model_blocks) + "\n)\n"


# ---------------------------------------------------------------------------
# High-level orchestrator
# ---------------------------------------------------------------------------


def commit_import(
    component: ImportedComponent,
    target_sym_lib: Optional[str | Path],
    target_fp_lib_dir: Optional[str | Path],
    models_dir: Optional[str | Path] = None,
    overwrite: bool = False,
) -> ImportResult:
    """Write both symbol and footprint to target libraries.

    Parameters
    ----------
    component:
        Populated :class:`ImportedComponent`.
    target_sym_lib:
        Path to the target ``.kicad_sym`` file.  *None* skips symbol writing.
    target_fp_lib_dir:
        Path to the target ``.pretty`` directory.  *None* skips footprint.
    models_dir:
        Directory for 3-D models.  Defaults to ``3dmodels/`` inside *target_fp_lib_dir*.
    overwrite:
        Whether to replace existing entries.

    Returns
    -------
    ImportResult
        Updated with final file paths.
    """
    warnings: list[str] = []

    if target_sym_lib and component.symbol_sexpr:
        try:
            ok, sym_name = write_symbol_to_library(component, target_sym_lib, overwrite=overwrite)
            if ok:
                component.symbol_path = Path(target_sym_lib)
                # Update footprint reference in the field set
                if target_fp_lib_dir:
                    lib_name = Path(target_fp_lib_dir).stem
                    component.fields.footprint = f"{lib_name}:{sym_name}"
            else:
                warnings.append(f"Symbol write failed: {sym_name}")
        except Exception as exc:
            warnings.append(f"Symbol write error: {exc}")

    if target_fp_lib_dir and component.footprint_sexpr:
        try:
            ok, fp_path, copied = write_footprint_to_library(
                component, target_fp_lib_dir, models_dir=models_dir, overwrite=overwrite
            )
            if ok:
                component.footprint_path = Path(fp_path)
                component.model_paths = copied
            else:
                warnings.append(f"Footprint write failed: {fp_path}")
        except Exception as exc:
            warnings.append(f"Footprint write error: {exc}")

    return ImportResult(
        success=True,
        component=component,
        warnings=warnings,
    )
