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
    overwrite_models: Optional[bool] = None,
    skip_models: bool = False,
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
    overwrite_models:
        If ``True``, replace existing 3-D model files.  If *None*, falls
        back to the *overwrite* flag.
    skip_models:
        If ``True``, do not copy 3-D model files at all (ignore).

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

    # Copy 3-D models (unless skip_models is set)
    eff_overwrite_models = overwrite_models if overwrite_models is not None else overwrite
    copied_models: List[Path] = []
    if not skip_models:
        for src in component.model_paths:
            m_dir.mkdir(parents=True, exist_ok=True)
            dst = m_dir / src.name
            if dst.exists() and not eff_overwrite_models:
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
            fp_text = _inject_3d_models(fp_text, copied_models, fp_lib_dir=lib_dir)
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


def _inject_3d_models(fp_text: str, model_paths: List[Path], fp_lib_dir: Optional[Path] = None) -> str:
    """Append / replace ``(model …)`` nodes in *fp_text* for the given paths.

    Paths are stored using the ``${FOOTPRINTLIB_DIR}`` KiCad variable when the
    model is inside *fp_lib_dir*, making the library portable.  Absolute paths
    (normalised to forward slashes) are used as a fallback.

    Uses the S-expression parser for correct handling of nested parentheses
    (the previous regex-based approach couldn't handle the ``(xyz ...)`` nesting
    inside model blocks).
    """
    # Build model reference strings
    model_refs: List[str] = []
    for mp in model_paths:
        if fp_lib_dir is not None:
            try:
                rel = mp.resolve().relative_to(fp_lib_dir.resolve())
                model_refs.append("${FOOTPRINTLIB_DIR}/" + rel.as_posix())
            except ValueError:
                model_refs.append(mp.as_posix())
        else:
            model_refs.append(mp.as_posix())

    # Parse → extract existing model transforms → remove old models →
    # add new models with preserved transforms → serialize
    try:
        tree = parse(fp_text.strip())
    except ValueError:
        logger.warning("Could not parse footprint S-expr; appending models via text fallback")
        return _inject_3d_models_text_fallback(fp_text, model_refs)

    # Extract transform data from existing model nodes before removing them.
    # We collect transforms keyed by the model filename stem so we can match
    # them to the new model paths (the path itself changes but the file name
    # is the same).  We also keep a list ordered by appearance as a fallback.
    existing_transforms: list[dict] = []
    transforms_by_stem: dict[str, dict] = {}
    for node in tree:
        if isinstance(node, list) and node and node[0] == "model":
            xform = _extract_model_transforms(node)
            existing_transforms.append(xform)
            # Key by the filename stem of the old model path
            old_path_str = node[1] if len(node) > 1 else ""
            if isinstance(old_path_str, QStr):
                old_path_str = str(old_path_str)
            stem = Path(old_path_str).stem if old_path_str else ""
            if stem:
                transforms_by_stem[stem] = xform

    # Remove existing (model ...) nodes
    tree[:] = [
        node for node in tree
        if not (isinstance(node, list) and node and node[0] == "model")
    ]

    # Append new model nodes, preserving transforms from the original
    for idx, ref in enumerate(model_refs):
        ref_stem = Path(ref).stem
        # Try to match by filename stem first, then by position, else use defaults
        xform = (
            transforms_by_stem.get(ref_stem)
            or (existing_transforms[idx] if idx < len(existing_transforms) else None)
            or {"offset": [0, 0, 0], "scale": [1, 1, 1], "rotate": [0, 0, 0]}
        )
        tree.append([
            "model", QStr(ref),
            ["offset", ["xyz"] + xform["offset"]],
            ["scale", ["xyz"] + xform["scale"]],
            ["rotate", ["xyz"] + xform["rotate"]],
        ])

    return serialize(tree, number_precision=6) + "\n"


def _extract_model_transforms(model_node: list) -> dict:
    """Extract offset, scale, rotate from a parsed ``(model ...)`` node.

    Returns a dict with keys ``offset``, ``scale``, ``rotate``, each a list
    ``[x, y, z]``.  Falls back to identity/zero values if not found.
    """
    result = {
        "offset": [0, 0, 0],
        "scale": [1, 1, 1],
        "rotate": [0, 0, 0],
    }
    for child in model_node:
        if not isinstance(child, list) or len(child) < 2:
            continue
        tag = child[0]
        if tag in ("offset", "scale", "rotate"):
            # child is e.g. ["offset", ["xyz", x, y, z]]  or  ["at", ["xyz", x, y, z]]
            xyz_node = child[1] if isinstance(child[1], list) else None
            if xyz_node and len(xyz_node) >= 4 and xyz_node[0] == "xyz":
                result[tag] = [
                    _num(xyz_node[1]),
                    _num(xyz_node[2]),
                    _num(xyz_node[3]),
                ]
    return result


