"""Tests for the KiAssist MCP server (mcp_server.py).

All tools are called via the async ``in_process_call`` helper so the tests
exercise the real tool logic without needing a running MCP process.
"""

from __future__ import annotations

import asyncio
import shutil
import tempfile
from pathlib import Path
from typing import Any, Dict

import pytest

from kiassist_utils.mcp_server import in_process_call

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

FIXTURE_DIR = Path(__file__).parent / "fixtures"
FIXTURE_SCH = FIXTURE_DIR / "test_schematic.kicad_sch"
FIXTURE_SYM = FIXTURE_DIR / "test_symbol_lib.kicad_sym"
FIXTURE_FP = FIXTURE_DIR / "test_footprint.kicad_mod"
FIXTURE_PCB = FIXTURE_DIR / "test_pcb.kicad_pcb"


def _call(tool: str, **kwargs: Any) -> Dict[str, Any]:
    """Synchronous wrapper around in_process_call."""
    return asyncio.run(in_process_call(tool, kwargs))


@pytest.fixture()
def tmp_sch(tmp_path: Path) -> Path:
    dst = tmp_path / "sch.kicad_sch"
    shutil.copy(FIXTURE_SCH, dst)
    return dst


@pytest.fixture()
def tmp_sym(tmp_path: Path) -> Path:
    dst = tmp_path / "sym.kicad_sym"
    shutil.copy(FIXTURE_SYM, dst)
    return dst


@pytest.fixture()
def tmp_fp(tmp_path: Path) -> Path:
    dst = tmp_path / "fp.kicad_mod"
    shutil.copy(FIXTURE_FP, dst)
    return dst


# ===========================================================================
# in_process_call helper
# ===========================================================================


class TestInProcessCall:
    def test_unknown_tool_raises_key_error(self):
        with pytest.raises(KeyError, match="not registered"):
            asyncio.run(in_process_call("this_tool_does_not_exist", {}))


# ===========================================================================
# Schematic tools
# ===========================================================================


class TestSchematicOpen:
    def test_success(self):
        result = _call("schematic_open", path=str(FIXTURE_SCH))
        assert result["status"] == "ok"
        data = result["data"]
        assert data["component_count"] >= 1
        assert data["paper"] == "A4"

    def test_not_found(self):
        result = _call("schematic_open", path="/nonexistent/file.kicad_sch")
        assert result["status"] == "error"


class TestSchematicListSymbols:
    def test_returns_list(self):
        result = _call("schematic_list_symbols", path=str(FIXTURE_SCH))
        assert result["status"] == "ok"
        assert isinstance(result["data"], list)
        assert len(result["data"]) >= 1

    def test_symbol_fields(self):
        result = _call("schematic_list_symbols", path=str(FIXTURE_SCH))
        sym = result["data"][0]
        assert "reference" in sym
        assert "value" in sym
        assert "position" in sym


class TestSchematicGetSymbol:
    def test_found(self):
        # Get list first to find a valid reference
        symbols = _call("schematic_list_symbols", path=str(FIXTURE_SCH))["data"]
        ref = symbols[0]["reference"]
        result = _call("schematic_get_symbol", path=str(FIXTURE_SCH), reference=ref)
        assert result["status"] == "ok"
        assert result["data"]["reference"] == ref
        assert "properties" in result["data"]

    def test_not_found(self):
        result = _call(
            "schematic_get_symbol", path=str(FIXTURE_SCH), reference="ZZZNOTEXIST"
        )
        assert result["status"] == "error"


class TestSchematicAddSymbol:
    def test_add_and_persist(self, tmp_sch: Path):
        result = _call(
            "schematic_add_symbol",
            path=str(tmp_sch),
            lib_id="Device:C",
            x=100.0,
            y=100.0,
            reference="C99",
            value="100nF",
        )
        assert result["status"] == "ok"
        # Verify persisted
        check = _call("schematic_list_symbols", path=str(tmp_sch))
        refs = [s["reference"] for s in check["data"]]
        assert "C99" in refs


class TestSchematicRemoveSymbol:
    def test_remove_existing(self, tmp_sch: Path):
        symbols = _call("schematic_list_symbols", path=str(tmp_sch))["data"]
        ref = symbols[0]["reference"]
        result = _call(
            "schematic_remove_symbol", path=str(tmp_sch), reference=ref
        )
        assert result["status"] == "ok"
        assert result["data"]["removed"] is True
        # Verify removed
        check = _call("schematic_list_symbols", path=str(tmp_sch))
        assert not any(s["reference"] == ref for s in check["data"])

    def test_remove_missing(self, tmp_sch: Path):
        result = _call(
            "schematic_remove_symbol", path=str(tmp_sch), reference="NOTEXIST"
        )
        assert result["status"] == "error"


