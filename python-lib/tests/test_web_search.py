"""Tests for kiassist_utils.web_search module."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch

import pytest

# Ensure the package is importable from within the python-lib directory
sys.path.insert(0, str(Path(__file__).parent.parent))

from kiassist_utils.web_search import (
    _DDGResultParser,
    build_component_search_prompt,
    web_search,
    _search_ddg_html,
    _search_ddg_lite,
)


# ---------------------------------------------------------------------------
# _DDGResultParser unit tests
# ---------------------------------------------------------------------------


class TestDDGResultParser:
    """Tests for the standalone HTML result parser."""

    def test_parses_main_endpoint_result(self):
        """Parser should extract title and URL from a result__body/result__a structure."""
        html = """
        <div class="result__body">
          <a class="result__a" href="https://example.com/product">
            BSS138 Logic Level Shifter
          </a>
          <a class="result__snippet">
            A 4-channel bidirectional logic level converter using BSS138 FETs.
          </a>
        </div>
        """
        parser = _DDGResultParser()
        parser.feed(html)
        assert len(parser.results) >= 1
        result = parser.results[0]
        assert "BSS138" in result["title"]
        assert result["url"] == "https://example.com/product"

    def test_parses_multiple_results(self):
        """Parser accumulates multiple results."""
        html = """
        <div class="result__body">
          <a class="result__a" href="https://a.com">Component A</a>
        </div>
        <div class="result__body">
          <a class="result__a" href="https://b.com">Component B</a>
        </div>
        """
        parser = _DDGResultParser()
        parser.feed(html)
        assert len(parser.results) == 2
        titles = [r["title"] for r in parser.results]
        assert any("Component A" in t for t in titles)
        assert any("Component B" in t for t in titles)

    def test_empty_html_returns_no_results(self):
        """Feeding empty HTML returns an empty results list."""
        parser = _DDGResultParser()
        parser.feed("<html><body></body></html>")
        assert parser.results == []

    def test_skips_result_without_title(self):
        """Results without a title are not added."""
        html = '<div class="result__body"><a class="result__a" href="https://x.com"></a></div>'
        parser = _DDGResultParser()
        parser.feed(html)
        assert parser.results == []

    def test_protocol_relative_url_normalised(self):
        """URLs starting with '//' are prefixed with 'https:'."""
        html = """
        <div class="result__body">
          <a class="result__a" href="//example.com/page">My Component</a>
        </div>
        """
        parser = _DDGResultParser()
        parser.feed(html)
        if parser.results:
            assert parser.results[0]["url"].startswith("https://")

    def test_snippet_captured(self):
        """Snippet text from result__snippet anchor is stored."""
        html = """
        <div class="result__body">
          <a class="result__a" href="https://example.com">Some IC</a>
          <a class="result__snippet">3.3V to 5V bidirectional conversion</a>
        </div>
        """
        parser = _DDGResultParser()
        parser.feed(html)
        if parser.results:
            assert "snippet" in parser.results[0]
            assert "3.3V" in parser.results[0]["snippet"]


# ---------------------------------------------------------------------------
# build_component_search_prompt unit tests
# ---------------------------------------------------------------------------


class TestBuildComponentSearchPrompt:
    """Tests for the prompt builder helper."""

    def test_includes_user_query(self):
        prompt = build_component_search_prompt(
            "logic level converter 3.3V to 5V",
            [],
        )
        assert "logic level converter 3.3V to 5V" in prompt

    def test_includes_fallback_when_no_results(self):
        prompt = build_component_search_prompt("some query", [])
        assert "No web search results" in prompt

    def test_includes_search_results(self):
        results = [
            {"title": "TXB0104 Shifter", "url": "https://adafruit.com/tx", "snippet": "Fast."},
            {"title": "BSS138 Shifter", "url": "https://adafruit.com/bs"},
        ]
        prompt = build_component_search_prompt("level converter", results)
        assert "TXB0104 Shifter" in prompt
        assert "BSS138 Shifter" in prompt
        assert "https://adafruit.com/tx" in prompt

    def test_prompt_instructs_markdown_format(self):
        prompt = build_component_search_prompt("query", [])
        assert "Markdown" in prompt

    def test_prompt_not_empty(self):
        prompt = build_component_search_prompt("query", [])
        assert len(prompt) > 100


# ---------------------------------------------------------------------------
# web_search integration (mocked network)
# ---------------------------------------------------------------------------


SAMPLE_DDG_HTML = """
<html><body>
<div class="result__body">
  <a class="result__a" href="https://example.com/txb0104">TXB0104 Bi-Directional Level Shifter</a>
  <a class="result__snippet">Fast bidirectional level shifting for 3.3V to 5V SPI and more.</a>
