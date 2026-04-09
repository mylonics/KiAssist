"""Pre-built in-memory index for fast symbol and footprint searching.

The :class:`LibraryIndex` scans all KiCad library tables once (in a
background thread) and builds lightweight lookup structures that make
subsequent searches near-instant — no repeated filesystem traversal or
file parsing required.

Usage::

    idx = LibraryIndex(project_dir="/path/to/project")
    idx.build()  # or idx.build_async() for non-blocking startup
    results = idx.search_footprints("SOIC 8", max_results=50)
    results = idx.search_symbols("LM358", max_results=50)
    idx.rebuild()  # after library changes
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Cache format version — bump when the serialised schema changes.
_CACHE_VERSION = 1


# ---------------------------------------------------------------------------
# Index entry models
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class FootprintEntry:
    """Cached footprint metadata — one per ``.kicad_mod`` file."""
    library: str
    name: str
    path: str
    # Pre-computed for fast matching
    name_norm: str = ""
    name_tokens: Tuple[str, ...] = ()


@dataclass(slots=True)
class SymbolEntry:
    """Cached symbol metadata — one per symbol in a ``.kicad_sym`` library."""
    library: str
    name: str
    description: str = ""
    value: str = ""
    footprint: str = ""
    # Pre-computed for fast matching
    name_norm: str = ""
    name_tokens: Tuple[str, ...] = ()
    searchable_norm: str = ""


# ---------------------------------------------------------------------------
# Text normalisation (shared with kicad_lib_importer scoring)
# ---------------------------------------------------------------------------

_SEP_RE = re.compile(r"[-_.,/\\:\s]+")


def _normalize(text: str) -> str:
    """Lowercase and collapse common separators to spaces."""
    return _SEP_RE.sub(" ", text.lower())


def _tokenize(text: str) -> Tuple[str, ...]:
    """Split *text* into non-empty lowercase tokens."""
    return tuple(t for t in _SEP_RE.split(text.lower()) if t)


def _primary_token(tokens: Tuple[str, ...] | List[str]) -> str:
    """Return the longest token (most specific) as the mandatory filter."""
    return max(tokens, key=len) if tokens else ""


def _token_in_any(token: str, targets: Tuple[str, ...] | List[str]) -> bool:
    return any(token in t for t in targets)


# ---------------------------------------------------------------------------
# LibraryIndex
# ---------------------------------------------------------------------------


class LibraryIndex:
    """In-memory index of all KiCad symbols and footprints.

    Build once (or lazily on first search), then search thousands of
    entries in milliseconds with no filesystem I/O.

    Parameters
    ----------
    project_dir:
        Path to the KiCad project directory.  Used to discover
        project-local library tables.
    """

    def __init__(self, project_dir: Optional[str | Path] = None) -> None:
        self._project_dir = str(project_dir) if project_dir else None
        self._footprints: List[FootprintEntry] = []
        self._symbols: List[SymbolEntry] = []
        self._lib_norms: Dict[str, str] = {}       # nickname -> normalised
        self._lib_tokens: Dict[str, Tuple[str, ...]] = {}  # nickname -> tokens
        self._ready = threading.Event()
        self._building = False
        self._build_lock = threading.Lock()
        self._build_time: float = 0.0
        self._from_cache = False  # True when current data was loaded from disk cache
        self._discovery: Any = None  # Cached LibraryDiscovery instance
        self._rebuild_pending = False  # True when another build is queued

        # Attempt to warm the index from disk cache immediately so
        # searches can proceed while the live build runs in the background.
        self._load_cache()

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def is_ready(self) -> bool:
        """``True`` once the index has been built at least once."""
        return self._ready.is_set()

    @property
    def footprint_count(self) -> int:
        return len(self._footprints)

    @property
    def symbol_count(self) -> int:
        return len(self._symbols)

    @property
    def build_time(self) -> float:
        """Time in seconds taken by the last build."""
        return self._build_time

    # ------------------------------------------------------------------
    # Build / rebuild
    # ------------------------------------------------------------------

    def build(self) -> None:
        """Build the index synchronously (blocks until complete).

        If another thread is already building, this marks a pending
        rebuild so the current build is followed by a fresh one —
        no build request is silently dropped.
        """
        if not self._build_lock.acquire(blocking=False):
            # Another thread is building.  Mark that we need another
            # round once it finishes (the project dir may have changed).
            self._rebuild_pending = True
            return

        # We hold the lock.
        self._building = True
        try:
            while True:
                self._rebuild_pending = False
                t0 = time.monotonic()
                self._do_build()
                self._build_time = time.monotonic() - t0
                logger.info(
                    "Library index built in %.2fs — %d symbols, %d footprints",
                    self._build_time,
                    len(self._symbols),
                    len(self._footprints),
                )
                # If someone requested another build while we were busy,
                # loop and build again with the latest project_dir.
                if not self._rebuild_pending:
                    break
                logger.info("Rebuild pending — running another pass")
        finally:
            self._building = False
            self._ready.set()
            self._build_lock.release()

    def build_async(self, callback: Optional[Any] = None) -> threading.Thread:
        """Build the index in a background thread.

        Parameters
        ----------
        callback:
            Optional callable invoked (with no arguments) when the build
            finishes.

        Returns
        -------
        threading.Thread
            The daemon thread performing the build.
        """
        # Set building flag eagerly so that status() reports
        # "building" immediately — before the thread acquires the lock.
        self._building = True

        def _worker():
            self.build()
            if callback:
                try:
                    callback()
                except Exception:
                    logger.exception("Library index build callback failed")

        t = threading.Thread(target=_worker, name="lib-index-build", daemon=True)
        t.start()
        return t

    def rebuild(self) -> None:
        """Rebuild the index synchronously.

        Existing data (from cache or a previous build) remains available
        for searches while the rebuild runs — the new data is swapped in
        atomically once complete.
        """
        self.build()

    def rebuild_async(self, callback: Optional[Any] = None) -> threading.Thread:
        """Rebuild in a background thread.

        Existing data (cached or previously built) stays available for
        searches while the live build runs.  The new data is swapped
        in atomically once complete.
        """
        return self.build_async(callback=callback)

    def wait_ready(self, timeout: Optional[float] = None) -> bool:
        """Block until the index is ready.  Returns ``True`` if ready."""
        return self._ready.wait(timeout=timeout)

    def set_project_dir(self, project_dir: Optional[str | Path]) -> None:
        """Update the project directory.

        Loads any project-specific cache from disk so that data is
        available immediately.  Call :meth:`rebuild_async` afterwards
        to schedule a live refresh.
        """
        new_dir = str(project_dir) if project_dir else None
        if new_dir != self._project_dir:
            self._project_dir = new_dir
            self._discovery = None  # Invalidate cached discovery
            # Try to load a project-specific cache immediately
            self._load_cache()

    # ------------------------------------------------------------------
    # Library discovery (shared / cached)
    # ------------------------------------------------------------------

    def get_discovery(self) -> Any:
        """Return a cached :class:`LibraryDiscovery` for the current project.

        Creates one lazily if needed.  The same instance is reused until
        the project directory changes or the index is rebuilt.
        """
        if self._discovery is None:
            from ..kicad_parser.library import LibraryDiscovery
            self._discovery = LibraryDiscovery(project_dir=self._project_dir)
        return self._discovery

    def list_symbol_libraries(self) -> List[Dict[str, str]]:
        """Return symbol library nicknames and URIs from the cached discovery."""
        disc = self.get_discovery()
        return [{"nickname": e.nickname, "uri": e.uri} for e in disc.list_symbol_libraries()]

    def list_footprint_libraries(self) -> List[Dict[str, str]]:
        """Return footprint library nicknames and URIs from the cached discovery."""
        disc = self.get_discovery()
        return [{"nickname": e.nickname, "uri": e.uri} for e in disc.list_footprint_libraries()]

    # ------------------------------------------------------------------
    # Search — footprints
    # ------------------------------------------------------------------

    def search_footprints(
        self,
        query: str,
        library_name: Optional[str] = None,
        max_results: int = 50,
    ) -> List[Dict[str, str]]:
        """Search the footprint index.

        If the index isn't ready yet, blocks up to 30 s for it.  Returns
        the same dict format as :func:`kicad_lib_importer.search_footprints`.
        """
        # If nothing loaded yet, kick off a background build but
        # return immediately — the cache is the source of truth.
        if not self._ready.is_set():
            if not self._building:
                self.build_async()
            return []

        query_norm = _normalize(query.strip())
        query_tokens = _tokenize(query.strip())
        if not query_tokens:
            return []

        primary = _primary_token(query_tokens)

        scored: List[Tuple[int, FootprintEntry]] = []
        for fp in self._footprints:
            if library_name and fp.library != library_name:
                continue

            score = self._score_fp(
                fp, query_norm, query_tokens, primary,
            )
            if score > 0:
                scored.append((score, fp))

        scored.sort(key=lambda x: (-x[0], x[1].library, x[1].name))
        return [
            {"library": fp.library, "name": fp.name, "path": fp.path}
            for _, fp in scored[:max_results]
        ]

    # ------------------------------------------------------------------
    # Search — symbols
    # ------------------------------------------------------------------

    def search_symbols(
        self,
        query: str,
        library_name: Optional[str] = None,
        max_results: int = 50,
    ) -> List[Dict[str, str]]:
        """Search the symbol index.

        Returns the same dict format as
        :func:`kicad_lib_importer.search_symbols`.
        """
        if not self._ready.is_set():
            if not self._building:
                self.build_async()
            return []

        query_lower = query.strip().lower()
        if not query_lower:
            return []

        results: List[Dict[str, str]] = []
        for sym in self._symbols:
            if library_name and sym.library != library_name:
                continue
            if query_lower in sym.searchable_norm:
                results.append({
                    "library": sym.library,
                    "name": sym.name,
                    "description": sym.description,
                    "value": sym.value,
                    "footprint": sym.footprint,
                })
                if len(results) >= max_results:
                    break

        return results

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def status(self) -> Dict[str, Any]:
        """Return a JSON-serialisable status dict."""
        return {
            "ready": self._ready.is_set(),
            "building": self._building,
            "symbol_count": len(self._symbols),
            "footprint_count": len(self._footprints),
            "build_time": round(self._build_time, 2),
            "from_cache": self._from_cache,
        }

    # ------------------------------------------------------------------
    # Internal — index building
    # ------------------------------------------------------------------

    def _do_build(self) -> None:
        """Walk all library tables and populate the index."""
        from ..kicad_parser.library import LibraryDiscovery
        from ..kicad_parser.symbol_lib import SymbolLibrary

        disc = LibraryDiscovery(project_dir=self._project_dir)

        # Build into local temporaries so that the existing data (from
        # cache or a previous build) stays available for searches until
        # we swap everything in atomically at the end.
        lib_norms: Dict[str, str] = {}
        lib_tokens: Dict[str, Tuple[str, ...]] = {}

        # --- Footprints (fast — filename scan only) ---
        fp_entries: List[FootprintEntry] = []
        for lib_entry in disc.list_footprint_libraries():
            lib = lib_entry.nickname
            lib_norms[lib] = _normalize(lib)
            lib_tokens[lib] = _tokenize(lib)

            path = disc.resolve_footprint_library(lib)
            if not path:
                continue
            lib_dir = Path(path)
            if not lib_dir.is_dir():
                continue
            for mod_file in lib_dir.glob("*.kicad_mod"):
                name = mod_file.stem
                entry = FootprintEntry(
                    library=lib,
                    name=name,
                    path=str(mod_file),
                    name_norm=_normalize(name),
                    name_tokens=_tokenize(name),
                )
                fp_entries.append(entry)

        # --- Symbols (reads .kicad_sym files / .kicad_symdir dirs) ---
        sym_entries: List[SymbolEntry] = []
        for lib_entry in disc.list_symbol_libraries():
            lib = lib_entry.nickname
            lib_norms.setdefault(lib, _normalize(lib))
            lib_tokens.setdefault(lib, _tokenize(lib))

            path = disc.resolve_symbol_library(lib)
            if not path:
                continue
            p = Path(path)
            if not p.exists():
                continue
            try:
                sym_lib = SymbolLibrary.load(p)
            except Exception:
                logger.debug("Failed to load symbol library %s", lib, exc_info=True)
                continue

            for sym in sym_lib.symbols:
                desc = _prop_val(sym.properties, "Description")
                value = _prop_val(sym.properties, "Value")
                footprint = _prop_val(sym.properties, "Footprint")
                # Build composite searchable string
                searchable = " ".join(filter(None, [
                    sym.name, desc, value, footprint,
                    *(p.value or "" for p in sym.properties),
                ]))
                entry = SymbolEntry(
                    library=lib,
                    name=sym.name,
                    description=desc,
                    value=value,
                    footprint=footprint,
                    name_norm=_normalize(sym.name),
                    name_tokens=_tokenize(sym.name),
                    searchable_norm=searchable.lower(),
                )
                sym_entries.append(entry)

        # --- Atomic swap: replace all data in one go ---
        self._footprints = fp_entries
        self._symbols = sym_entries
        self._lib_norms = lib_norms
        self._lib_tokens = lib_tokens
        self._discovery = disc
        self._from_cache = False

        # Persist the freshly built index to disk for next startup.
        self._save_cache()

    # ------------------------------------------------------------------
    # Internal — footprint scoring
    # ------------------------------------------------------------------

    def _score_fp(
        self,
        fp: FootprintEntry,
        query_norm: str,
        query_tokens: Tuple[str, ...],
        primary: str,
    ) -> int:
        """Score a footprint entry against the query.  0 = no match."""
        name_norm = fp.name_norm
        name_tokens = fp.name_tokens
        lib_norm = self._lib_norms.get(fp.library, "")
        lib_tokens = self._lib_tokens.get(fp.library, ())

        total = len(query_tokens)
        if total == 0:
            return 0

        # Hard filter: primary token must be present
        primary_in_name = primary in name_norm
        primary_in_lib = primary in lib_norm
        if not primary_in_name and not primary_in_lib:
            return 0

        # Exact / substring
        if query_norm == name_norm:
            return 100
        if query_norm in name_norm:
            return 80

        # Token matching
        name_matched = sum(1 for qt in query_tokens if _token_in_any(qt, name_tokens))
        combined = name_tokens + lib_tokens
        combined_matched = sum(1 for qt in query_tokens if _token_in_any(qt, combined))

        if name_matched == total:
            return 60 + (8 if primary_in_name else 0)
        if combined_matched == total:
            score = 50
            if primary_in_name:
                score += 8
            return score

        # Partial
        score = 0
        if combined_matched > 1:
            score = 30
        elif primary_in_name:
            score = 20
        else:
            score = 10

        if primary_in_name:
            score += 8
        non_primary = [qt for qt in query_tokens if qt != primary]
        for qt in non_primary:
            if _token_in_any(qt, lib_tokens):
                score += 4
            if _token_in_any(qt, name_tokens):
                score += 2

        return score


    # ------------------------------------------------------------------
    # Internal — disk cache
    # ------------------------------------------------------------------

    def _cache_key(self) -> str:
        """Return a short hash that distinguishes cache files per project."""
        raw = self._project_dir or "__global__"
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    @staticmethod
    def _cache_dir() -> Path:
        """Return the directory used for library-index cache files."""
        from ..recent_projects import get_config_dir
        d = get_config_dir() / "library_cache"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _cache_path(self) -> Path:
        return self._cache_dir() / f"lib_index_{self._cache_key()}.json"

    def _save_cache(self) -> None:
        """Serialise the current index to a JSON file."""
        try:
            data = {
                "version": _CACHE_VERSION,
                "project_dir": self._project_dir,
                "footprints": [
                    {"library": fp.library, "name": fp.name, "path": fp.path}
                    for fp in self._footprints
                ],
                "symbols": [
                    {
                        "library": s.library,
                        "name": s.name,
                        "description": s.description,
                        "value": s.value,
                        "footprint": s.footprint,
                    }
                    for s in self._symbols
                ],
            }
            tmp = self._cache_path().with_suffix(".tmp")
            tmp.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
            tmp.replace(self._cache_path())  # atomic on most OSes
            logger.info(
                "Library index cache saved (%d symbols, %d footprints) -> %s",
                len(self._symbols), len(self._footprints), self._cache_path(),
            )
        except Exception:
            logger.debug("Failed to save library index cache", exc_info=True)

    def _load_cache(self) -> None:
        """Load a previously saved cache from disk.

        If successful the index is immediately marked *ready* so that
        searches can proceed while the live build runs in the background.
        """
        path = self._cache_path()
        if not path.exists():
            return
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if raw.get("version") != _CACHE_VERSION:
                logger.info("Stale library cache version — ignoring")
                return

            fp_entries: List[FootprintEntry] = []
            for item in raw.get("footprints", []):
                name = item["name"]
                lib = item["library"]
                entry = FootprintEntry(
                    library=lib,
                    name=name,
                    path=item["path"],
                    name_norm=_normalize(name),
                    name_tokens=_tokenize(name),
                )
                fp_entries.append(entry)
                self._lib_norms.setdefault(lib, _normalize(lib))
                self._lib_tokens.setdefault(lib, _tokenize(lib))

            sym_entries: List[SymbolEntry] = []
            for item in raw.get("symbols", []):
                desc = item.get("description", "")
                value = item.get("value", "")
                footprint = item.get("footprint", "")
                searchable = " ".join(filter(None, [
                    item["name"], desc, value, footprint,
                ]))
                entry = SymbolEntry(
                    library=item["library"],
                    name=item["name"],
                    description=desc,
                    value=value,
                    footprint=footprint,
                    name_norm=_normalize(item["name"]),
                    name_tokens=_tokenize(item["name"]),
                    searchable_norm=searchable.lower(),
                )
                sym_entries.append(entry)

            self._footprints = fp_entries
            self._symbols = sym_entries
            self._from_cache = True
            self._ready.set()
            logger.info(
                "Library index loaded from cache (%d symbols, %d footprints)",
                len(self._symbols), len(self._footprints),
            )
        except Exception:
            logger.debug("Failed to load library index cache", exc_info=True)


# ---------------------------------------------------------------------------
# Private utilities
# ---------------------------------------------------------------------------

def _prop_val(props, name: str) -> str:
    for p in props:
        if p.key == name:
            return p.value or ""
    return ""
