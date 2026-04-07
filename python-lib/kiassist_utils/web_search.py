"""Web search utilities for KiAssist.

Provides a lightweight web search function using only Python stdlib
(``urllib.request`` and ``html.parser``) – no extra dependencies required.

The primary backend is DuckDuckGo's HTML endpoint; a simpler ``lite``
fallback is attempted when the main endpoint fails or returns no results.
"""

from __future__ import annotations

import logging
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DDG_HTML_URL = "https://html.duckduckgo.com/html/"
_DDG_LITE_URL = "https://lite.duckduckgo.com/lite/"
_REQUEST_TIMEOUT = 10  # seconds
_MAX_RESULTS = 8

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "en-US,en;q=0.9",
}


# ---------------------------------------------------------------------------
# HTML parser for DuckDuckGo HTML results
# ---------------------------------------------------------------------------


class _DDGResultParser(HTMLParser):
    """Extract search result titles, URLs, and snippets from DDG HTML output.

    DuckDuckGo HTML search wraps each result in a ``<div class="result">``
    (main endpoint) or ``<tr>`` with ``<td>`` cells (lite endpoint).  This
    parser handles both layouts.
    """

    def __init__(self) -> None:
        super().__init__()
        self.results: List[Dict[str, str]] = []

        # State for the main DDG HTML endpoint
        self._in_result: bool = False
        self._current: Dict[str, str] = {}
        self._capture_title: bool = False
        self._capture_snippet: bool = False
        # Track <div> nesting depth so we know when we've left the result div
        self._div_depth: int = 0

        # State for the DDG lite endpoint (table layout)
        self._in_lite_td_title: bool = False
        self._in_lite_td_snippet: bool = False

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _class_contains(self, attrs: List, fragment: str) -> bool:
        """Return True if any ``class`` attribute value contains *fragment*."""
        for name, val in attrs:
            if name == "class" and val and fragment in val:
                return True
        return False

    def _attr(self, attrs: List, name: str) -> Optional[str]:
        """Return the value of attribute *name*, or None."""
        for k, v in attrs:
            if k == name:
                return v
        return None

    def _flush_current(self) -> None:
        """Persist the current result if it has at minimum a title."""
        if self._current.get("title"):
            self.results.append(dict(self._current))
        self._current = {}

    # ------------------------------------------------------------------
    # HTMLParser overrides
    # ------------------------------------------------------------------

    def handle_starttag(self, tag: str, attrs: List) -> None:  # type: ignore[override]
        tag = tag.lower()

        # --- Main DDG HTML endpoint ---
        if tag == "div":
            if self._class_contains(attrs, "result__body"):
                # Starting a new result: flush any in-progress result first.
                if self._in_result:
                    self._flush_current()
                self._in_result = True
                self._div_depth = 1  # We are now 1 <div> deep
            elif self._in_result:
                # Nested <div> inside the current result
                self._div_depth += 1

        if self._in_result:
            if tag == "a" and self._class_contains(attrs, "result__a"):
                href = self._attr(attrs, "href")
                if href and href.startswith("//"):
                    href = "https:" + href
                if href:
                    self._current["url"] = href
                self._capture_title = True

            elif tag == "a" and self._class_contains(attrs, "result__snippet"):
                self._capture_snippet = True

        # --- DDG Lite endpoint (table layout) ---
        if tag == "td" and self._class_contains(attrs, "result-title"):
            self._in_lite_td_title = True

        if tag == "a" and self._in_lite_td_title:
            href = self._attr(attrs, "href")
            if href:
                if href.startswith("//"):
                    href = "https:" + href
                self._current["url"] = href

        if tag == "td" and self._class_contains(attrs, "result-snippet"):
            self._in_lite_td_snippet = True

    def handle_endtag(self, tag: str) -> None:  # type: ignore[override]
        tag = tag.lower()

        if tag == "a":
            if self._capture_title:
                self._capture_title = False
            if self._capture_snippet:
                self._capture_snippet = False
            if self._in_lite_td_title:
                self._in_lite_td_title = False

        if tag == "td":
            if self._in_lite_td_snippet:
                self._in_lite_td_snippet = False
                if self._current.get("title"):
                    self._flush_current()

        if tag == "div" and self._in_result:
            self._div_depth -= 1
            if self._div_depth <= 0:
                self._flush_current()
                self._in_result = False
                self._div_depth = 0
                self._capture_title = False
                self._capture_snippet = False

    def handle_data(self, data: str) -> None:  # type: ignore[override]
        data = data.strip()
        if not data:
            return

        if self._capture_title:
            self._current["title"] = self._current.get("title", "") + data

        elif self._capture_snippet:
            self._current["snippet"] = self._current.get("snippet", "") + " " + data

        elif self._in_lite_td_title and self._current.get("url"):
            self._current["title"] = self._current.get("title", "") + data

        elif self._in_lite_td_snippet:
            self._current["snippet"] = self._current.get("snippet", "") + " " + data

    def error(self, message: str) -> None:  # type: ignore[override]
        logger.debug("HTML parser error: %s", message)