def _num(v) -> float:
    """Convert a parsed value to float, defaulting to 0."""
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _inject_3d_models_text_fallback(fp_text: str, model_refs: List[str]) -> str:
    """Fallback model injection using paren-aware text manipulation.

    Used only when the S-expression parser fails (e.g. already-corrupt files).
    Attempts to preserve transforms from existing model blocks before replacing.
    """
    # Extract transforms from existing model blocks before removing them
    existing_transforms = _extract_model_transforms_text(fp_text)

    # Remove model blocks by counting parentheses
    cleaned = _remove_model_blocks_text(fp_text)

    # Strip trailing close-paren of the footprint
    cleaned = cleaned.rstrip()
    if cleaned.endswith(")"):
        cleaned = cleaned[:-1].rstrip()

    model_blocks = []
    for idx, ref in enumerate(model_refs):
        ref_stem = Path(ref).stem
        # Try to match by stem first, then by index, else use defaults
        xform = None
        for et in existing_transforms:
            if et.get("stem") == ref_stem:
                xform = et
                break
        if xform is None and idx < len(existing_transforms):
            xform = existing_transforms[idx]
        if xform is None:
            xform = {"offset": [0, 0, 0], "scale": [1, 1, 1], "rotate": [0, 0, 0]}

        ox, oy, oz = xform["offset"]
        sx, sy, sz = xform["scale"]
        rx, ry, rz = xform["rotate"]
        model_blocks.append(
            f'  (model "{ref}"\n'
            f"    (offset (xyz {ox} {oy} {oz}))\n"
            f"    (scale (xyz {sx} {sy} {sz}))\n"
            f"    (rotate (xyz {rx} {ry} {rz}))\n"
            f"  )"
        )
    return cleaned + "\n" + "\n".join(model_blocks) + "\n)\n"


def _extract_model_transforms_text(fp_text: str) -> list[dict]:
    """Extract model transforms from footprint text using regex.

    Returns a list of dicts with offset/scale/rotate arrays and a 'stem' key.
    """
    results = []
    # Find all (model "path" ...) blocks
    pattern = re.compile(
        r'\(model\s+"([^"]*)"(.*?)\)',
        re.DOTALL,
    )
    # We need paren-aware matching for the model block
    i = 0
    n = len(fp_text)
    while i < n:
        if fp_text[i:i+6] == "(model" and (i + 6 >= n or fp_text[i+6] in " \t\n\r"):
            # Find the model path
            depth = 0
            j = i
            block_chars = []
            in_str = False
            while j < n:
                c = fp_text[j]
                if c == '"' and not in_str:
                    in_str = True
                elif c == '"' and in_str:
                    bs = 0
                    k = j - 1
                    while k >= 0 and fp_text[k] == '\\':
                        bs += 1
                        k -= 1
                    if bs % 2 == 0:
                        in_str = False
                elif not in_str:
                    if c == '(':
                        depth += 1
                    elif c == ')':
                        depth -= 1
                        if depth == 0:
                            block_chars.append(c)
                            j += 1
                            break
                block_chars.append(c)
                j += 1
            block = "".join(block_chars)
            # Extract path
            path_m = re.search(r'\(model\s+"([^"]*)"', block)
            stem = Path(path_m.group(1)).stem if path_m else ""
            # Extract xyz values
            xform: dict = {"offset": [0, 0, 0], "scale": [1, 1, 1], "rotate": [0, 0, 0], "stem": stem}
            for key in ("offset", "scale", "rotate"):
                m = re.search(
                    rf'\({key}\s+\(xyz\s+([-\d.eE+]+)\s+([-\d.eE+]+)\s+([-\d.eE+]+)\s*\)',
                    block,
                )
                if m:
                    try:
                        xform[key] = [float(m.group(1)), float(m.group(2)), float(m.group(3))]
                    except ValueError:
                        pass
                elif key == "scale":
                    xform[key] = [1, 1, 1]
            results.append(xform)
            i = j
        else:
            i += 1
    return results


def _remove_model_blocks_text(text: str) -> str:
    """Remove all ``(model ...)`` blocks using paren-aware matching."""
    result: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        # Check for "(model" followed by whitespace
        if (text[i] == "(" and text[i + 1:i + 6] == "model"
                and (i + 6 >= n or text[i + 6] in " \t\n\r")):
            # Skip the entire block by counting parens
            depth = 0
            in_str = False
            j = i
            while j < n:
                c = text[j]
                if c == '"' and not in_str:
                    in_str = True
                elif c == '"' and in_str:
                    # Check preceding backslashes
                    bs = 0
                    k = j - 1
                    while k >= 0 and text[k] == '\\':
                        bs += 1
                        k -= 1
                    if bs % 2 == 0:
                        in_str = False
                elif not in_str:
                    if c == '(':
                        depth += 1
                    elif c == ')':
                        depth -= 1
                        if depth == 0:
                            j += 1
                            break
                j += 1
            # Skip trailing whitespace
            while j < n and text[j] in " \t\n\r":
                j += 1
            i = j
        else:
            result.append(text[i])
            i += 1
    return "".join(result)


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
                # Update the Footprint field to the canonical library:name reference
                fp_stem = Path(fp_path).stem
                lib_name = Path(target_fp_lib_dir).stem
                component.fields.footprint = f"{lib_name}:{fp_stem}"
            else:
                warnings.append(f"Footprint write failed: {fp_path}")
        except Exception as exc:
            warnings.append(f"Footprint write error: {exc}")

    return ImportResult(
        success=True,
        component=component,
        warnings=warnings,
    )
