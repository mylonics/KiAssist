"""Structured parametric part-search workflow.

This module powers the *parametric* part-search loop used by the KiAssist
chat agent and the dedicated Component Search panel:

1. The agent / UI calls :func:`run_part_search` with free-text specs (and
   optionally a list of candidate MPNs that an LLM has already extracted
   from a prior turn).
2. When candidate MPNs are provided, each one is enriched via
   :func:`kiassist_utils.importer.part_lookup.lookup_part` (Octopart +
   JLCPCB) so the caller gets a structured card per candidate — MPN,
   manufacturer, datasheet URL, supplier part numbers, product page URL.
3. When no MPNs are provided, the helper falls back to a DuckDuckGo
   search over ``<specs> electronic component MPN datasheet`` and returns
   the raw search hits as a hint that the agent should extract MPNs from
   and re-call.
4. Every search produces (or reuses) a stable ``search_id`` keyed in an
   in-memory :class:`PartSearchSession` so refinement turns ("similar to
   option 2 but faster") can be threaded together without re-doing work.

The module deliberately contains **no LLM calls**.  The chat agent is the
LLM; this is the deterministic enrichment plumbing that keeps the agent's
output grounded in real Octopart / JLCPCB data instead of hallucinated
part numbers.
"""

from __future__ import annotations

import logging
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Session cache
# ---------------------------------------------------------------------------

# A search "session" expires after this many seconds since its last access.
# Searches are cheap to redo, so we don't need long-lived state.
_SESSION_TTL_SECONDS = 30 * 60  # 30 minutes


@dataclass
class _SearchTurn:
    """One round of refinement inside a :class:`PartSearchSession`."""

    specs: str
    refine: str = ""
    candidate_mpns: List[str] = field(default_factory=list)
    candidates: List[Dict[str, Any]] = field(default_factory=list)
    web_results: List[Dict[str, str]] = field(default_factory=list)
    timestamp: float = field(default_factory=time.time)


@dataclass
class _Session:
    """In-memory state for a multi-turn part-search conversation."""

    search_id: str
    turns: List[_SearchTurn] = field(default_factory=list)
    last_access: float = field(default_factory=time.time)


class PartSearchSession:
    """Process-wide cache of in-flight part-search conversations.

    The cache is intentionally tiny and process-local — nothing is
    persisted to disk.  Old sessions are pruned lazily on every access.
    """

    def __init__(self) -> None:
        self._sessions: Dict[str, _Session] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self) -> _Session:
        """Create a new empty session and return it."""
        with self._lock:
            self._prune_locked()
            sid = uuid.uuid4().hex[:12]
            session = _Session(search_id=sid)
            self._sessions[sid] = session
            return session

    def get(self, search_id: str) -> Optional[_Session]:
        """Look up an existing session by id, refreshing its TTL."""
        with self._lock:
            self._prune_locked()
            session = self._sessions.get(search_id)
            if session is not None:
                session.last_access = time.time()
            return session

    def reset(self) -> None:
        """Clear every cached session (used by tests)."""
        with self._lock:
            self._sessions.clear()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _prune_locked(self) -> None:
        cutoff = time.time() - _SESSION_TTL_SECONDS
        stale = [sid for sid, s in self._sessions.items() if s.last_access < cutoff]
        for sid in stale:
            self._sessions.pop(sid, None)


# Process-wide singleton.  Tests can call ``DEFAULT_SESSION_STORE.reset()``.
DEFAULT_SESSION_STORE = PartSearchSession()


# ---------------------------------------------------------------------------
# MPN heuristics
# ---------------------------------------------------------------------------

# A loose pattern that matches strings that look like manufacturer part
# numbers: at least one letter and one digit, length 4-32, only A-Z 0-9
# and ``- _ /``.  Used as a fallback when the agent doesn't supply MPNs.
_MPN_PATTERN = re.compile(r"\b[A-Z][A-Z0-9][A-Z0-9_\-/]{2,30}\b")