# ---------------------------------------------------------------------------
# Public search function
# ---------------------------------------------------------------------------


def web_search(
    query: str,
    max_results: int = _MAX_RESULTS,
) -> List[Dict[str, str]]:
    """Search the web using DuckDuckGo and return structured results.

    Uses only Python stdlib (``urllib``, ``html.parser``) – no third-party
    dependencies required.

    Args:
        query:       Search query string.
        max_results: Maximum number of results to return (default 8).

    Returns:
        List of dicts, each with ``title``, ``url``, and optionally
        ``snippet`` keys.  An empty list is returned when the search
        fails or yields no results.
    """
    results = _search_ddg_html(query, max_results)
    if not results:
        results = _search_ddg_lite(query, max_results)
    return results[:max_results]


# ---------------------------------------------------------------------------
# Backend implementations
# ---------------------------------------------------------------------------


def _search_ddg_html(
    query: str, max_results: int
) -> List[Dict[str, str]]:
    """Query the main DuckDuckGo HTML endpoint."""
    try:
        data = urllib.parse.urlencode({"q": query, "b": "", "kl": "us-en"}).encode()
        req = urllib.request.Request(_DDG_HTML_URL, data=data, headers=_HEADERS)
        with urllib.request.urlopen(req, timeout=_REQUEST_TIMEOUT) as resp:
            html = resp.read().decode("utf-8", errors="replace")

        parser = _DDGResultParser()
        parser.feed(html)
        return [r for r in parser.results if r.get("title")][:max_results]
    except Exception as exc:
        logger.debug("DDG HTML search failed: %s", exc)
        return []


def _search_ddg_lite(
    query: str, max_results: int
) -> List[Dict[str, str]]:
    """Query the DuckDuckGo Lite HTML endpoint (simpler markup)."""
    try:
        data = urllib.parse.urlencode({"q": query, "kl": "us-en"}).encode()
        req = urllib.request.Request(_DDG_LITE_URL, data=data, headers=_HEADERS)
        with urllib.request.urlopen(req, timeout=_REQUEST_TIMEOUT) as resp:
            html = resp.read().decode("utf-8", errors="replace")

        parser = _DDGResultParser()
        parser.feed(html)
        return [r for r in parser.results if r.get("title")][:max_results]
    except Exception as exc:
        logger.debug("DDG Lite search failed: %s", exc)
        return []


# ---------------------------------------------------------------------------
# Component-search prompt builder
# ---------------------------------------------------------------------------


def build_component_search_prompt(
    user_query: str,
    search_results: List[Dict[str, str]],
) -> str:
    """Build an AI prompt for synthesizing component search results.

    Args:
        user_query:     The original natural-language component request.
        search_results: List of dicts from :func:`web_search`.

    Returns:
        Prompt string ready to send to an AI provider.
    """
    if search_results:
        results_text = "\n".join(
            f"[{i + 1}] {r.get('title', 'Untitled')}\n"
            f"    URL: {r.get('url', '')}\n"
            f"    Snippet: {r.get('snippet', 'No description').strip()}"
            for i, r in enumerate(search_results)
        )
    else:
        results_text = "(No web search results were available.)"

    return (
        "You are a knowledgeable electronics engineer assistant. "
        "A user is searching for a suitable electronic component.\n\n"
        f"**User request:** {user_query}\n\n"
        f"**Web search results:**\n{results_text}\n\n"
        "Based on the user request and the search results above, provide a concise, "
        "helpful response that:\n"
        "1. Recommends 2–4 specific components that best match the requirements.\n"
        "2. For each component, lists the key specifications and features relevant "
        "to the user's use case.\n"
        "3. Notes any important trade-offs or compatibility considerations.\n"
        "4. Keeps the response focused and practical for a PCB design context.\n\n"
        "Format the response in clear Markdown with component names as headings."
    )