class TestSchematicModifySymbol:
    def test_modify_value(self, tmp_sch: Path):
        symbols = _call("schematic_list_symbols", path=str(tmp_sch))["data"]
        ref = symbols[0]["reference"]
        result = _call(
            "schematic_modify_symbol",
            path=str(tmp_sch),
            reference=ref,
            value="NEW_VALUE",
        )
        assert result["status"] == "ok"
        # Verify persisted
        check = _call("schematic_get_symbol", path=str(tmp_sch), reference=ref)
        assert check["data"]["value"] == "NEW_VALUE"


class TestSchematicAddWire:
    def test_add_wire(self, tmp_sch: Path):
        result = _call(
            "schematic_add_wire",
            path=str(tmp_sch),
            x1=0.0,
            y1=0.0,
            x2=10.0,
            y2=0.0,
        )
        assert result["status"] == "ok"
        assert result["data"]["added"] is True


class TestSchematicConnectPins:
    def test_unknown_pin_error(self, tmp_sch: Path):
        result = _call(
            "schematic_connect_pins",
            path=str(tmp_sch),
            pin_a="NOTEXIST:1",
            pin_b="NOTEXIST:2",
        )
        assert result["status"] == "error"


class TestSchematicAddLabel:
    def test_add_net_label(self, tmp_sch: Path):
        result = _call(
            "schematic_add_label",
            path=str(tmp_sch),
            text="VCC",
            x=10.0,
            y=10.0,
        )
        assert result["status"] == "ok"
        assert result["data"]["type"] == "label"

    def test_add_global_label(self, tmp_sch: Path):
        result = _call(
            "schematic_add_label",
            path=str(tmp_sch),
            text="GND",
            x=20.0,
            y=20.0,
            global_label=True,
        )
        assert result["status"] == "ok"
        assert result["data"]["type"] == "global_label"


class TestSchematicGetNets:
    def test_returns_dict(self):
        result = _call("schematic_get_nets", path=str(FIXTURE_SCH))
        assert result["status"] == "ok"
        assert isinstance(result["data"], dict)


class TestSchematicFindPins:
    def test_find_by_reference(self):
        symbols = _call("schematic_list_symbols", path=str(FIXTURE_SCH))["data"]
        ref = symbols[0]["reference"]
        result = _call(
            "schematic_find_pins", path=str(FIXTURE_SCH), reference=ref
        )
        assert result["status"] == "ok"
        # Each entry has required fields
        for entry in result["data"]:
            assert "symbol_reference" in entry
            assert "pin_number" in entry
            assert "pin_name" in entry


class TestSchematicGetPowerPins:
    def test_returns_list(self):
        symbols = _call("schematic_list_symbols", path=str(FIXTURE_SCH))["data"]
        ref = symbols[0]["reference"]
        result = _call(
            "schematic_get_power_pins", path=str(FIXTURE_SCH), reference=ref
        )
        # May be empty for resistor, but should succeed
        assert result["status"] == "ok"
        assert isinstance(result["data"], list)


class TestSchematicAddJunctionNoConnect:
    def test_add_junction(self, tmp_sch: Path):
        result = _call("schematic_add_junction", path=str(tmp_sch), x=5.0, y=5.0)
        assert result["status"] == "ok"

    def test_add_no_connect(self, tmp_sch: Path):
        result = _call("schematic_add_no_connect", path=str(tmp_sch), x=5.0, y=5.0)
        assert result["status"] == "ok"


class TestSchematicSearch:
    def test_search_finds_match(self):
        # Search for something that exists in the fixture
        result = _call("schematic_search", path=str(FIXTURE_SCH), query="R")
        assert result["status"] == "ok"
        # Should find the resistor
        assert len(result["data"]) >= 1

    def test_search_no_match(self):
        result = _call(
            "schematic_search", path=str(FIXTURE_SCH), query="ZZZNOMATCH_9999"
        )
        assert result["status"] == "ok"
        assert result["data"] == []


# ===========================================================================
# Symbol library tools
# ===========================================================================


class TestSymbolLibOpen:
    def test_success(self):
        result = _call("symbol_lib_open", path=str(FIXTURE_SYM))
        assert result["status"] == "ok"
        assert result["data"]["symbol_count"] >= 1

    def test_not_found(self):
        result = _call("symbol_lib_open", path="/no/such/file.kicad_sym")
        assert result["status"] == "error"


class TestSymbolLibGetSymbol:
    def test_found(self):
        lib_result = _call("symbol_lib_open", path=str(FIXTURE_SYM))
        name = lib_result["data"]["symbols"][0]
        result = _call("symbol_lib_get_symbol", path=str(FIXTURE_SYM), name=name)
        assert result["status"] == "ok"
        assert result["data"]["name"] == name
        assert "pins" in result["data"]

    def test_not_found(self):
        result = _call(
            "symbol_lib_get_symbol", path=str(FIXTURE_SYM), name="NOTEXIST_SYM"
        )
        assert result["status"] == "error"


