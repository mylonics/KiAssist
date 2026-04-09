"""Part lookup via Octopart + DigiKey + JLCPCB and optional LCSC import.

Queries the Octopart GraphQL API to resolve manufacturer part numbers,
supplier part numbers, and cross-reference data.  Falls back to scraping
DigiKey product pages (via *cloudscraper*) when Octopart only echoes
back the MPN instead of a real DKPN.  Uses the JLCPCB/EasyEDA component
search to discover LCSC part numbers by MPN (even for parts that are out
of stock on Octopart / DigiKey).  When an LCSC part number is available
(provided or discovered), delegates to :func:`import_lcsc` for
symbol/footprint/3D model retrieval.
"""

from __future__ import annotations

import concurrent.futures
import json
import logging
import re
import tempfile
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .models import CadSource, FieldSet, ImportedComponent, ImportMethod, ImportResult

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Rate-limiting — be respectful to external services
# ---------------------------------------------------------------------------

_RATE_LIMIT_SECONDS: Dict[str, float] = {
    "octopart": 2.0,
    "digikey": 5.0,
    "jlcpcb": 2.0,
    "ultralibrarian": 3.0,
}

_last_request_time: Dict[str, float] = {}
_rate_limit_locks: Dict[str, threading.Lock] = {}
_rate_limit_meta_lock = threading.Lock()


def _rate_limit(service: str) -> None:
    """Sleep if needed to enforce a minimum interval between requests.

    Rate limiting is per-service: concurrent requests to *different*
    services (e.g. DigiKey and Octopart) proceed independently, while
    requests to the *same* service are serialised with a minimum delay.
    """
    # Acquire a per-service lock so concurrent threads targeting the same
    # service don't race, while threads targeting different services are
    # completely independent.
    with _rate_limit_meta_lock:
        if service not in _rate_limit_locks:
            _rate_limit_locks[service] = threading.Lock()
        lock = _rate_limit_locks[service]

    with lock:
        delay = _RATE_LIMIT_SECONDS.get(service, 2.0)
        last = _last_request_time.get(service, 0.0)
        elapsed = time.monotonic() - last
        if elapsed < delay:
            wait = delay - elapsed
            logger.debug("Rate-limiting %s: sleeping %.1fs", service, wait)
            time.sleep(wait)
        _last_request_time[service] = time.monotonic()


# ---------------------------------------------------------------------------
# Octopart GraphQL API
# ---------------------------------------------------------------------------

_OCTOPART_URL = "https://octopart.com/api/v4/internal"

_GRAPHQL_QUERY = """
{
  search(q: "%QUERY%", limit: 3) {
    results {
      part {
        mpn
        slug
        manufacturer { name }
        short_description
        best_datasheet { url name }
        sellers {
          company { name }
          offers {
            sku
            click_url
            packaging
          }
        }
      }
    }
  }
}
"""

# Seller normalised names → field mapping keys
_SELLER_MAP = {
    "digi-key": "digikey",
    "digikey": "digikey",
    "lcsc": "lcsc",
    "mouser": "mouser",
}


def _octopart_search(query: str) -> List[Dict[str, Any]]:
    """Run a search against the Octopart GraphQL endpoint.

    Returns a list of part dicts from the ``search.results`` array.
    """
    gql = _GRAPHQL_QUERY.replace("%QUERY%", query.replace('"', '\\"'))
    body = json.dumps({"query": gql}).encode("utf-8")
    req = urllib.request.Request(
        _OCTOPART_URL,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0 (KiAssist)",
            "Origin": "https://octopart.com",
            "Referer": "https://octopart.com/",
        },
        method="POST",
    )
    _rate_limit("octopart")
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        logger.warning("Octopart query failed: %s", exc)
        return []

    results = data.get("data", {}).get("search", {}).get("results", [])
    return [r["part"] for r in results if "part" in r]


def _pick_best_offer(
    offers: List[Dict[str, Any]],
) -> Dict[str, str]:
    """Pick the best offer from a seller, preferring cut tape packaging.

    Returns ``{"sku": ..., "click_url": ..., "packaging": ...}``.
    """
    if not offers:
        return {}

    # Priority: Cut Tape > Tape & Reel > any with a SKU
    packaging_priority = {
        "cut tape": 0,
        "tape & reel": 1,
    }

    best: Optional[Dict[str, Any]] = None
    best_score = 999

    for off in offers:
        pkg = (off.get("packaging") or "").lower()
        score = packaging_priority.get(pkg, 50)
        if score < best_score:
            best = off
            best_score = score

    if best is None:
        best = offers[0]

    return {
        "sku": best.get("sku", ""),
        "click_url": best.get("click_url", ""),
        "packaging": best.get("packaging", ""),
    }