# Common English words / units that the loose MPN pattern often picks up;
# these are filtered before returning hints.
_MPN_STOPWORDS = frozenset({
    "ADC", "DAC", "MCU", "PWM", "USB", "USB-A", "USB-B", "USB-C",
    "I2C", "SPI", "UART", "JTAG", "SWD", "GPIO", "LED", "LCD",
    "CMOS", "TTL", "MOSFET", "IGBT", "BJT", "FET", "JFET",
    "PCB", "SMD", "SMT", "DIP", "SOIC", "SOT", "QFN", "QFP", "BGA",
    "VDC", "VAC", "RMS", "MAX", "MIN", "TYP", "AVG",
    "DATASHEET", "PINOUT", "ROHS", "REACH",
    "AMAZON", "DIGIKEY", "MOUSER", "OCTOPART", "JLCPCB", "LCSC",
    "GITHUB", "WIKIPEDIA", "YOUTUBE",
})


def extract_mpn_hints(text: str, max_hints: int = 8) -> List[str]:
    """Return likely MPN tokens from *text*.

    This is a deliberately fuzzy heuristic; it is only used as a fallback
    when the chat agent has not yet extracted candidate MPNs from web
    search snippets.  False positives are filtered against
    :data:`_MPN_STOPWORDS`.
    """
    if not text:
        return []
    seen: List[str] = []
    seen_set: set = set()
    for match in _MPN_PATTERN.findall(text):
        token = match.upper()
        if token in _MPN_STOPWORDS:
            continue
        # Must contain at least one digit so we don't pick up plain words.
        if not any(c.isdigit() for c in token):
            continue
        if token in seen_set:
            continue
        seen_set.add(token)
        seen.append(token)
        if len(seen) >= max_hints:
            break
    return seen


# ---------------------------------------------------------------------------
# Candidate enrichment via Octopart / JLCPCB
# ---------------------------------------------------------------------------

# URL templates for clickable product pages.  These are deterministic
# vendor URL conventions and require no API access; they are passed
# through the same ``safeUrl`` sanitiser the frontend already uses.
_PRODUCT_URL_TEMPLATES = {
    "octopart": "https://octopart.com/{slug}",
    "digikey": "https://www.digikey.com/short/{sku}",
    "lcsc":    "https://www.lcsc.com/product-detail/{sku}.html",
    "mouser":  "https://www.mouser.com/ProductDetail/{sku}",
}


def _build_product_url(part: Dict[str, Any]) -> str:
    """Pick the best-available product page URL for a candidate."""
    sellers = part.get("sellers") or {}
    # Prefer DigiKey direct click_url when available (Octopart returns it).
    dk = sellers.get("digikey") or {}
    if dk.get("click_url"):
        return dk["click_url"]
    # Then LCSC / Mouser direct URLs.
    for key in ("lcsc", "mouser"):
        offer = sellers.get(key) or {}
        if offer.get("click_url"):
            return offer["click_url"]
    # Synthesise from SKU when click_url isn't there.
    if part.get("digikey_pn"):
        return _PRODUCT_URL_TEMPLATES["digikey"].format(sku=part["digikey_pn"])
    if part.get("lcsc_pn"):
        return _PRODUCT_URL_TEMPLATES["lcsc"].format(sku=part["lcsc_pn"])
    if part.get("mouser_pn"):
        return _PRODUCT_URL_TEMPLATES["mouser"].format(sku=part["mouser_pn"])
    if part.get("slug"):
        return _PRODUCT_URL_TEMPLATES["octopart"].format(slug=part["slug"])
    return ""


def _candidate_from_lookup(mpn: str, lookup: Dict[str, Any]) -> Dict[str, Any]:
    """Convert a :func:`lookup_part` result into a structured card."""
    if not lookup or not lookup.get("found"):
        return {
            "mpn": mpn,
            "manufacturer": "",
            "description": "",
            "datasheet_url": "",
            "product_url": "",
            "digikey_pn": "",
            "lcsc_pn": "",
            "mouser_pn": "",
            "key_specs": {},
            "price": None,
            "availability": "",
            "verified": False,
            "warnings": ["Octopart returned no data for this MPN."],
        }
    return {
        "mpn": lookup.get("mpn") or mpn,
        "manufacturer": lookup.get("manufacturer", ""),
        "description": lookup.get("description", ""),
        "datasheet_url": lookup.get("datasheet", ""),
        "product_url": _build_product_url(lookup),
        "digikey_pn": lookup.get("digikey_pn", ""),
        "lcsc_pn": lookup.get("lcsc_pn", ""),
        "mouser_pn": lookup.get("mouser_pn", ""),
        "key_specs": {},  # left for the LLM to populate from datasheet text
        "price": None,    # Octopart internal endpoint does not expose pricing
        "availability": "",
        "verified": True,
        "warnings": [],
    }


