"""Quick variant import for passive components (R, C, L, D).

Given a component type and an MPN, this module:

1. Looks up the MPN via the full scraping pipeline (Octopart + DigiKey +
   JLCPCB + EasyEDA) to get manufacturer data, description, supplier
   part numbers, datasheet, and optionally CAD data.
2. Parses the description to extract value, package size, tolerance,
   power/voltage rating, and other specs.
3. Maps the package size to a standard KiCad footprint.
4. Generates a descriptive variant name
   (e.g. ``R_4.7k_0603_0.1W_1%``).
5. Resolves the default KiCad symbol (e.g. ``R_Small_US``) from the
   standard library, or clones a template from the user's library.

The main difference from a regular import is that the footprint, symbol,
and 3D model already exist in the standard KiCad libraries — only the
field values and the unique naming pattern are generated.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, Tuple

if TYPE_CHECKING:
    from .models import ImportResult

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Component type definitions
# ---------------------------------------------------------------------------

COMPONENT_TYPES = {
    "resistor": {"reference": "R", "prefix": "R"},
    "capacitor": {"reference": "C", "prefix": "C"},
    "inductor": {"reference": "L", "prefix": "L"},
    "diode": {"reference": "D", "prefix": "D"},
}

# ---------------------------------------------------------------------------
# Default KiCad symbol for each component type.
#
# These are standard symbols from the KiCad built-in libraries. Users can
# override them via the app settings.  The symbol graphics and pins are
# reused as-is — only the field values are updated for each variant.
# ---------------------------------------------------------------------------

DEFAULT_SYMBOLS: Dict[str, Dict[str, str]] = {
    "resistor":  {"library": "Device", "symbol": "R_Small_US"},
    "capacitor": {"library": "Device", "symbol": "C_Small"},
    "inductor":  {"library": "Device", "symbol": "L_Small"},
    "diode":     {"library": "Device", "symbol": "D_Small"},
}

# ---------------------------------------------------------------------------
# Footprint type table — maps component type + package to KiCad footprint.
#
# This is the single source of truth for footprint resolution. Each
# component type has a KiCad library and naming prefix. The package size
# (imperial) is mapped to its metric KiCad name via _PACKAGE_METRIC.
# ---------------------------------------------------------------------------

_PACKAGE_METRIC: Dict[str, str] = {
    "0201": "0603Metric",
    "01005": "0402Metric",
    "0402": "1005Metric",
    "0603": "1608Metric",
    "0805": "2012Metric",
    "1206": "3216Metric",
    "1210": "3225Metric",
    "1812": "4532Metric",
    "2010": "5025Metric",
    "2512": "6332Metric",
    "2920": "7451Metric",
}

# KiCad footprint library and prefix per component type
_FOOTPRINT_MAP: Dict[str, Dict[str, str]] = {
    "resistor": {"library": "Resistor_SMD", "prefix": "R"},
    "capacitor": {"library": "Capacitor_SMD", "prefix": "C"},
    "inductor": {"library": "Inductor_SMD", "prefix": "L"},
}

# Common diode packages → KiCad footprint
_DIODE_PACKAGES: Dict[str, str] = {
    "SOD-123": "Diode_SMD:D_SOD-123",
    "SOD-323": "Diode_SMD:D_SOD-323",
    "SOD-523": "Diode_SMD:D_SOD-523",
    "SOD-123F": "Diode_SMD:D_SOD-123F",
    "SOD-123FL": "Diode_SMD:D_SOD-123FL",
    "SOD-80": "Diode_SMD:D_MiniMELF",
    "SOD-80C": "Diode_SMD:D_MicroMELF",
    "SOT-23": "Package_TO_SOT_SMD:SOT-23",
    "SOT-323": "Package_TO_SOT_SMD:SOT-323_SC-70",
    "DO-214AA": "Diode_SMD:D_SMB",
    "DO-214AB": "Diode_SMD:D_SMC",
    "DO-214AC": "Diode_SMD:D_SMA",
    "SMB": "Diode_SMD:D_SMB",
    "SMC": "Diode_SMD:D_SMC",
    "SMA": "Diode_SMD:D_SMA",
    "DPAK": "Package_TO_SOT_SMD:TO-252-2",
    "D2PAK": "Package_TO_SOT_SMD:TO-263-2",
}


# ---------------------------------------------------------------------------
# Description parsing
# ---------------------------------------------------------------------------

def _normalise_value(raw: str) -> str:
    """Normalise a component value string for display.

    Examples::

        "4.7K"    → "4.7k"
        "100NF"   → "100nF"
        "10UH"    → "10uH"
        "4K7"     → "4.7k"
        "2R2"     → "2.2"
        "0R1"     → "0.1"
    """
    s = raw.strip()
    if not s:
        return s

    # Handle 4K7-style notation (digit + multiplier + digit)
    m = re.match(r'^(\d+)[kKmMuUnNpP](\d+)$', s)
    if m:
        mult_char = s[len(m.group(1))]
        return f"{m.group(1)}.{m.group(2)}{mult_char.lower()}"

    # Handle 2R2-style notation (digit + R + digit) → plain ohms
    m = re.match(r'^(\d+)[rR](\d+)$', s)
    if m:
        return f"{m.group(1)}.{m.group(2)}"

    # Normalise SI suffix casing: k lowercase, F/H uppercase
    s = re.sub(r'(?i)kohm|kohms|k ohm', 'k', s)
    s = re.sub(r'(?i)mohm|mohms|m ohm', 'M', s)
    s = re.sub(r'(?i)ohm|ohms', '', s)
    # pF, nF, uF, µF
    s = re.sub(r'(?i)pf', 'pF', s)
    s = re.sub(r'(?i)nf', 'nF', s)
    s = re.sub(r'(?i)[uµ]f', 'uF', s)
    s = re.sub(r'(?i)mf', 'mF', s)
    # pH, nH, uH, µH, mH
    s = re.sub(r'(?i)ph', 'pH', s)
    s = re.sub(r'(?i)nh', 'nH', s)
    s = re.sub(r'(?i)[uµ]h', 'uH', s)
    s = re.sub(r'(?i)mh', 'mH', s)

    return s


def _extract_package_from_desc(desc: str) -> str:
    """Extract imperial package size from a description string.

    Looks for patterns like ``0603``, ``0402``, etc.
    """
    # Direct 4-digit imperial sizes
    m = re.search(r'\b(01005|0201|0402|0603|0805|1206|1210|1812|2010|2512|2920)\b', desc)
    if m:
        return m.group(1)
    return ""


def _extract_diode_package(desc: str) -> str:
    """Extract diode package from description."""
    desc_up = desc.upper()
    for pkg in sorted(_DIODE_PACKAGES.keys(), key=len, reverse=True):
        if pkg.upper() in desc_up:
            return pkg
    return ""


def parse_description(desc: str, component_type: str) -> Dict[str, str]:
    """Parse an Octopart-style component description into structured specs.

    Typical descriptions::

        "RES SMD 4.7K OHM 1% 1/10W 0603"
        "CAP CER 100NF 16V X7R 0402"
        "INDUCTOR SMD 10UH 20% 1.2A 0805"
        "DIODE SCHOTTKY 40V 1A SOD-123"

    Returns a dict with keys: ``value``, ``package``, ``tolerance``,
    ``power_rating``, ``voltage_rating``, ``dielectric``, ``current_rating``,
    ``type`` (for diodes).
    """
    result: Dict[str, str] = {}
    d = desc.strip()
    if not d:
        return result

    # Package size
    if component_type == "diode":
        pkg = _extract_diode_package(d)
        if not pkg:
            pkg = _extract_package_from_desc(d)
        result["package"] = pkg
    else:
        result["package"] = _extract_package_from_desc(d)

    # Tolerance: e.g. "1%", "5%", "0.1%", "20%", "±1%"
    tol_m = re.search(r'[±]?(\d+(?:\.\d+)?)\s*%', d)
    if tol_m:
        result["tolerance"] = tol_m.group(1) + "%"

    # Power rating: e.g. "1/10W", "0.1W", "1W", "0.25W", "100mW", "125mW"
    pw_m = re.search(r'(\d+/\d+)\s*[wW]', d)
    if pw_m:
        # Convert fraction to decimal
        num, den = pw_m.group(1).split('/')
        val = float(num) / float(den)
        if val < 1:
            result["power_rating"] = f"{val}W"
        else:
            result["power_rating"] = f"{int(val)}W"
    else:
        pw_m2 = re.search(r'(\d+(?:\.\d+)?)\s*[wW]\b', d)
        if pw_m2:
            result["power_rating"] = pw_m2.group(1) + "W"
        else:
            pw_m3 = re.search(r'(\d+)\s*m[wW]', d)
            if pw_m3:
                mw = int(pw_m3.group(1))
                result["power_rating"] = f"{mw / 1000}W"

    # Voltage rating: e.g. "16V", "50V", "100V"
    # Be careful not to match "X7R" as voltage
    vr_m = re.search(r'(?<![A-Za-z])(\d+(?:\.\d+)?)\s*[vV]\b', d)
    if vr_m:
        result["voltage_rating"] = vr_m.group(1) + "V"

    # Current rating: e.g. "1.2A", "500mA"
    cr_m = re.search(r'(\d+(?:\.\d+)?)\s*[aA]\b', d)
    if cr_m:
        result["current_rating"] = cr_m.group(1) + "A"
    else:
        cr_m2 = re.search(r'(\d+)\s*m[aA]', d)
        if cr_m2:
            result["current_rating"] = cr_m2.group(1) + "mA"

    # Dielectric (for capacitors): X5R, X7R, C0G, NP0, Y5V
    diel_m = re.search(r'\b([XCYN][057][RPGSUV]|NP0|C0G)\b', d, re.IGNORECASE)
    if diel_m:
        result["dielectric"] = diel_m.group(1).upper()

    # Diode type: Schottky, Zener, TVS, Rectifier, etc.
    if component_type == "diode":
        for dtype in ["SCHOTTKY", "ZENER", "TVS", "RECTIFIER", "SWITCHING", "LED"]:
            if dtype in d.upper():
                result["type"] = dtype.capitalize()
                break

    # Value extraction depends on component type
    if component_type == "resistor":
        # Look for resistance values: 4.7K, 100R, 10K, 1M, 47, etc.
        val_m = re.search(
            r'(?<![A-Za-z])(\d+(?:\.\d+)?)\s*([kKmM]?)\s*(?:OHM|Ohm|ohm|Ω)',
            d,
        )
        if val_m:
            num = val_m.group(1)
            suffix = val_m.group(2)
            result["value"] = _normalise_value(num + suffix)
        else:
            # Try bare number with suffix: 4.7K, 100K, 1M, 10R
            val_m2 = re.search(
                r'(?:^|[\s,])(\d+(?:\.\d+)?)\s*([kKmMrR])\b',
                d,
            )
            if val_m2:
                num = val_m2.group(1)
                suffix = val_m2.group(2)
                result["value"] = _normalise_value(num + suffix)

    elif component_type == "capacitor":
        # Look for capacitance: 100NF, 10UF, 22PF, etc.
        val_m = re.search(
            r'(?<![A-Za-z])(\d+(?:\.\d+)?)\s*([pPnNuUµmM])\s*[fF]',
            d,
        )
        if val_m:
            result["value"] = _normalise_value(val_m.group(1) + val_m.group(2) + "F")

    elif component_type == "inductor":
        # Look for inductance: 10UH, 100NH, 1MH, etc.
        val_m = re.search(
            r'(?<![A-Za-z])(\d+(?:\.\d+)?)\s*([pPnNuUµmM])\s*[hH]',
            d,
        )
        if val_m:
            result["value"] = _normalise_value(val_m.group(1) + val_m.group(2) + "H")

    elif component_type == "diode":
        # For diodes the "value" is usually the voltage + type
        pass

    return result


# ---------------------------------------------------------------------------
# Footprint mapping
# ---------------------------------------------------------------------------

def determine_footprint(component_type: str, package: str) -> str:
    """Map a component type + package to a KiCad footprint string.

    Returns an empty string if no mapping is known.
    """
    if component_type == "diode":
        # Try exact diode package match first
        fp = _DIODE_PACKAGES.get(package, "")
        if fp:
            return fp
        # Fall back to imperial size mapping  (e.g. 0805 chip diode)
        metric = _PACKAGE_METRIC.get(package, "")
        if metric:
            return f"Diode_SMD:D_{package}_{metric}"
        return ""

    info = _FOOTPRINT_MAP.get(component_type)
    if not info:
        return ""

    metric = _PACKAGE_METRIC.get(package, "")
    if not metric:
        return ""

    return f"{info['library']}:{info['prefix']}_{package}_{metric}"


# ---------------------------------------------------------------------------
# Variant name generation
# ---------------------------------------------------------------------------

def generate_variant_name(component_type: str, specs: Dict[str, str]) -> str:
    """Build a descriptive variant name from parsed specs.

    Examples::

        resistor  → R_4.7k_0603_0.1W_1%
        capacitor → C_100nF_0402_16V_X7R
        inductor  → L_10uH_0805_1.2A_20%
        diode     → D_Schottky_SOD-123_40V_1A
    """
    prefix = COMPONENT_TYPES.get(component_type, {}).get("prefix", "X")
    parts: List[str] = [prefix]

    if component_type == "resistor":
        if specs.get("value"):
            parts.append(specs["value"])
        if specs.get("package"):
            parts.append(specs["package"])
        if specs.get("power_rating"):
            parts.append(specs["power_rating"])
        if specs.get("tolerance"):
            parts.append(specs["tolerance"])

    elif component_type == "capacitor":
        if specs.get("value"):
            parts.append(specs["value"])
        if specs.get("package"):
            parts.append(specs["package"])
        if specs.get("voltage_rating"):
            parts.append(specs["voltage_rating"])
        if specs.get("dielectric"):
            parts.append(specs["dielectric"])

    elif component_type == "inductor":
        if specs.get("value"):
            parts.append(specs["value"])
        if specs.get("package"):
            parts.append(specs["package"])
        if specs.get("current_rating"):
            parts.append(specs["current_rating"])
        if specs.get("tolerance"):
            parts.append(specs["tolerance"])

    elif component_type == "diode":
        if specs.get("type"):
            parts.append(specs["type"])
        if specs.get("package"):
            parts.append(specs["package"])
        if specs.get("voltage_rating"):
            parts.append(specs["voltage_rating"])
        if specs.get("current_rating"):
            parts.append(specs["current_rating"])

    # Fallback: if we only have the prefix, use a placeholder
    if len(parts) <= 1:
        parts.append("Unknown")

    return "_".join(parts)


# ---------------------------------------------------------------------------
# Template symbol matching
# ---------------------------------------------------------------------------

def find_best_template(
    component_type: str,
    package: str,
    library_nickname: str,
    project_dir: Optional[str] = None,
) -> Optional[Dict[str, str]]:
    """Search a library for the best template symbol to clone.

    Looks for symbols matching the component type prefix and package size.
    Falls back to any symbol with the right prefix.

    Returns ``{"name": ..., "library": ...}`` or ``None``.
    """
    from ..kicad_parser.library import LibraryDiscovery
    from ..kicad_parser.symbol_lib import SymbolLibrary

    disc = LibraryDiscovery(project_dir=project_dir)
    path_str = disc.resolve_symbol_library(library_nickname)
    if not path_str:
        return None

    from pathlib import Path
    path = Path(path_str)
    if not path.exists():
        return None

    try:
        lib = SymbolLibrary.load(path)
    except Exception:
        return None

    prefix = COMPONENT_TYPES.get(component_type, {}).get("prefix", "")
    if not prefix:
        return None

    # Strategy 1: Find a symbol matching prefix + package (e.g. R_*_0603_*)
    best_pkg_match = None
    best_any_match = None

    for sym in lib.symbols:
        name = sym.name
        if not name.startswith(prefix + "_"):
            continue

        # This symbol matches the type prefix
        if best_any_match is None:
            best_any_match = name

        # Check if it also matches the package size
        if package and f"_{package}_" in name:
            best_pkg_match = name
            break  # Perfect match

    winner = best_pkg_match or best_any_match
    if winner:
        return {"name": winner, "library": library_nickname}
    return None


# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------

def quick_variant_import(
    component_type: str,
    mpn: str,
    target_library: str,
    project_dir: Optional[str] = None,
    on_progress: Optional[Callable[[str], None]] = None,
) -> Dict[str, Any]:
    """Import a passive component variant by MPN.

    Parameters
    ----------
    component_type:
        One of ``"resistor"``, ``"capacitor"``, ``"inductor"``, ``"diode"``.
    mpn:
        Manufacturer part number to look up.
    target_library:
        Library nickname to write the new variant into.
    project_dir:
        Path to KiCad project directory (for library table resolution).
    on_progress:
        Optional callback for progress messages.

    Returns
    -------
    dict
        ``{"success": bool, "name": str, "library": str, ...}``
    """
    if component_type not in COMPONENT_TYPES:
        return {"success": False, "error": f"Unknown component type: {component_type}"}

    if not mpn.strip():
        return {"success": False, "error": "MPN is required"}

    if not target_library.strip():
        return {"success": False, "error": "Target library is required"}

    def _progress(msg: str) -> None:
        if on_progress:
            on_progress(msg)

    # Step 1: Octopart lookup
    _progress("Looking up MPN on Octopart…")
    from .part_lookup import lookup_part
    part_data = lookup_part(mpn.strip())

    if not part_data.get("found"):
        return {
            "success": False,
            "error": f"MPN '{mpn}' not found on Octopart. Check the part number.",
        }

    description = part_data.get("description", "")
    manufacturer = part_data.get("manufacturer", "")
    datasheet = part_data.get("datasheet", "")
    digikey_pn = part_data.get("digikey_pn", "")
    mouser_pn = part_data.get("mouser_pn", "")
    lcsc_pn = part_data.get("lcsc_pn", "")

    logger.info("Octopart result: mpn=%s, desc=%s", mpn, description)

    # Step 2: Parse description for specs
    _progress("Parsing component specifications…")
    specs = parse_description(description, component_type)
    logger.info("Parsed specs: %s", specs)

    # Step 3: Determine footprint
    package = specs.get("package", "")
    footprint = determine_footprint(component_type, package)
    _progress(f"Footprint: {footprint or '(not determined)'}")

    # Step 4: Generate variant name
    variant_name = generate_variant_name(component_type, specs)
    _progress(f"Variant name: {variant_name}")

    # Step 5: Find a template symbol in the target library
    _progress("Searching for template symbol…")
    template = find_best_template(component_type, package, target_library, project_dir)

    if not template:
        return {
            "success": False,
            "error": (
                f"No template symbol found in '{target_library}' for "
                f"{component_type} (prefix {COMPONENT_TYPES[component_type]['prefix']}_). "
                f"Please ensure the library contains at least one {component_type} symbol."
            ),
            "specs": specs,
            "variant_name": variant_name,
            "footprint": footprint,
            "description": description,
            "manufacturer": manufacturer,
        }

    # Step 6: Build fields dict
    value = specs.get("value", "")
    ref = COMPONENT_TYPES[component_type]["reference"]

    fields: Dict[str, str] = {
        "Reference": ref,
        "Value": variant_name,
        "Footprint": footprint,
        "Datasheet": datasheet,
        "Description": description,
        "MPN": mpn.strip(),
        "MF": manufacturer,
        "DKPN": digikey_pn,
        "MSPN": mouser_pn,
        "LCSC": lcsc_pn,
        "Package": package,
    }

    # Add type-specific extra fields
    if specs.get("tolerance"):
        fields["Tolerance"] = specs["tolerance"]
    if specs.get("power_rating"):
        fields["Power"] = specs["power_rating"]
    if specs.get("voltage_rating"):
        fields["Voltage"] = specs["voltage_rating"]
    if specs.get("current_rating"):
        fields["Current"] = specs["current_rating"]
    if specs.get("dielectric"):
        fields["Dielectric"] = specs["dielectric"]
    if specs.get("type"):
        fields["Type"] = specs["type"]

    # Step 7: Clone template and create variant
    _progress(f"Creating variant from template '{template['name']}'…")
    from .kicad_lib_importer import add_variant

    result = add_variant(
        template_library=template["library"],
        template_symbol=template["name"],
        new_symbol_name=variant_name,
        fields=fields,
        project_dir=project_dir,
        target_library=target_library,
    )

    if result.get("success"):
        result["specs"] = specs
        result["variant_name"] = variant_name
        result["footprint"] = footprint
        result["description"] = description
        result["manufacturer"] = manufacturer
        result["mpn"] = mpn.strip()

    return result


def lookup_variant_preview(
    component_type: str,
    mpn: str,
) -> Dict[str, Any]:
    """Look up an MPN and return parsed specs without creating anything.

    Useful for previewing what will be created before committing.
    """
    if component_type not in COMPONENT_TYPES:
        return {"success": False, "error": f"Unknown component type: {component_type}"}

    if not mpn.strip():
        return {"success": False, "error": "MPN is required"}

    from .part_lookup import lookup_part
    part_data = lookup_part(mpn.strip())

    if not part_data.get("found"):
        return {
            "success": False,
            "error": f"MPN '{mpn}' not found on Octopart.",
        }

    description = part_data.get("description", "")
    specs = parse_description(description, component_type)
    package = specs.get("package", "")
    footprint = determine_footprint(component_type, package)
    variant_name = generate_variant_name(component_type, specs)

    return {
        "success": True,
        "mpn": part_data.get("mpn", mpn.strip()),
        "manufacturer": part_data.get("manufacturer", ""),
        "description": description,
        "datasheet": part_data.get("datasheet", ""),
        "digikey_pn": part_data.get("digikey_pn", ""),
        "mouser_pn": part_data.get("mouser_pn", ""),
        "lcsc_pn": part_data.get("lcsc_pn", ""),
        "specs": specs,
        "footprint": footprint,
        "variant_name": variant_name,
    }


# ---------------------------------------------------------------------------
# Helper: resolve 3D model file(s) from footprint S-expression
# ---------------------------------------------------------------------------

def _resolve_footprint_3d_model(
    footprint_sexpr: str,
    project_dir: Optional[str] = None,
) -> Tuple[Optional[bytes], List[Path]]:
    """Extract 3D model paths from a footprint S-expression and read STEP data.

    Parses ``(model "…")`` nodes in the footprint, resolves KiCad path
    variables (``${KICAD8_3DMODEL_DIR}`` etc.), and reads the first
    ``.step`` / ``.stp`` file found as binary data.

    Returns
    -------
    tuple[Optional[bytes], list[Path]]
        (step_data, model_paths) — *step_data* is the raw STEP binary or
        ``None`` if no ``.step`` file was found.  *model_paths* contains all
        resolved model :class:`Path` objects.
    """
    from pathlib import Path
    from ..kicad_parser.sexpr import parse
    from ..kicad_parser.library import _default_env

    step_data: Optional[bytes] = None
    model_paths: List[Path] = []

    if not footprint_sexpr.strip():
        return step_data, model_paths

    try:
        tree = parse(footprint_sexpr.strip())
    except (ValueError, Exception):
        logger.warning("Could not parse footprint sexpr for 3D model resolution")
        return step_data, model_paths

    # Build env for variable expansion
    env = dict(_default_env())
    if project_dir:
        env["KIPRJMOD"] = str(project_dir)

    for node in tree:
        if not (isinstance(node, list) and node and node[0] == "model"):
            continue
        if len(node) < 2:
            continue

        # node[1] is the model path (may be a QStr)
        raw_path = str(node[1])
        # Strip surrounding quotes if present
        if raw_path.startswith('"') and raw_path.endswith('"'):
            raw_path = raw_path[1:-1]

        # Expand ${VAR} references
        resolved = raw_path
        for var, val in env.items():
            resolved = resolved.replace(f"${{{var}}}", val)

        model_file = Path(resolved)
        if model_file.is_file():
            model_paths.append(model_file)
            if step_data is None and model_file.suffix.lower() in (".step", ".stp"):
                try:
                    step_data = model_file.read_bytes()
                    logger.info("Loaded 3D model: %s", model_file)
                except Exception as exc:
                    logger.warning("Failed to read 3D model %s: %s", model_file, exc)
        else:
            logger.debug("3D model not found on disk: %s", model_file)

    return step_data, model_paths


# ---------------------------------------------------------------------------
# New variant import — uses full scraping pipeline, returns ImportResult
# ---------------------------------------------------------------------------

def variant_import_by_part(
    component_type: str,
    mpn: str,
    default_symbol_overrides: Optional[Dict[str, Dict[str, str]]] = None,
    project_dir: Optional[str] = None,
    on_progress: Optional[Callable[[str], None]] = None,
) -> "ImportResult":
    """Import a passive component variant using the full scraping pipeline.

    Unlike :func:`quick_variant_import` which clones a template symbol and
    writes directly to a library, this function returns an
    :class:`ImportResult` compatible with the normal importer flow.  The
    frontend renders it in ImporterDetails for preview/editing/save.

    The footprint and symbol already exist in standard KiCad libraries —
    this function resolves them and populates all fields via scraping
    (Octopart + DigiKey + JLCPCB).

    Parameters
    ----------
    component_type:
        One of ``"resistor"``, ``"capacitor"``, ``"inductor"``, ``"diode"``.
    mpn:
        Manufacturer part number to look up.
    default_symbol_overrides:
        Optional per-type symbol overrides, e.g.
        ``{"resistor": {"library": "Device", "symbol": "R_Small_US"}}``.
        Falls back to :data:`DEFAULT_SYMBOLS`.
    project_dir:
        Path to KiCad project directory (for library table resolution).
    on_progress:
        Optional callback for progress messages.

    Returns
    -------
    ImportResult
        On success, the component has its symbol_sexpr populated from the
        default/override symbol, fields fully populated from scraping, and
        the footprint field set from the type mapping table.
    """
    from .models import FieldSet, ImportedComponent, ImportMethod, ImportResult

    if component_type not in COMPONENT_TYPES:
        return ImportResult(
            success=False,
            error=f"Unknown component type: {component_type}",
        )

    if not mpn.strip():
        return ImportResult(success=False, error="MPN is required")

    def _progress(msg: str) -> None:
        if on_progress:
            try:
                on_progress(msg)
            except Exception:
                pass

    warnings: list[str] = []

    # --- Step 1: Full scraping pipeline via import_by_part ---
    _progress("Looking up part data (Octopart + DigiKey + JLCPCB)…")
    from .part_lookup import import_by_part
    import tempfile

    with tempfile.TemporaryDirectory(prefix="kiassist_variant_") as tmp_dir:
        part_result = import_by_part(
            mpn=mpn.strip(),
            output_dir=tmp_dir,
            on_progress=_progress,
        )

    # Extract fields from the scraping result
    if part_result.success and part_result.component:
        fields = part_result.component.fields
        warnings.extend(part_result.warnings)
    elif part_result.warnings:
        # Partial data — try extracting what we can
        warnings.extend(part_result.warnings)
        fields = FieldSet(mpn=mpn.strip())
        if part_result.error:
            warnings.append(f"Part lookup partial: {part_result.error}")
    else:
        return ImportResult(
            success=False,
            error=part_result.error or f"MPN '{mpn}' not found.",
            warnings=part_result.warnings,
            cad_sources=part_result.cad_sources,
            octopart_url=part_result.octopart_url,
        )

    # --- Step 2: Parse description for variant-specific specs ---
    _progress("Parsing component specifications…")
    description = fields.description or ""
    specs = parse_description(description, component_type)

    # --- Step 3: Determine footprint from type table ---
    package = specs.get("package", "")
    if not package and fields.package:
        package = fields.package
        specs["package"] = package

    mapped_footprint = determine_footprint(component_type, package)
    if mapped_footprint:
        fields.footprint = mapped_footprint
    # If no mapping, keep whatever scraping found (EasyEDA footprint)

    if not fields.package and package:
        fields.package = package

    # --- Step 3b: Load footprint from KiCad library ---
    footprint_sexpr = ""
    step_data: Optional[bytes] = None
    fp_model_paths: list[Path] = []

    fp_ref = mapped_footprint or fields.footprint
    if fp_ref and ":" in fp_ref:
        fp_lib, fp_name = fp_ref.split(":", 1)
        _progress(f"Loading footprint {fp_lib}:{fp_name}…")
        try:
            from .kicad_lib_importer import import_from_footprint_lib
            fp_result = import_from_footprint_lib(
                footprint_name=fp_name,
                library_name=fp_lib,
                project_dir=project_dir,
            )
            if fp_result.success and fp_result.component:
                footprint_sexpr = fp_result.component.footprint_sexpr
                logger.info("Loaded footprint %s:%s", fp_lib, fp_name)

                # --- Step 3c: Resolve 3D model from the footprint ---
                _progress("Resolving 3D model…")
                step_data, fp_model_paths = _resolve_footprint_3d_model(
                    footprint_sexpr, project_dir,
                )
            else:
                warnings.append(
                    f"Could not load footprint {fp_lib}:{fp_name}: "
                    f"{fp_result.error}"
                )
        except Exception as exc:
            warnings.append(f"Failed to load footprint: {exc}")

    # --- Step 4: Generate variant name ---
    variant_name = generate_variant_name(component_type, specs)
    _progress(f"Variant: {variant_name}")

    # Override component name and value with the variant name
    fields.value = variant_name
    fields.reference = COMPONENT_TYPES[component_type]["reference"]

    # Add parsed specs as extra fields
    if specs.get("tolerance"):
        fields.extra["Tolerance"] = specs["tolerance"]
    if specs.get("power_rating"):
        fields.extra["Power"] = specs["power_rating"]
    if specs.get("voltage_rating"):
        fields.extra["Voltage"] = specs["voltage_rating"]
    if specs.get("current_rating"):
        fields.extra["Current"] = specs["current_rating"]
    if specs.get("dielectric"):
        fields.extra["Dielectric"] = specs["dielectric"]
    if specs.get("type"):
        fields.extra["Type"] = specs["type"]

    # --- Step 5: Resolve default symbol from KiCad library ---
    _progress("Resolving default symbol…")
    overrides = default_symbol_overrides or {}
    sym_info = overrides.get(component_type) or DEFAULT_SYMBOLS.get(component_type)

    symbol_sexpr = ""
    if sym_info:
        sym_library = sym_info.get("library", "")
        sym_name = sym_info.get("symbol", "")
        if sym_library and sym_name:
            try:
                from .kicad_lib_importer import import_from_symbol_lib
                sym_result = import_from_symbol_lib(
                    symbol_name=sym_name,
                    library_name=sym_library,
                    project_dir=project_dir,
                )
                if sym_result.success and sym_result.component:
                    symbol_sexpr = sym_result.component.symbol_sexpr
                    logger.info("Resolved default symbol %s:%s", sym_library, sym_name)
                else:
                    warnings.append(
                        f"Could not load default symbol {sym_library}:{sym_name}: "
                        f"{sym_result.error}. You can select a symbol in the editor."
                    )
            except Exception as exc:
                warnings.append(f"Failed to load default symbol: {exc}")

    # --- Step 6: Build the ImportedComponent ---
    component = ImportedComponent(
        name=variant_name,
        fields=fields,
        symbol_sexpr=symbol_sexpr,
        footprint_sexpr=footprint_sexpr,
        step_data=step_data,
        import_method=ImportMethod.KICAD_LIB,
        source_info=f"Variant:{component_type}:{mpn.strip()}",
    )
    component.model_paths = fp_model_paths

    _progress("Variant import complete.")
    return ImportResult(
        success=True,
        component=component,
        warnings=warnings,
        cad_sources=part_result.cad_sources if part_result else [],
        octopart_url=part_result.octopart_url if part_result else "",
    )