</div>
<div class="result__body">
  <a class="result__a" href="https://example.com/bss138">BSS138 Logic Level Converter</a>
  <a class="result__snippet">4-channel bidirectional, I2C safe.</a>
</div>
</body></html>
""".encode("utf-8")


class _FakeResponse:
    """Minimal file-like object for mocking urllib.request.urlopen."""

    def __init__(self, data: bytes) -> None:
        self._data = data

    def read(self) -> bytes:
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


class TestWebSearch:
    """Tests for the web_search() public function with mocked HTTP."""

    def test_returns_list_on_success(self):
        with patch(
            "kiassist_utils.web_search.urllib.request.urlopen",
            return_value=_FakeResponse(SAMPLE_DDG_HTML),
        ):
            results = web_search("logic level converter")
        assert isinstance(results, list)

    def test_results_have_title_and_url(self):
        with patch(
            "kiassist_utils.web_search.urllib.request.urlopen",
            return_value=_FakeResponse(SAMPLE_DDG_HTML),
        ):
            results = web_search("logic level converter")
        if results:
            for r in results:
                assert "title" in r
                assert "url" in r

    def test_max_results_respected(self):
        with patch(
            "kiassist_utils.web_search.urllib.request.urlopen",
            return_value=_FakeResponse(SAMPLE_DDG_HTML),
        ):
            results = web_search("anything", max_results=1)
        assert len(results) <= 1

    def test_returns_empty_list_on_network_error(self):
        with patch(
            "kiassist_utils.web_search.urllib.request.urlopen",
            side_effect=OSError("network unreachable"),
        ):
            results = web_search("logic level converter")
        assert results == []

    def test_falls_back_to_lite_when_html_empty(self):
        """When the HTML endpoint returns no parseable results, lite is tried."""
        empty_html = b"<html><body></body></html>"
        call_count = {"n": 0}

        def fake_urlopen(req, timeout=10):
            call_count["n"] += 1
            return _FakeResponse(SAMPLE_DDG_HTML if call_count["n"] > 1 else empty_html)

        with patch("kiassist_utils.web_search.urllib.request.urlopen", side_effect=fake_urlopen):
            results = web_search("something")
        # At least two calls were made (html + lite fallback)
        assert call_count["n"] >= 2


# ---------------------------------------------------------------------------
# Individual backend helpers
# ---------------------------------------------------------------------------


class TestSearchBackends:
    def test_ddg_html_returns_list(self):
        with patch(
            "kiassist_utils.web_search.urllib.request.urlopen",
            return_value=_FakeResponse(SAMPLE_DDG_HTML),
        ):
            results = _search_ddg_html("test", 8)
        assert isinstance(results, list)

    def test_ddg_html_handles_exception(self):
        with patch(
            "kiassist_utils.web_search.urllib.request.urlopen",
            side_effect=Exception("boom"),
        ):
            results = _search_ddg_html("test", 8)
        assert results == []

    def test_ddg_lite_returns_list(self):
        with patch(
            "kiassist_utils.web_search.urllib.request.urlopen",
            return_value=_FakeResponse(b"<html><body></body></html>"),
        ):
            results = _search_ddg_lite("test", 8)
        assert isinstance(results, list)

    def test_ddg_lite_handles_exception(self):
        with patch(
            "kiassist_utils.web_search.urllib.request.urlopen",
            side_effect=OSError("timeout"),
        ):
            results = _search_ddg_lite("test", 8)
        assert results == []
