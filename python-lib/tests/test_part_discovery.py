"""Tests for ``part_lookup`` and ``part_import`` MCP tools.

External HTTP services (Octopart, JLCPCB, EasyEDA) are mocked; these tests
must run hermetically with no network access.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Dict
from unittest import mock

import pytest

from kiassist_utils.importer import models as importer_models
from kiassist_utils.importer.models import (
    FieldSet,
    ImportedComponent,
    ImportMethod,
    ImportResult,
)
from kiassist_utils.mcp_server import in_process_call


def _call(tool: str, **kwargs: Any) -> Dict[str, Any]:
    return asyncio.run(in_process_call(tool, kwargs))


# ---------------------------------------------------------------------------
# part_lookup
# ---------------------------------------------------------------------------


class TestPartLookup:
    def test_rejects_empty_query(self):
        result = _call("part_lookup", query="")
        assert result["status"] == "error"

    def test_mpn_query_uses_octopart(self):
        fake_octo = {
            "found": True,
            "mpn": "STM32F103C8T6",
            "manufacturer": "STMicroelectronics",
            "description": "ARM Cortex-M3 MCU",
            "datasheet": "https://example.com/ds.pdf",
            "digikey_pn": "497-6063-1-ND",
            "lcsc_pn": "C8734",
            "mouser_pn": "511-STM32F103C8T6",
            "sellers": {},
            "slug": "stmicroelectronics-stm32f103c8t6",
        }
        with mock.patch(
            "kiassist_utils.importer.part_lookup.lookup_part",
            return_value=fake_octo,
        ) as m:
            result = _call("part_lookup", query="STM32F103C8T6")
        assert result["status"] == "ok"
        assert result["data"]["mpn"] == "STM32F103C8T6"
        assert result["data"]["digikey_pn"] == "497-6063-1-ND"
        m.assert_called_once_with("STM32F103C8T6")

    def test_lcsc_query_resolves_via_jlcpcb_first(self):
        fake_jlc = {
            "found": True,
            "mpn": "STM32F103C8T6",
            "brand": "STMicroelectronics",
            "description": "MCU",
            "datasheet": "https://example.com/ds.pdf",
            "package": "LQFP-48",
        }
        fake_octo = {
            "found": True,
            "mpn": "STM32F103C8T6",
            "manufacturer": "STMicroelectronics",
            "description": "ARM Cortex-M3 MCU",
            "datasheet": "https://example.com/ds.pdf",
            "digikey_pn": "497-6063-1-ND",
            "lcsc_pn": "",
            "mouser_pn": "",
            "sellers": {},
        }
        with mock.patch(
            "kiassist_utils.importer.part_lookup._jlcpcb_search",
            return_value=fake_jlc,
        ) as jlc_m, mock.patch(
            "kiassist_utils.importer.part_lookup.lookup_part",
            return_value=fake_octo,
        ) as oc_m:
            result = _call("part_lookup", query="C8734")
        assert result["status"] == "ok"
        assert result["data"]["found"] is True
        assert result["data"]["mpn"] == "STM32F103C8T6"
        # Octopart lookup was called with the MPN resolved by JLCPCB,
        # never with the raw "C8734".
        jlc_m.assert_called_once_with("C8734")
        oc_m.assert_called_once_with("STM32F103C8T6")
        # LCSC PN should be carried through even though Octopart didn't return one.
        assert result["data"]["lcsc_pn"] == "C8734"

    def test_lcsc_query_falls_back_to_jlcpcb_only(self):
        fake_jlc = {
            "found": True,
            "mpn": "OBSCUREPART",
            "brand": "Obscure",
            "description": "weird",
            "datasheet": "",
            "package": "",
        }
        with mock.patch(
            "kiassist_utils.importer.part_lookup._jlcpcb_search",
            return_value=fake_jlc,
        ), mock.patch(
            "kiassist_utils.importer.part_lookup.lookup_part",
            return_value={"found": False},
        ):
            result = _call("part_lookup", query="C99999")
        assert result["status"] == "ok"
        d = result["data"]
        assert d["found"] is True
        assert d["mpn"] == "OBSCUREPART"
        assert d["lcsc_pn"] == "C99999"

    def test_lcsc_query_not_found(self):
        with mock.patch(
            "kiassist_utils.importer.part_lookup._jlcpcb_search",
            return_value={"found": False},
        ):
            result = _call("part_lookup", query="C0000000")
        assert result["status"] == "ok"
        assert result["data"]["found"] is False


# ---------------------------------------------------------------------------
# part_import
# ---------------------------------------------------------------------------


class TestPartImport:
    def test_rejects_no_identifier(self):
        result = _call("part_import")
        assert result["status"] == "error"

    def test_rejects_path_traversal_in_fp_dir(self, tmp_path: Path):
        result = _call(
            "part_import",
            mpn="STM32",
            target_fp_lib_dir="../escape.pretty",
        )
        assert result["status"] == "error"
        assert "traversal" in result["message"]

    def test_dry_run_returns_lookup_only(self):
        # No target lib supplied → no commit_import call.
        fake_component = ImportedComponent(
            name="STM32F103C8T6",
            fields=FieldSet(
                mpn="STM32F103C8T6",
                manufacturer="STMicroelectronics",
                lcsc_pn="C8734",
                digikey_pn="497-6063-1-ND",
                datasheet="https://example.com/ds.pdf",
            ),
            symbol_sexpr="(symbol ...)",
            footprint_sexpr="(footprint ...)",
            import_method=ImportMethod.LCSC,
        )
        fake_result = ImportResult(
            success=True, component=fake_component, warnings=["test warning"]
        )
        with mock.patch(
            "kiassist_utils.importer.part_lookup.import_by_part",
            return_value=fake_result,
        ), mock.patch(
            "kiassist_utils.importer.library_writer.commit_import"
        ) as commit_m:
            result = _call("part_import", mpn="STM32F103C8T6")
        assert result["status"] == "ok"
        assert result["data"]["success"] is True
        assert result["data"]["mpn"] == "STM32F103C8T6"
        assert "test warning" in result["data"]["warnings"]
        # Without target paths we must not write anything.
        commit_m.assert_not_called()
        assert "lib_id" not in result["data"]

    def test_writes_to_target_libraries(self, tmp_path: Path):
        sym_lib = tmp_path / "imports.kicad_sym"
        fp_dir = tmp_path / "imports.pretty"

        fake_component = ImportedComponent(
            name="STM32F103C8T6",
            fields=FieldSet(mpn="STM32F103C8T6", manufacturer="ST"),
            symbol_sexpr="(symbol ...)",
            footprint_sexpr="(footprint ...)",
            import_method=ImportMethod.LCSC,
        )
        fake_lookup = ImportResult(
            success=True, component=fake_component, warnings=[]
        )

        def fake_commit(component, *, target_sym_lib, target_fp_lib_dir, **kw):
            # Simulate the writer's contract — set the result paths.
            component.symbol_path = Path(target_sym_lib)
            component.footprint_path = Path(target_fp_lib_dir) / f"{component.name}.kicad_mod"
            component.model_paths = []
            return ImportResult(
                success=True,
                component=component,
                warnings=["committed"],
            )

        with mock.patch(
            "kiassist_utils.importer.part_lookup.import_by_part",
            return_value=fake_lookup,
        ), mock.patch(
            "kiassist_utils.importer.library_writer.commit_import",
            side_effect=fake_commit,
        ):
            result = _call(
                "part_import",
                mpn="STM32F103C8T6",
                target_sym_lib=str(sym_lib),
                target_fp_lib_dir=str(fp_dir),
            )
        assert result["status"] == "ok", result
        d = result["data"]
        assert d["success"] is True
        assert d["lib_id"] == "imports:STM32F103C8T6"
        assert d["symbol_path"].endswith("imports.kicad_sym")
        assert d["footprint_path"].endswith("STM32F103C8T6.kicad_mod")
        assert "committed" in d["warnings"]

    def test_lookup_failure_propagates(self):
        fake_result = ImportResult(
            success=False, component=None, error="No data found", warnings=[]
        )
        with mock.patch(
            "kiassist_utils.importer.part_lookup.import_by_part",
            return_value=fake_result,
        ):
            result = _call("part_import", mpn="DOES_NOT_EXIST")
        assert result["status"] == "ok"
        assert result["data"]["success"] is False
        assert result["data"]["error"] == "No data found"