def _is_real_dkpn(sku: str) -> bool:
    """Return True if *sku* looks like a genuine DigiKey part number.

    DigiKey part numbers typically end with ``-ND`` (e.g.
    ``497-6063-ND``, ``296-39441-1-ND``).  When a part is out of stock
    Octopart echoes back the MPN verbatim (e.g. ``STM32G474CET6``)
    which is **not** a real DKPN.
    """
    return bool(sku) and sku.upper().endswith("-ND")


def _extract_seller_data(part: Dict[str, Any]) -> Dict[str, Dict[str, str]]:
    """Extract relevant seller offers from an Octopart part dict.

    Returns a dict keyed by normalised seller id (``"digikey"``, ``"lcsc"``,
    ``"mouser"``) mapping to the best offer for that seller.
    """
    result: Dict[str, Dict[str, str]] = {}
    for seller in part.get("sellers", []):
        company_name = (seller.get("company", {}).get("name", "")).lower()
        # Match against known seller keys
        seller_key: Optional[str] = None
        for pattern, key in _SELLER_MAP.items():
            if pattern in company_name:
                seller_key = key
                break
        if seller_key and seller_key not in result:
            best = _pick_best_offer(seller.get("offers", []))
            if best.get("sku"):
                # Filter out fake DigiKey SKUs (MPN echoed back for OOS parts)
                if seller_key == "digikey" and not _is_real_dkpn(best["sku"]):
                    continue
                result[seller_key] = best
    return result


def lookup_part(query: str) -> Dict[str, Any]:
    """Query Octopart and return enriched part data.

    Parameters
    ----------
    query:
        Search term — typically an MPN, supplier PN, or LCSC number.

    Returns
    -------
    dict
        ``{"found": True/False, "mpn", "manufacturer", "description",
        "datasheet", "digikey_pn", "lcsc_pn", "mouser_pn", "sellers": {...}}``
    """
    parts = _octopart_search(query)
    if not parts:
        return {"found": False}

    part = parts[0]
    mpn = part.get("mpn", "")
    manufacturer = (part.get("manufacturer") or {}).get("name", "")
    description = part.get("short_description", "")
    datasheet_info = part.get("best_datasheet") or {}
    datasheet_url = datasheet_info.get("url", "")

    sellers = _extract_seller_data(part)

    dk = sellers.get("digikey", {})
    lcsc = sellers.get("lcsc", {})
    mouser = sellers.get("mouser", {})

    return {
        "found": True,
        "mpn": mpn,
        "manufacturer": manufacturer,
        "slug": part.get("slug", ""),
        "description": description,
        "datasheet": datasheet_url,
        "digikey_pn": dk.get("sku", ""),
        "lcsc_pn": lcsc.get("sku", ""),
        "mouser_pn": mouser.get("sku", ""),
        "sellers": sellers,
    }


# ---------------------------------------------------------------------------
# DigiKey product-page scrape (finds DKPN even for OOS parts)
# ---------------------------------------------------------------------------