def _enrich_candidates(
    candidate_mpns: List[str],
    *,
    limit: int,
    lookup_fn: Optional[Callable[[str], Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """Look up each MPN and return a list of structured cards.

    Lookups are performed sequentially so the per-service rate limiter in
    :mod:`kiassist_utils.importer.part_lookup` is honoured.

    Args:
        candidate_mpns: MPN strings to enrich.
        limit:          Maximum number of cards to return.
        lookup_fn:      Optional override for the Octopart lookup
                        function (used by tests to avoid network access).
    """
    if lookup_fn is None:
        # Local import — keep the module importable in environments
        # without optional dependencies installed.
        from .part_lookup import lookup_part as _lookup
        lookup_fn = _lookup

    cards: List[Dict[str, Any]] = []
    seen: set = set()
    for raw in candidate_mpns:
        if len(cards) >= limit:
            break
        mpn = (raw or "").strip()
        if not mpn or mpn.upper() in seen:
            continue
        seen.add(mpn.upper())
        try:
            lookup_result = lookup_fn(mpn)
        except Exception as exc:  # noqa: BLE001
            logger.warning("part_lookup failed for %s: %s", mpn, exc)
            lookup_result = {"found": False}
        cards.append(_candidate_from_lookup(mpn, lookup_result))
    return cards


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def run_part_search(
    specs: str,
    *,
    candidate_mpns: Optional[List[str]] = None,
    refine: str = "",
    search_id: Optional[str] = None,
    limit: int = 5,
    store: Optional[PartSearchSession] = None,
    lookup_fn: Optional[Callable[[str], Dict[str, Any]]] = None,
    web_search_fn: Optional[Callable[[str, int], List[Dict[str, str]]]] = None,
) -> Dict[str, Any]:
    """Run one round of the parametric part-search workflow.

    Args:
        specs:          Free-text specification string.
        candidate_mpns: MPNs to enrich (typically extracted by the chat
                        agent from a previous turn's web hits).  When
                        empty, the helper performs a DuckDuckGo search
                        and returns the raw hits as a hint.
        refine:         Optional refinement instruction ("cheaper", "lower
                        power", "similar to option 2 but faster") — saved
                        on the session for context but not interpreted
                        here.
        search_id:      Reuse an existing session id from a prior turn.
        limit:          Max candidates per response (default 5).
        store:          Override session store (defaults to the
                        process-wide singleton).
        lookup_fn:      Test hook overriding ``lookup_part``.
        web_search_fn:  Test hook overriding ``web_search``.

    Returns:
        Dict with ``search_id``, ``specs``, ``refine``, ``candidates``,
        ``web_results``, ``mpn_hints``, ``turn`` (1-based), and
        ``verified_all`` (False when any candidate could not be verified
        on Octopart, so the caller can warn the user).
    """
    if not isinstance(specs, str) or not specs.strip():
        raise ValueError("specs must be a non-empty string.")
    if not isinstance(limit, int) or limit < 1 or limit > 20:
        raise ValueError("limit must be an integer between 1 and 20.")

    store = store or DEFAULT_SESSION_STORE
    session = store.get(search_id) if search_id else None
    if session is None:
        session = store.start()

    candidates: List[Dict[str, Any]] = []
    web_results: List[Dict[str, str]] = []
    mpn_hints: List[str] = []

    if candidate_mpns:
        candidates = _enrich_candidates(
            list(candidate_mpns), limit=limit, lookup_fn=lookup_fn,
        )
    else:
        # No MPNs supplied — run a web search so the agent can extract
        # candidates from the snippets and re-call us.
        if web_search_fn is None:
            from ..web_search import web_search as _web_search

            def _default_search(q: str, n: int) -> List[Dict[str, str]]:
                return _web_search(q, max_results=n)

            web_search_fn = _default_search

        query = (
            f"{specs.strip()} {refine.strip()}"
            if refine else specs.strip()
        )
        # Bias the search toward distributor / datasheet pages.
        query = f"{query} electronic component MPN datasheet"
        try:
            web_results = web_search_fn(query, max(limit * 2, 8)) or []
        except Exception as exc:  # noqa: BLE001
            logger.warning("web_search failed in part_search: %s", exc)
            web_results = []

        # Provide a fuzzy hint list so naive callers get *something*.
        snippets = " ".join(
            (r.get("title", "") + " " + r.get("snippet", ""))
            for r in web_results
        )
        mpn_hints = extract_mpn_hints(snippets, max_hints=limit * 2)

    verified_all = bool(candidates) and all(c.get("verified") for c in candidates)

    turn = _SearchTurn(
        specs=specs.strip(),
        refine=refine.strip(),
        candidate_mpns=list(candidate_mpns or []),
        candidates=candidates,
        web_results=web_results,
    )
    session.turns.append(turn)
    session.last_access = time.time()

    return {
        "search_id": session.search_id,
        "turn": len(session.turns),
        "specs": specs.strip(),
        "refine": refine.strip(),
        "candidates": candidates,
        "web_results": web_results,
        "mpn_hints": mpn_hints,
        "verified_all": verified_all,
        "needs_mpn_extraction": not candidates and bool(web_results),
    }


# ===========================================================================
# Phase 2 — Reuse-first: find existing parts in the project
# ===========================================================================


def _tokenise_query(query: str) -> List[str]:
    """Split *query* into lowercase, alphanumeric tokens of length >= 2."""
    return [t for t in re.findall(r"[A-Za-z0-9]+", query.lower()) if len(t) >= 2]


def _haystack_match(tokens: List[str], haystack: str) -> int:
    """Return the number of *tokens* that appear in *haystack* (lowercased)."""
    if not tokens or not haystack:
        return 0
    h = haystack.lower()
    return sum(1 for t in tokens if t in h)


def _scan_project_bom(
    project_dir: Path, tokens: List[str], limit: int,
) -> List[Dict[str, Any]]:
    """Walk every ``.kicad_sch`` in *project_dir* and return matching symbols.

    A "match" is any schematic symbol whose reference, value, or
    footprint string contains at least one of the user's query tokens.
    """
    from ..kicad_parser.schematic import Schematic  # local — heavy import

    matches: List[Dict[str, Any]] = []
    for sch_path in sorted(project_dir.rglob("*.kicad_sch")):
        try:
            sch = Schematic.load(sch_path)
        except Exception:  # noqa: BLE001
            continue
        for sym in sch.symbols:
            haystack = " ".join(
                str(x) for x in (sym.reference, sym.value, sym.footprint)
                if x
            )
            score = _haystack_match(tokens, haystack)
            if score == 0:
                continue
            matches.append({
                "kind": "schematic_symbol",
                "reference": sym.reference,
                "value": sym.value,
                "footprint": sym.footprint,
                "schematic": str(sch_path),
                "score": score,
            })
            if len(matches) >= limit * 4:
                # Cap aggressively — we still re-sort and trim later.
                break
    matches.sort(key=lambda m: m["score"], reverse=True)
    return matches[:limit]


def _scan_symbol_libraries(
    project_dir: Optional[Path], tokens: List[str], limit: int,
) -> List[Dict[str, Any]]:
    """Walk the project's resolved symbol libraries and return matches.

    For each :class:`SymbolDef`, we look at the symbol name plus any
    ``MPN``, ``Description``, ``Datasheet``, ``Manufacturer``, ``Value``
    properties.  Returns at most *limit* results, sorted by match score.
    """
    from ..kicad_parser.library import LibraryDiscovery
    from ..kicad_parser.symbol_lib import SymbolLibrary

    try:
        disc = LibraryDiscovery(str(project_dir) if project_dir else None)
        entries = disc.list_symbol_libraries()
    except Exception as exc:  # noqa: BLE001
        logger.debug("LibraryDiscovery failed: %s", exc)
        return []

    matches: List[Dict[str, Any]] = []
    env = {"KIPRJMOD": str(project_dir)} if project_dir else None
    for entry in entries:
        try:
            resolved = entry.resolved_path(env=env)
        except Exception:  # noqa: BLE001
            resolved = None
        if not resolved:
            continue
        # Skip large built-in KiCad libraries — those are searched via
        # `library_search`; here we only care about project-local /
        # imported libs so we don't suggest hundreds of generic matches.
        if not _is_project_local_library(resolved, project_dir):
            continue
        try:
            lib = SymbolLibrary.load(resolved)
        except Exception:  # noqa: BLE001
            continue
        for sym in lib.symbols:
            props = {p.key.lower(): p.value for p in sym.properties}
            haystack_parts = [sym.name]
            for key in ("description", "value", "mpn", "manufacturer", "datasheet"):
                val = props.get(key, "")
                if val:
                    haystack_parts.append(str(val))
            haystack = " ".join(haystack_parts)
            score = _haystack_match(tokens, haystack)
            if score == 0:
                continue
            matches.append({
                "kind": "library_symbol",
                "lib_id": f"{entry.nickname}:{sym.name}",
                "nickname": entry.nickname,
                "symbol_name": sym.name,
                "library_path": str(resolved),
                "description": props.get("description", ""),
                "mpn": props.get("mpn", ""),
                "manufacturer": props.get("manufacturer", ""),
                "datasheet": props.get("datasheet", ""),
                "value": props.get("value", ""),
                "score": score,
            })
    matches.sort(key=lambda m: m["score"], reverse=True)
    return matches[:limit]


def _is_project_local_library(lib_path: Path, project_dir: Optional[Path]) -> bool:
    """Return True if *lib_path* is the project's own / kiassist_imports library.

    We only want to suggest *reusable* parts the user has already curated
    for this project, not symbols from KiCad's built-in libraries.
    """
    name = lib_path.name.lower()
    if "kiassist_imports" in name or "kiassist_import" in name:
        return True
    if project_dir is None:
        return False
    try:
        lib_path.resolve().relative_to(project_dir.resolve())
        return True
    except (ValueError, OSError):
        return False


def find_existing_parts(
    project_path: str,
    query: str,
    *,
    limit: int = 5,
) -> Dict[str, Any]:
    """Look up parts already present in the user's project / libraries.

    This is the "reuse-first" half of the part-search workflow: before
    proposing fresh web candidates we check whether the user already has
    something matching their specs in the project BOM or in a project /
    imported symbol library.  No internet access is performed.

    Args:
        project_path: Path to a ``.kicad_pro`` file or project directory.
        query:        Free-text query — the same specs string used for
                      :func:`run_part_search`.
        limit:        Per-section result cap (default 5).

    Returns:
        Dict with ``schematic_matches``, ``library_matches``, and a
        boolean ``has_matches`` flag.  Each match always includes a
        ``score`` field so callers can present them sorted.
    """
    if not isinstance(query, str) or not query.strip():
        raise ValueError("query must be a non-empty string.")
    if not isinstance(limit, int) or limit < 1 or limit > 25:
        raise ValueError("limit must be an integer between 1 and 25.")

    p = Path(project_path)
    if p.is_file():
        project_dir = p.parent
    elif p.is_dir():
        project_dir = p
    else:
        raise FileNotFoundError(f"Path not found: {project_path}")

    tokens = _tokenise_query(query)
    schematic_matches = _scan_project_bom(project_dir, tokens, limit)
    library_matches = _scan_symbol_libraries(project_dir, tokens, limit)

    return {
        "project_dir": str(project_dir),
        "query": query.strip(),
        "schematic_matches": schematic_matches,
        "library_matches": library_matches,
        "has_matches": bool(schematic_matches or library_matches),
    }