class TestSymbolLibCreateDeleteSymbol:
    def test_create_and_delete(self, tmp_sym: Path):
        create_result = _call(
            "symbol_lib_create_symbol",
            path=str(tmp_sym),
            name="TEST_NEW_SYM",
            properties={"Description": "A test symbol"},
        )
        assert create_result["status"] == "ok"

        # Verify it appears in the library
        open_result = _call("symbol_lib_open", path=str(tmp_sym))
        assert "TEST_NEW_SYM" in open_result["data"]["symbols"]

        # Delete it
        del_result = _call(
            "symbol_lib_delete_symbol", path=str(tmp_sym), name="TEST_NEW_SYM"
        )
        assert del_result["status"] == "ok"
        assert del_result["data"]["deleted"] is True

    def test_duplicate_create_error(self, tmp_sym: Path):
        lib_result = _call("symbol_lib_open", path=str(tmp_sym))
        existing_name = lib_result["data"]["symbols"][0]
        result = _call(
            "symbol_lib_create_symbol", path=str(tmp_sym), name=existing_name
        )
        assert result["status"] == "error"


class TestSymbolLibModifySymbol:
    def test_modify_existing(self, tmp_sym: Path):
        lib_result = _call("symbol_lib_open", path=str(tmp_sym))
        name = lib_result["data"]["symbols"][0]
        result = _call(
            "symbol_lib_modify_symbol",
            path=str(tmp_sym),
            name=name,
            properties={"Description": "Updated description"},
        )
        assert result["status"] == "ok"

    def test_modify_missing(self, tmp_sym: Path):
        result = _call(
            "symbol_lib_modify_symbol",
            path=str(tmp_sym),
            name="NOTEXIST",
            properties={},
        )
        assert result["status"] == "error"


class TestSymbolLibAddPin:
    def test_add_pin(self, tmp_sym: Path):
        lib_result = _call("symbol_lib_open", path=str(tmp_sym))
        name = lib_result["data"]["symbols"][0]
        result = _call(
            "symbol_lib_add_pin",
            path=str(tmp_sym),
            symbol_name=name,
            pin_number="99",
            pin_name="TEST_PIN",
            electrical_type="input",
            x=5.0,
            y=5.0,
        )
        assert result["status"] == "ok"


class TestSymbolLibBulkUpdate:
    def test_bulk_update(self, tmp_sym: Path):
        result = _call(
            "symbol_lib_bulk_update",
            path=str(tmp_sym),
            properties={"Manufacturer": "ACME"},
        )
        assert result["status"] == "ok"
        assert result["data"]["updated_count"] >= 1


class TestSymbolLibListLibraries:
    def test_returns_list(self):
        result = _call("symbol_lib_list_libraries")
        # May be empty in CI (no KiCad installed), but must not error
        assert result["status"] in ("ok", "error")
        if result["status"] == "ok":
            assert isinstance(result["data"], list)


# ===========================================================================
# Footprint tools
# ===========================================================================


class TestFootprintOpen:
    def test_success(self):
        result = _call("footprint_open", path=str(FIXTURE_FP))
        assert result["status"] == "ok"
        assert "name" in result["data"]
        assert "pad_count" in result["data"]

    def test_not_found(self):
        result = _call("footprint_open", path="/no/such/file.kicad_mod")
        assert result["status"] == "error"


class TestFootprintGetDetails:
    def test_pads_present(self):
        result = _call("footprint_get_details", path=str(FIXTURE_FP))
        assert result["status"] == "ok"
        assert isinstance(result["data"]["pads"], list)


class TestFootprintCreate:
    def test_create_new(self, tmp_path: Path):
        dest = str(tmp_path / "NEW_FP.kicad_mod")
        result = _call(
            "footprint_create",
            path=dest,
            name="NEW_FP",
            description="A test footprint",
            tags="test smd",
            pads=[
                {"number": "1", "type": "smd", "shape": "rect", "x": 0.0, "y": 0.0, "width": 1.5, "height": 1.0},
                {"number": "2", "type": "smd", "shape": "rect", "x": 2.0, "y": 0.0, "width": 1.5, "height": 1.0},
            ],
        )
        assert result["status"] == "ok"
        # Verify the file was created and parses correctly
        verify = _call("footprint_get_details", path=dest)
        assert verify["status"] == "ok"
        assert verify["data"]["pad_count"] == 2