def _digikey_search(mpn: str) -> Optional[str]:
    """Scrape DigiKey to find the real DKPN for *mpn*.

    Uses *cloudscraper* to bypass Cloudflare protection.  DigiKey
    redirects an exact MPN keyword search to the product detail page
    whose ``__NEXT_DATA__`` JSON contains the canonical
    ``digikeyProductNumber`` (e.g. ``497-STM32G474CET6-ND``).

    Returns the DKPN string or ``None`` on any failure.
    """
    try:
        import cloudscraper  # type: ignore
    except ImportError:
        logger.debug("cloudscraper not installed — skipping DigiKey scrape")
        return None

    try:
        scraper = cloudscraper.create_scraper(
            browser={"browser": "chrome", "platform": "windows", "desktop": True}
        )
        url = f"https://www.digikey.com/en/products/result?keywords={mpn}"
        _rate_limit("digikey")
        resp = scraper.get(url, timeout=25, allow_redirects=True)

        if resp.status_code != 200:
            logger.debug("DigiKey returned HTTP %s for %s", resp.status_code, mpn)
            return None

        html = resp.text
        final_url = resp.url

        # If we didn't land on a product detail page, look for an
        # exactMatch redirect URL and follow it.
        if "/detail/" not in final_url:
            nd_m = re.search(
                r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>',
                html,
                re.DOTALL,
            )
            if nd_m:
                nd = json.loads(nd_m.group(1))
                env = (
                    nd.get("props", {})
                    .get("pageProps", {})
                    .get("envelope", {})
                    .get("data", {})
                )
                for match in env.get("exactMatch", []):
                    if (match.get("mfrProduct", "").lower() == mpn.lower()
                            and match.get("detailUrl")):
                        detail_url = (
                            f"https://www.digikey.com{match['detailUrl']}"
                        )
                        _rate_limit("digikey")
                        resp2 = scraper.get(
                            detail_url, timeout=25, allow_redirects=True,
                        )
                        if resp2.status_code == 200:
                            html = resp2.text
                            final_url = resp2.url
                        break

        if "/detail/" not in final_url:
            logger.debug("DigiKey did not resolve to detail page for %s", mpn)
            return None

        # Parse __NEXT_DATA__ for digikeyProductNumber
        nd_m = re.search(
            r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>',
            html,
            re.DOTALL,
        )
        if nd_m:
            nd = json.loads(nd_m.group(1))
            env = (
                nd.get("props", {})
                .get("pageProps", {})
                .get("envelope", {})
                .get("data", {})
            )
            # Prefer pricing section (most reliable)
            pricing = env.get("priceQuantity", {}).get("pricing", [])
            if pricing:
                dkpn = pricing[0].get("digikeyProductNumber", "")
                if dkpn and _is_real_dkpn(dkpn):
                    return dkpn

        # Regex fallback
        m = re.search(r'"sku"\s*:\s*"([^"]*-ND)"', html)
        return m.group(1) if m else None

    except Exception as exc:
        logger.debug("DigiKey scrape failed for %s: %s", mpn, exc)
        return None


# ---------------------------------------------------------------------------
# Octopart page scrape — discover alternative CAD model sources
# ---------------------------------------------------------------------------


def _octopart_cad_models(
    manufacturer: str, mpn: str, slug: str = ""
) -> tuple[list[CadSource], str]:
    """Scrape an Octopart part page for CAD model partner info.

    Octopart embeds structured ``cad_models`` data in its React Server
    Component (RSC) stream.  This function extracts the partner list so
    users can be directed to SnapEDA, SamacSys, or TraceParts when
    EasyEDA has no symbol/footprint.

    Parameters
    ----------
    manufacturer:
        Manufacturer name (fallback for building the URL slug).
    mpn:
        Manufacturer Part Number.
    slug:
        Octopart slug from the API (e.g. ``/part/stmicroelectronics/STM32F103C8T6``).
        When available this is used directly instead of constructing the URL.

    Returns
    -------
    tuple
        ``(cad_sources, octopart_url)`` where *cad_sources* is a list of
        :class:`CadSource` and *octopart_url* is the canonical page URL.
    """
    if not manufacturer or not mpn:
        return [], ""

    # Use the API-provided slug when available; fall back to constructing one
    if slug:
        page_url = f"https://octopart.com{slug}"
    else:
        mfr_slug = re.sub(r"[^a-z0-9]+", "-", manufacturer.lower()).strip("-")
        mpn_slug = re.sub(r"[^a-zA-Z0-9]+", "-", mpn).strip("-")
        page_url = f"https://octopart.com/part/{mfr_slug}/{mpn_slug}"

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
    }

    _rate_limit("octopart")
    try:
        req = urllib.request.Request(page_url, headers=headers)
        with urllib.request.urlopen(req, timeout=20) as resp:
            html = resp.read().decode("utf-8", errors="replace")
    except Exception as exc:
        logger.debug("Octopart page fetch failed for %s: %s", page_url, exc)
        return [], page_url

    # Find the RSC chunk containing "cad_models"
    chunks = re.findall(
        r'self\.__next_f\.push\(\[1,"(.*?)"\]\)', html, re.DOTALL
    )
    cad_chunk: str | None = None
    for chunk in chunks:
        unescaped = (
            chunk.replace('\\"', '"')
            .replace("\\\\", "\\")
            .replace("\\n", "\n")
            .replace("\\/", "/")
        )
        if '"cad_models"' in unescaped:
            cad_chunk = unescaped
            break

    if not cad_chunk:
        logger.debug("No cad_models found on %s", page_url)
        return [], page_url

    # Bracket-match to extract the cad_models JSON object
    idx = cad_chunk.find('"cad_models"')
    colon_idx = cad_chunk.index(":", idx + len('"cad_models"'))
    obj_start = cad_chunk.index("{", colon_idx)

    depth = 0
    pos = obj_start
    while pos < len(cad_chunk):
        ch = cad_chunk[pos]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                break
        pos += 1

    try:
        cad_data = json.loads(cad_chunk[obj_start : pos + 1])
    except json.JSONDecodeError as exc:
        logger.debug("Failed to parse cad_models JSON: %s", exc)
        return [], page_url

    sources: list[CadSource] = []
    for model in cad_data.get("models", []):
        partner = (model.get("source") or {}).get("name", "")
        if not partner:
            continue

        previews = model.get("preview_urls") or {}

        def _get_default(d: Any) -> str:
            if isinstance(d, dict):
                return d.get("default", "")
            return str(d) if d else ""

        sources.append(
            CadSource(
                partner=partner,
                has_symbol=bool(model.get("has_symbol")),
                has_footprint=bool(model.get("has_footprint")),
                has_3d_model=bool(model.get("has_3d_model")),
                preview_symbol=_get_default(previews.get("symbol")),
                preview_footprint=_get_default(previews.get("footprint")),
                preview_3d=_get_default(previews.get("three_d")),
                download_url=model.get("cad_source_url", ""),
            )
        )

    logger.info(
        "Octopart CAD sources for %s %s: %s",
        manufacturer,
        mpn,
        [f"{s.partner}(sym={s.has_symbol},fp={s.has_footprint},3d={s.has_3d_model})"
         for s in sources],
    )
    return sources, page_url


