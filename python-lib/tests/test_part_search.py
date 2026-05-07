"""Tests for ``part_search`` and ``part_find_existing`` MCP tools.

External services (Octopart, JLCPCB, DuckDuckGo) are mocked; these tests
must run hermetically with no network access.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Dict
from unittest import mock

import pytest

from kiassist_utils.importer import part_search as ps
from kiassist_utils.mcp_server import in_process_call


def _call(tool: str, **kwargs: Any) -> Dict[str, Any]:
    return asyncio.run(in_process_call(tool, kwargs))


@pytest.fixture(autouse=True)
def _reset_session_store():
    ps.DEFAULT_SESSION_STORE.reset()
    yield
    ps.DEFAULT_SESSION_STORE.reset()


# ---------------------------------------------------------------------------
# extract_mpn_hints
# ---------------------------------------------------------------------------


class TestExtractMpnHints:
    def test_extracts_likely_mpns(self):
        text = "ADS1256IDBT and the LTC2440CGN are both 24-bit ADCs from TI."
        hints = ps.extract_mpn_hints(text)
        assert "ADS1256IDBT" in hints
        assert "LTC2440CGN" in hints

    def test_filters_acronyms_and_words(self):
        text = "USB ADC SPI MCU GPIO datasheet"
        hints = ps.extract_mpn_hints(text)
        # All of these are stop-words or have no digits
        assert hints == []

    def test_dedupes_case_insensitive(self):
        text = "ads1256 ADS1256 Ads1256"
        hints = ps.extract_mpn_hints(text)
        assert hints.count("ADS1256") == 1

    def test_empty_input(self):
        assert ps.extract_mpn_hints("") == []
        assert ps.extract_mpn_hints(None) == []  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# PartSearchSession
# ---------------------------------------------------------------------------


class TestPartSearchSession:
    def test_start_returns_unique_ids(self):
        store = ps.PartSearchSession()
        a = store.start()
        b = store.start()
        assert a.search_id != b.search_id

    def test_get_returns_existing_session(self):
        store = ps.PartSearchSession()
        s = store.start()
        assert store.get(s.search_id) is s

    def test_get_unknown_returns_none(self):
        store = ps.PartSearchSession()
        assert store.get("does-not-exist") is None


# ---------------------------------------------------------------------------
# part_search MCP tool
# ---------------------------------------------------------------------------


class TestPartSearchTool:
    def test_rejects_empty_specs(self):
        result = _call("part_search", specs="")
        assert result["status"] == "error"

    def test_rejects_bad_limit(self):
        result = _call("part_search", specs="adc", limit=0)
        assert result["status"] == "error"
        result = _call("part_search", specs="adc", limit=999)
        assert result["status"] == "error"

    def test_rejects_non_list_candidate_mpns(self):
        # Pydantic validates the schema at the MCP layer and raises a
        # ToolError before our code runs — that's still a rejection.
        with pytest.raises(Exception):
            _call("part_search", specs="adc", candidate_mpns="ADS1256")  # type: ignore[arg-type]

    def test_first_call_runs_web_search_when_no_mpns(self):
        fake_results = [
            {"title": "ADS1256 24-bit ADC", "url": "https://ti.com/x",
             "snippet": "ADS1256IDBT 24-bit ADC SPI"},
            {"title": "LTC2440 datasheet", "url": "https://analog.com/y",
             "snippet": "LTC2440CGN delta-sigma ADC"},
        ]

        def fake_web(query, max_results):
            assert "adc" in query.lower()
            assert "datasheet" in query.lower()
            return fake_results

        with mock.patch(
            "kiassist_utils.web_search.web_search",
            side_effect=lambda q, max_results=8: fake_web(q, max_results),
        ):
            result = _call("part_search", specs="24-bit ADC SPI")

        assert result["status"] == "ok"
        d = result["data"]
        assert d["candidates"] == []
        assert d["web_results"] == fake_results
        assert d["needs_mpn_extraction"] is True
        assert "ADS1256IDBT" in d["mpn_hints"]
        assert d["search_id"]

    def test_second_call_enriches_candidates_via_lookup(self):
        # Pre-create a session so we can confirm reuse.
        first = _call(
            "part_search", specs="24-bit ADC",
        )
        sid = first["data"]["search_id"]

        fake_lookup_results = {
            "ADS1256IDBT": {
                "found": True,
                "mpn": "ADS1256IDBT",
                "manufacturer": "Texas Instruments",
                "description": "24-bit Delta-Sigma ADC",
                "datasheet": "https://ti.com/ads1256.pdf",
                "digikey_pn": "296-1234-ND",
                "lcsc_pn": "C12345",
                "mouser_pn": "",
                "slug": "ti-ads1256idbt",
                "sellers": {
                    "digikey": {
                        "sku": "296-1234-ND",
                        "click_url": "https://www.digikey.com/short/abc",
                        "packaging": "Cut Tape",
                    }
                },
            },
            "FAKE-PART-9999": {"found": False},
        }

        def fake_lookup(mpn):
            return fake_lookup_results.get(mpn, {"found": False})

        with mock.patch(
            "kiassist_utils.importer.part_lookup.lookup_part",
            side_effect=fake_lookup,
        ):
            result = _call(
                "part_search",
                specs="24-bit ADC",
                candidate_mpns=["ADS1256IDBT", "FAKE-PART-9999"],
                search_id=sid,
                refine="cheaper",
            )

        assert result["status"] == "ok"
        d = result["data"]
        assert d["search_id"] == sid
        assert d["turn"] == 2
        assert d["refine"] == "cheaper"
        assert len(d["candidates"]) == 2
        ads = d["candidates"][0]
        assert ads["mpn"] == "ADS1256IDBT"
        assert ads["manufacturer"] == "Texas Instruments"
        assert ads["datasheet_url"] == "https://ti.com/ads1256.pdf"
        # Product URL prefers DigiKey click_url.
        assert ads["product_url"] == "https://www.digikey.com/short/abc"
        assert ads["verified"] is True
        # Unverified card carries a warning instead of fake data.
        fake = d["candidates"][1]
        assert fake["mpn"] == "FAKE-PART-9999"
        assert fake["verified"] is False
        assert fake["warnings"]
        # verified_all should reflect the worst case.
        assert d["verified_all"] is False

    def test_unknown_search_id_starts_new_session(self):
        with mock.patch(
            "kiassist_utils.web_search.web_search",
            return_value=[],
        ):
            result = _call(
                "part_search",
                specs="opamp",
                search_id="nonexistent-id-1234",
            )
        assert result["status"] == "ok"
        # A fresh session id was minted because the supplied one wasn't found.
        assert result["data"]["search_id"] != "nonexistent-id-1234"
        assert result["data"]["turn"] == 1

    def test_session_persists_across_turns(self):
        first = _call("part_search", specs="adc")
        sid = first["data"]["search_id"]
        second = _call(
            "part_search",
            specs="adc",
            candidate_mpns=[],
            search_id=sid,
        )
        # When candidate_mpns is empty list (falsy) and we re-supply the
        # sid, the second call should reuse the session and increment turn.
        with mock.patch(
            "kiassist_utils.web_search.web_search",
            return_value=[],
        ):
            third = _call("part_search", specs="adc", search_id=sid)
        assert third["data"]["search_id"] == sid
        assert third["data"]["turn"] >= 2

    def test_candidates_dedupe_case_insensitive(self):
        with mock.patch(
            "kiassist_utils.importer.part_lookup.lookup_part",
            return_value={"found": False},
        ) as m:
            result = _call(
                "part_search",
                specs="adc",
                candidate_mpns=["ADS1256", "ads1256", "ADS1256"],
            )
        assert result["status"] == "ok"
        # Only one unique lookup despite three inputs.
        assert m.call_count == 1
        assert len(result["data"]["candidates"]) == 1


# ---------------------------------------------------------------------------
# part_find_existing
# ---------------------------------------------------------------------------


def _make_minimal_kicad_sym(tmp_path: Path, name: str, mpn: str = "") -> Path:
    """Write a minimal but parseable .kicad_sym file containing one symbol."""
    mpn_prop = f'(property "MPN" "{mpn}" (at 0 0 0) (effects (font (size 1.27 1.27))))' if mpn else ""
    content = f"""(kicad_symbol_lib
  (version 20231120)
  (generator "test")
  (symbol "{name}"
    (pin_numbers hide)
    (pin_names (offset 1.016))
    (in_bom yes)
    (on_board yes)
    (property "Reference" "U" (at 0 5 0) (effects (font (size 1.27 1.27))))
    (property "Value" "{name}" (at 0 0 0) (effects (font (size 1.27 1.27))))
    (property "Description" "24-bit Delta-Sigma ADC SPI" (at 0 -5 0) (effects (font (size 1.27 1.27))))
    {mpn_prop}
  )
)
"""
    p = tmp_path / "kiassist_imports.kicad_sym"
    p.write_text(content, encoding="utf-8")
    return p


def _make_minimal_sch(tmp_path: Path, value: str) -> Path:
    """Write a minimal .kicad_sch with one symbol whose Value is *value*."""
    content = f"""(kicad_sch (version 20231120) (generator "test") (uuid "00000000-0000-0000-0000-000000000001")
  (paper "A4")
  (symbol (lib_id "Device:R") (at 100 100 0) (uuid "11111111-1111-1111-1111-111111111111")
    (property "Reference" "U1" (at 100 95 0))
    (property "Value" "{value}" (at 100 105 0))
    (property "Footprint" "Package_SO:SOIC-28W" (at 100 110 0))
  )
)
"""
    p = tmp_path / "test.kicad_sch"
    p.write_text(content, encoding="utf-8")
    return p


def _make_lib_table(tmp_path: Path, sym_lib: Path) -> Path:
    """Write a project-local sym-lib-table that points at *sym_lib*."""
    content = f"""(sym_lib_table
  (version 7)
  (lib (name "kiassist_imports")(type "KiCad")(uri "{sym_lib}")(options "")(descr "Imported parts"))
)
"""
    p = tmp_path / "sym-lib-table"
    p.write_text(content, encoding="utf-8")
    return p


class TestFindExistingParts:
    def test_rejects_missing_project(self, tmp_path: Path):
        result = _call(
            "part_find_existing",
            project_path=str(tmp_path / "nope"),
            query="adc",
        )
        assert result["status"] == "error"

    def test_rejects_empty_query(self, tmp_path: Path):
        result = _call(
            "part_find_existing",
            project_path=str(tmp_path),
            query="",
        )
        assert result["status"] == "error"

    def test_rejects_path_traversal(self):
        result = _call(
            "part_find_existing",
            project_path="../escape",
            query="adc",
        )
        assert result["status"] == "error"

    def test_finds_match_in_schematic_bom(self, tmp_path: Path):
        _make_minimal_sch(tmp_path, value="ADS1256IDBT")
        result = _call(
            "part_find_existing",
            project_path=str(tmp_path),
            query="ADS1256",
        )
        assert result["status"] == "ok"
        d = result["data"]
        assert d["has_matches"] is True
        assert any(
            m["value"] == "ADS1256IDBT" for m in d["schematic_matches"]
        )

    def test_finds_match_in_imported_library(self, tmp_path: Path):
        sym_lib = _make_minimal_kicad_sym(tmp_path, "ADS1256IDBT", mpn="ADS1256IDBT")
        _make_lib_table(tmp_path, sym_lib)
        result = _call(
            "part_find_existing",
            project_path=str(tmp_path),
            query="24-bit ADC",
        )
        assert result["status"] == "ok"
        d = result["data"]
        assert d["has_matches"] is True
        ids = [m["lib_id"] for m in d["library_matches"]]
        assert "kiassist_imports:ADS1256IDBT" in ids

    def test_no_matches_returns_empty_lists(self, tmp_path: Path):
        _make_minimal_sch(tmp_path, value="LM358")
        result = _call(
            "part_find_existing",
            project_path=str(tmp_path),
            query="totally unrelated query xyzzy",
        )
        assert result["status"] == "ok"
        assert result["data"]["has_matches"] is False
        assert result["data"]["schematic_matches"] == []
        assert result["data"]["library_matches"] == []