class TestFootprintModify:
    def test_modify_description(self, tmp_fp: Path):
        result = _call(
            "footprint_modify",
            path=str(tmp_fp),
            description="Updated description",
            tags="updated test",
        )
        assert result["status"] == "ok"


class TestFootprintAddRemovePad:
    def test_add_pad(self, tmp_fp: Path):
        before = _call("footprint_get_details", path=str(tmp_fp))["data"]["pad_count"]
        result = _call(
            "footprint_add_pad",
            path=str(tmp_fp),
            number="99",
            pad_type="smd",
            shape="rect",
            x=10.0,
            y=10.0,
            width=1.0,
            height=1.0,
        )
        assert result["status"] == "ok"
        after = _call("footprint_get_details", path=str(tmp_fp))["data"]["pad_count"]
        assert after == before + 1

    def test_remove_pad(self, tmp_fp: Path):
        # Add a pad first
        _call(
            "footprint_add_pad",
            path=str(tmp_fp),
            number="77",
            pad_type="smd",
            shape="rect",
            x=5.0,
            y=5.0,
            width=1.0,
            height=1.0,
        )
        result = _call("footprint_remove_pad", path=str(tmp_fp), number="77")
        assert result["status"] == "ok"
        assert result["data"]["removed"] is True

    def test_remove_missing(self, tmp_fp: Path):
        result = _call("footprint_remove_pad", path=str(tmp_fp), number="NOTEXIST")
        assert result["status"] == "error"


class TestFootprintRenumberPads:
    def test_renumber(self, tmp_fp: Path):
        result = _call("footprint_renumber_pads", path=str(tmp_fp), start=1)
        assert result["status"] == "ok"
        assert result["data"]["renumbered"] >= 1


class TestFootprintListLibraries:
    def test_returns_list(self):
        result = _call("footprint_list_libraries")
        assert result["status"] in ("ok", "error")
        if result["status"] == "ok":
            assert isinstance(result["data"], list)


# ===========================================================================
# IPC Bridge tools
# ===========================================================================


class TestKiCadListInstances:
    def test_returns_list(self):
        # In CI there is no KiCad instance running, so the list is empty.
        result = _call("kicad_list_instances")
        assert result["status"] == "ok"
        assert isinstance(result["data"], list)


class TestKiCadGetProjectInfo:
    def test_existing_dir(self, tmp_path: Path):
        # Create a fake .kicad_pro file
        pro = tmp_path / "test.kicad_pro"
        pro.write_text("(kicad_project)", encoding="utf-8")
        result = _call("kicad_get_project_info", project_path=str(pro))
        assert result["status"] == "ok"
        assert "schematics" in result["data"]
        assert "is_open" in result["data"]

    def test_not_found(self):
        result = _call("kicad_get_project_info", project_path="/no/such/proj.kicad_pro")
        assert result["status"] == "error"


class TestKiCadGetBoardInfo:
    def test_success(self):
        result = _call("kicad_get_board_info", path=str(FIXTURE_PCB))
        assert result["status"] == "ok"
        data = result["data"]
        assert "net_count" in data
        assert "footprint_count" in data
        assert "layer_stackup" in data

    def test_not_found(self):
        result = _call("kicad_get_board_info", path="/no/such/file.kicad_pcb")
        assert result["status"] == "error"


# ===========================================================================
# Project Context tools
# ===========================================================================


class TestProjectGetContext:
    def test_from_directory(self, tmp_path: Path):
        # Copy a schematic into a temp project dir
        shutil.copy(FIXTURE_SCH, tmp_path / "test.kicad_sch")
        result = _call("project_get_context", project_path=str(tmp_path))
        assert result["status"] == "ok"
        data = result["data"]
        assert data["schematic_count"] >= 1
        assert "bom_entries" in data
        assert "has_memory_file" in data

    def test_not_found(self):
        result = _call("project_get_context", project_path="/totally/fake/path")
        assert result["status"] == "error"


class TestProjectReadWriteMemory:
    def test_write_then_read(self, tmp_path: Path):
        project_dir = tmp_path
        content = "# KiAssist Project Memory\n\nUse 100nF 0402 caps.\n"

        write_result = _call(
            "project_write_memory",
            project_path=str(project_dir),
            content=content,
        )
        assert write_result["status"] == "ok"
        assert (project_dir / "KIASSIST.md").exists()

        read_result = _call(
            "project_read_memory", project_path=str(project_dir)
        )
        assert read_result["status"] == "ok"
        assert read_result["data"]["content"] == content

    def test_read_missing(self, tmp_path: Path):
        result = _call("project_read_memory", project_path=str(tmp_path))
        assert result["status"] == "error"

    def test_write_missing_dir(self):
        result = _call(
            "project_write_memory",
            project_path="/totally/fake/path",
            content="hello",
        )
        assert result["status"] == "error"