# ---------------------------------------------------------------------------
# Ultra Librarian search — find detail page URL for KiCad CAD download
# ---------------------------------------------------------------------------


def _ultralibrarian_search(mpn: str) -> Optional[CadSource]:
    """Search Ultra Librarian for a part and return a :class:`CadSource`.

    UL serves server-rendered HTML with detail links of the form
    ``/details/{uuid}/{manufacturer}/{mpn}?uid={octopart_id}``.
    Downloads require a free account, so we provide the direct URL.

    Returns *None* when nothing is found.
    """
    try:
        import cloudscraper  # type: ignore
    except ImportError:
        logger.debug("cloudscraper not installed — skipping UL search")
        return None

    try:
        scraper = cloudscraper.create_scraper(
            browser={"browser": "chrome", "platform": "windows", "desktop": True}
        )
        url = f"https://app.ultralibrarian.com/search?queryText={mpn}"
        _rate_limit("ultralibrarian")
        resp = scraper.get(url, timeout=20)

        if resp.status_code != 200:
            logger.debug("UL returned HTTP %s for %s", resp.status_code, mpn)
            return None

        html = resp.text

        # Extract detail links:  /details/{uuid}/{manufacturer}/{mpn}?uid=...
        links = re.findall(
            r'href="(/details/([a-f0-9-]+)/([^/"]+)/([^/"?]+)(?:\?[^"]*)?)"',
            html,
        )
        if not links:
            logger.debug("No UL results for %s", mpn)
            return None

        # Pick the first unique result (best match)
        href, uuid, mfr_slug, mpn_slug = links[0]
        # Clean up &amp; entities
        href = href.split("&amp;")[0]
        detail_url = f"https://app.ultralibrarian.com{href}"

        # Check for 3D preview existence
        preview_3d = f"https://3d.ultralibrarian.com/{uuid}?ac=1"

        logger.info(
            "Ultra Librarian found %s %s → %s",
            mfr_slug.replace("-", " "), mpn_slug, detail_url,
        )

        return CadSource(
            partner="Ultra Librarian",
            has_symbol=True,
            has_footprint=True,
            has_3d_model=True,
            preview_3d=preview_3d,
            download_url=detail_url,
        )

    except Exception as exc:
        logger.debug("Ultra Librarian search failed for %s: %s", mpn, exc)
        return None


# ---------------------------------------------------------------------------
# JLCPCB / EasyEDA component search (finds LCSC numbers by MPN)
# ---------------------------------------------------------------------------


def _jlcpcb_search(keyword: str) -> Dict[str, Any]:
    """Search the JLCPCB component library by keyword (MPN, description, etc.).

    Uses ``EasyedaApi.search_jlcpcb_components`` which queries the JLCPCB
    parts API.  This works even for parts that are out of stock on
    DigiKey/Octopart, because JLCPCB maintains its own catalogue.

    Returns
    -------
    dict
        ``{"found": True/False, "lcsc_pn": ..., "mpn": ..., "brand": ...,
        "description": ..., "datasheet": ..., "package": ..., "stock": ...}``
    """
    try:
        from easyeda2kicad.easyeda.easyeda_api import EasyedaApi  # type: ignore

        api = EasyedaApi()
        _rate_limit("jlcpcb")
        data = api.search_jlcpcb_components(keyword, page_size=3)
    except ImportError:
        logger.debug("easyeda2kicad not available for JLCPCB search")
        return {"found": False}
    except Exception as exc:
        logger.warning("JLCPCB search failed: %s", exc)
        return {"found": False}

    results = data.get("results") or []
    if not results:
        return {"found": False}

    # Pick the first result (best match)
    hit = results[0]
    return {
        "found": True,
        "lcsc_pn": hit.get("lcsc", ""),
        "mpn": hit.get("model", ""),
        "brand": hit.get("brand", ""),
        "description": hit.get("description", ""),
        "datasheet": hit.get("datasheet", ""),
        "package": hit.get("package", ""),
        "stock": hit.get("stock", 0),
    }


# ---------------------------------------------------------------------------
# High-level import-by-part flow
# ---------------------------------------------------------------------------


def import_by_part(
    mpn: str = "",
    spn: str = "",
    lcsc: str = "",
    output_dir: str | Path | None = None,
    on_progress: Callable[[str], None] | None = None,
) -> ImportResult:
    """Import a component by MPN, supplier PN, or LCSC number.

    1. Query Octopart for cross-reference data (DigiKey/Mouser PNs, datasheet).
    2. Search JLCPCB by MPN to discover LCSC number (works even for parts
       out of stock on Octopart/DigiKey).
    3. If an LCSC part number is found, import symbol/footprint/3D via EasyEDA.
    4. Merge metadata from all sources into the component fields.

    Parameters
    ----------
    mpn:
        Manufacturer Part Number.
    spn:
        Supplier Part Number (e.g. Digi-Key, Mouser, or any other).
    lcsc:
        LCSC / EasyEDA part number (e.g. ``"C14663"``).
    output_dir:
        Temporary directory for output files (managed by caller).

    Returns
    -------
    ImportResult
    """
    mpn = (mpn or "").strip()
    spn = (spn or "").strip()
    lcsc = (lcsc or "").strip().upper()

    if not mpn and not spn and not lcsc:
        return ImportResult(success=False, error="No part identifier provided.")

    def _progress(msg: str) -> None:
        if on_progress:
            try:
                on_progress(msg)
            except Exception:
                pass

    warnings: list[str] = []
    octopart_data: Dict[str, Any] = {}
    jlcpcb_data: Dict[str, Any] = {}

    # --- Step 0: If only LCSC is provided, resolve MPN via JLCPCB first ---
    # LCSC numbers (e.g. "C8734") are meaningless on Octopart; we need
    # the real MPN before querying Octopart.
    if lcsc and not mpn and not spn:
        _progress("Resolving via JLCPCB…")
        try:
            jlcpcb_data = _jlcpcb_search(lcsc)
            if jlcpcb_data.get("found"):
                resolved_mpn = jlcpcb_data.get("mpn", "")
                if resolved_mpn:
                    mpn = resolved_mpn
                    logger.info(
                        "Resolved LCSC %s → MPN %s via JLCPCB", lcsc, mpn,
                    )
        except Exception as exc:
            warnings.append(f"JLCPCB lookup for LCSC {lcsc} failed: {exc}")

    # Build search terms for Octopart — never use bare LCSC numbers
    octopart_terms = [t for t in [mpn, spn] if t]

    # --- Step 1: Octopart cross-reference (MPN/SPN only) ---
    if octopart_terms:
        _progress("Querying Octopart…")
        try:
            octopart_data = lookup_part(octopart_terms[0])
            if not octopart_data.get("found"):
                for alt in octopart_terms[1:]:
                    octopart_data = lookup_part(alt)
                    if octopart_data.get("found"):
                        break
        except Exception as exc:
            warnings.append(f"Octopart lookup failed: {exc}")
            octopart_data = {}

    # --- Step 2: Determine LCSC number ---
    lcsc_pn = lcsc

    # Try Octopart seller data first
    if not lcsc_pn and octopart_data.get("lcsc_pn"):
        lcsc_pn = octopart_data["lcsc_pn"]

    # Fallback: search JLCPCB component library by MPN/SPN
    # (finds parts even when delisted from Octopart/DigiKey/LCSC main site)
    if not lcsc_pn and not jlcpcb_data.get("found"):
        jlcpcb_query = mpn or spn or (octopart_data.get("mpn") or "")
        if jlcpcb_query:
            _progress("Searching JLCPCB…")
            try:
                jlcpcb_data = _jlcpcb_search(jlcpcb_query)
                if jlcpcb_data.get("found") and jlcpcb_data.get("lcsc_pn"):
                    lcsc_pn = jlcpcb_data["lcsc_pn"]
                    logger.info(
                        "JLCPCB search found LCSC %s for %s",
                        lcsc_pn, jlcpcb_query,
                    )
            except Exception as exc:
                warnings.append(f"JLCPCB search failed: {exc}")
    elif jlcpcb_data.get("found") and jlcpcb_data.get("lcsc_pn") and not lcsc_pn:
        lcsc_pn = jlcpcb_data["lcsc_pn"]

    # --- Step 3: Import via EasyEDA if we have an LCSC number ---
    easyeda_result: Optional[ImportResult] = None
    if lcsc_pn:
        _progress("Importing from EasyEDA…")
        try:
            from .lcsc_importer import import_lcsc, is_available

            if not is_available():
                warnings.append(
                    "easyeda2kicad is not installed — skipping symbol/footprint import."
                )
            else:
                if output_dir is None:
                    import tempfile as _tf
                    output_dir = _tf.mkdtemp(prefix="kiassist_part_")
                easyeda_result = import_lcsc(lcsc_pn, output_dir=output_dir)
                if not easyeda_result.success:
                    warnings.append(
                        f"EasyEDA import failed for {lcsc_pn}: {easyeda_result.error}"
                    )
                else:
                    warnings.extend(easyeda_result.warnings)
        except Exception as exc:
            warnings.append(f"LCSC import error: {exc}")

    # --- Step 4: Build / merge the component ---
    # Look up alternative CAD sources (Octopart partners + Ultra Librarian)
    # and scrape DigiKey — all in parallel so per-service rate limits don't
    # block unrelated services.
    cad_sources: list[CadSource] = []
    octopart_url = ""
    easyeda_has_data = (
        easyeda_result is not None
        and easyeda_result.success
        and easyeda_result.component is not None
        and (easyeda_result.component.symbol_sexpr
             or easyeda_result.component.footprint_sexpr)
    )

    # Prepare parameters for the parallel lookups
    _octo_mfr = ""
    _octo_mpn_resolved = ""
    _octo_slug = ""
    if octopart_data.get("found"):
        _octo_mfr = octopart_data.get("manufacturer", "")
        _octo_mpn_resolved = octopart_data.get("mpn", "") or mpn
        _octo_slug = octopart_data.get("slug", "")

    _ul_mpn = (
        (octopart_data.get("mpn") or "") if octopart_data.get("found")
        else ""
    ) or mpn or spn

    # Determine DigiKey MPN early (before field overlay) so we know
    # whether the scrape is needed.
    _octo_dkpn = octopart_data.get("digikey_pn", "") if octopart_data.get("found") else ""
    _dk_mpn = (
        _octo_mpn_resolved or mpn or (octopart_data.get("mpn") or "")
    ) if not _octo_dkpn else ""

    # --- Run independent lookups concurrently ---
    _progress("Fetching CAD sources…")
    _fut_cad: concurrent.futures.Future | None = None
    _fut_ul: concurrent.futures.Future | None = None
    _fut_dk: concurrent.futures.Future | None = None

    with concurrent.futures.ThreadPoolExecutor(
        max_workers=3, thread_name_prefix="part_lookup",
    ) as pool:
        if _octo_mfr and _octo_mpn_resolved:
            _fut_cad = pool.submit(_octopart_cad_models, _octo_mfr, _octo_mpn_resolved, _octo_slug)
        if _ul_mpn:
            _fut_ul = pool.submit(_ultralibrarian_search, _ul_mpn)
        if _dk_mpn:
            _fut_dk = pool.submit(_digikey_search, _dk_mpn)

    # Collect results
    if _fut_cad is not None:
        try:
            cad_sources, octopart_url = _fut_cad.result()
        except Exception as exc:
            logger.debug("Octopart CAD models lookup failed: %s", exc)

    if _fut_ul is not None:
        try:
            ul_src = _fut_ul.result()
            if ul_src:
                cad_sources.insert(0, ul_src)
        except Exception as exc:
            logger.debug("Ultra Librarian search failed: %s", exc)

    dk_scrape_result: Optional[str] = None
    if _fut_dk is not None:
        try:
            dk_scrape_result = _fut_dk.result()
        except Exception as exc:
            warnings.append(f"DigiKey scrape failed: {exc}")

    if cad_sources and not easyeda_has_data:
        warnings.append(
            f"EasyEDA has no CAD data for this part. "
            f"Alternative CAD models available from: "
            f"{', '.join(s.partner for s in cad_sources)}."
        )

    if easyeda_result and easyeda_result.success and easyeda_result.component:
        component = easyeda_result.component
        fields = component.fields
    else:
        fields = FieldSet()
        component = ImportedComponent(
            name=octopart_data.get("mpn") or mpn or spn or lcsc or "Unknown",
            fields=fields,
            import_method=ImportMethod.LCSC,
            source_info=f"Octopart:{mpn or spn or lcsc}",
        )

    # Overlay Octopart data (DigiKey/Mouser PNs, manufacturer, datasheet)
    if octopart_data.get("found"):
        if not fields.mpn:
            fields.mpn = octopart_data.get("mpn", "")
        if not fields.manufacturer:
            fields.manufacturer = octopart_data.get("manufacturer", "")
        if not fields.description:
            fields.description = octopart_data.get("description", "")
        # Prefer Octopart datasheet PDF over EasyEDA's LCSC product-page URLs
        octo_ds = octopart_data.get("datasheet", "")
        if octo_ds:
            easyeda_ds = fields.datasheet or ""
            is_lcsc_page = "lcsc.com" in easyeda_ds.lower()
            if not easyeda_ds or easyeda_ds == "~" or is_lcsc_page:
                fields.datasheet = octo_ds
        if not fields.digikey_pn:
            fields.digikey_pn = octopart_data.get("digikey_pn", "")
        if not fields.mouser_pn:
            fields.mouser_pn = octopart_data.get("mouser_pn", "")
        if not fields.lcsc_pn and lcsc_pn:
            fields.lcsc_pn = lcsc_pn

    # Apply DigiKey scrape result (ran concurrently above)
    if not fields.digikey_pn and dk_scrape_result:
        fields.digikey_pn = dk_scrape_result
        logger.info("DigiKey scrape found %s for %s", dk_scrape_result, _dk_mpn)

    # Overlay JLCPCB search data where Octopart didn't fill gaps
    if jlcpcb_data.get("found"):
        if not fields.mpn:
            fields.mpn = jlcpcb_data.get("mpn", "")
        if not fields.manufacturer:
            fields.manufacturer = jlcpcb_data.get("brand", "")
        if not fields.description:
            fields.description = jlcpcb_data.get("description", "")
        if not fields.package:
            fields.package = jlcpcb_data.get("package", "")
        if not fields.lcsc_pn and lcsc_pn:
            fields.lcsc_pn = lcsc_pn
        # JLCPCB provides direct LCSC-hosted datasheet PDFs
        jlc_ds = jlcpcb_data.get("datasheet", "")
        if jlc_ds and jlc_ds.endswith(".pdf"):
            cur_ds = fields.datasheet or ""
            is_lcsc_page = "lcsc.com" in cur_ds.lower() and not cur_ds.endswith(".pdf")
            if not cur_ds or cur_ds == "~" or is_lcsc_page:
                fields.datasheet = jlc_ds

    # Ensure value mirrors MPN
    if fields.mpn and not fields.value:
        fields.value = fields.mpn

    _progress("Finalizing import…")
    component.fields = fields
    component.source_info = f"Part:{mpn or spn or lcsc}" + (f" LCSC:{lcsc_pn}" if lcsc_pn else "")

    return ImportResult(
        success=True,
        component=component,
        warnings=warnings,
        cad_sources=cad_sources,
        octopart_url=octopart_url,
    )
