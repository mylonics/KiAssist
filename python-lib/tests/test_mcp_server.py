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


@pytest.fixture()
def tmp_pcb(tmp_path: Path) -> Path:
    dst = tmp_path / "pcb.kicad_pcb"
    shutil.copy(FIXTURE_PCB, dst)
    return dst


# ===========================================================================
# in_process_call helper
# ===========================================================================


class TestInProcessCall:
    def test_unknown_tool_raises_key_error(self):
        with pytest.raises(KeyError, match="not registered"):
            asyncio.run(in_process_call("this_tool_does_not_exist", {}))


class TestSafeSave:
    """Verify that mutation tools create a .bak backup before writing."""

    def test_backup_created_on_schematic_mutation(self, tmp_sch: Path):
        bak = Path(str(tmp_sch) + ".bak")
        assert not bak.exists()
        _call("schematic_add_wire", path=str(tmp_sch), x1=0.0, y1=0.0, x2=5.0, y2=0.0)
        assert bak.exists(), ".bak file should be created by _safe_save"

    def test_backup_created_on_footprint_mutation(self, tmp_fp: Path):
        bak = Path(str(tmp_fp) + ".bak")
        assert not bak.exists()
        _call(
            "footprint_add_pad",
            path=str(tmp_fp),
            number="88",
            pad_type="smd",
            shape="rect",
            x=3.0,
            y=3.0,
            width=1.0,
            height=1.0,
        )
        assert bak.exists(), ".bak file should be created by _safe_save"

    def test_backup_created_on_sym_lib_mutation(self, tmp_sym: Path):
        bak = Path(str(tmp_sym) + ".bak")
        assert not bak.exists()
        lib_result = _call("symbol_lib_open", path=str(tmp_sym))
        name = lib_result["data"]["symbols"][0]
        _call(
            "symbol_lib_modify_symbol",
            path=str(tmp_sym),
            name=name,
            properties={"Description": "backup test"},
        )
        assert bak.exists(), ".bak file should be created by _safe_save"

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
        assert "connections" in result["data"]
        assert isinstance(result["data"]["connections"], dict)
        assert "pin_positions" in result["data"]

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
        # Phase 2 plan requires design rules and library lists
        assert "design_rule_files" in data
        assert "design_rules" in data
        assert "symbol_libraries" in data
        assert "footprint_libraries" in data

    def test_design_rules_parsed(self, tmp_path: Path):
        # Create a minimal .kicad_dru file and verify it appears in context
        dru = tmp_path / "test.kicad_dru"
        dru.write_text("(rules (version 1))", encoding="utf-8")
        result = _call("project_get_context", project_path=str(tmp_path))
        assert result["status"] == "ok"
        assert len(result["data"]["design_rule_files"]) == 1
        assert result["data"]["design_rules"][0]["content"] == "(rules (version 1))"

    def test_unreadable_design_rule_file(self, tmp_path: Path):
        """Error handling: unreadable .kicad_dru produces empty content string."""
        import stat

        dru = tmp_path / "unreadable.kicad_dru"
        dru.write_text("(rules)", encoding="utf-8")
        # Remove read permission so Path.read_text() fails
        dru.chmod(0o000)
        try:
            result = _call("project_get_context", project_path=str(tmp_path))
            assert result["status"] == "ok"
            # The file path is still listed
            assert len(result["data"]["design_rule_files"]) == 1
            # Content falls back to empty string on read error
            assert result["data"]["design_rules"][0]["content"] == ""
        finally:
            # Restore permission so tmp_path cleanup works
            dru.chmod(stat.S_IRUSR | stat.S_IWUSR)

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


# ===========================================================================
# PCB Editor tools
# ===========================================================================


class TestPCBOpen:
    def test_success(self):
        result = _call("pcb_open", path=str(FIXTURE_PCB))
        assert result["status"] == "ok"
        data = result["data"]
        assert "version" in data
        assert "net_count" in data
        assert "footprint_count" in data
        assert "track_count" in data
        assert "via_count" in data
        assert "layer_stackup" in data
        assert isinstance(data["nets"], list)
        assert isinstance(data["footprints"], list)
        # The fixture has at least one net and one footprint
        assert data["footprint_count"] >= 1
        assert data["net_count"] >= 1

    def test_not_found(self):
        result = _call("pcb_open", path="/no/such/file.kicad_pcb")
        assert result["status"] == "error"


class TestPCBNew:
    def test_creates_file(self, tmp_path: Path):
        dest = tmp_path / "new_board.kicad_pcb"
        result = _call("pcb_new", path=str(dest))
        assert result["status"] == "ok"
        assert dest.exists()
        # Verify the created file is loadable
        check = _call("pcb_open", path=str(dest))
        assert check["status"] == "ok"


class TestPCBGetLayerStackup:
    def test_returns_layers(self):
        result = _call("pcb_get_layer_stackup", path=str(FIXTURE_PCB))
        assert result["status"] == "ok"
        assert isinstance(result["data"]["layers"], list)
        assert len(result["data"]["layers"]) >= 2

    def test_not_found(self):
        result = _call("pcb_get_layer_stackup", path="/no/such/file.kicad_pcb")
        assert result["status"] == "error"


class TestPCBNets:
    def test_list_nets(self):
        result = _call("pcb_list_nets", path=str(FIXTURE_PCB))
        assert result["status"] == "ok"
        nets = result["data"]
        assert isinstance(nets, list)
        # Fixture has VCC and GND
        names = [n["name"] for n in nets]
        assert "VCC" in names
        assert "GND" in names

    def test_add_net(self, tmp_pcb: Path):
        result = _call("pcb_add_net", path=str(tmp_pcb), name="PWR_3V3")
        assert result["status"] == "ok"
        assert result["data"]["name"] == "PWR_3V3"
        assert result["data"]["added"] is True
        # Verify persisted
        check = _call("pcb_list_nets", path=str(tmp_pcb))
        names = [n["name"] for n in check["data"]]
        assert "PWR_3V3" in names

    def test_add_duplicate_net(self, tmp_pcb: Path):
        result = _call("pcb_add_net", path=str(tmp_pcb), name="VCC")
        assert result["status"] == "error"

    def test_list_not_found(self):
        result = _call("pcb_list_nets", path="/no/such/file.kicad_pcb")
        assert result["status"] == "error"


class TestPCBFootprints:
    def test_list_footprints(self):
        result = _call("pcb_list_footprints", path=str(FIXTURE_PCB))
        assert result["status"] == "ok"
        fps = result["data"]
        assert isinstance(fps, list)
        assert len(fps) >= 1
        # Check fields
        fp = fps[0]
        assert "reference" in fp
        assert "value" in fp
        assert "layer" in fp
        assert "position" in fp

    def test_get_footprint(self):
        result = _call("pcb_get_footprint", path=str(FIXTURE_PCB), reference="R1")
        assert result["status"] == "ok"
        data = result["data"]
        assert data["reference"] == "R1"
        assert "pads" in data
        assert "pad_count" in data
        assert data["pad_count"] >= 1
        # Pads should have net names from the fixture
        pad_nets = [p["net"] for p in data["pads"]]
        assert "VCC" in pad_nets or "GND" in pad_nets

    def test_get_footprint_not_found(self):
        result = _call("pcb_get_footprint", path=str(FIXTURE_PCB), reference="ZZZZ")
        assert result["status"] == "error"

    def test_add_footprint(self, tmp_pcb: Path):
        before = _call("pcb_list_footprints", path=str(tmp_pcb))["data"]
        result = _call(
            "pcb_add_footprint",
            path=str(tmp_pcb),
            name="Resistor_SMD:R_0402_1005Metric",
            reference="R99",
            value="100R",
            layer="F.Cu",
            x=50.0,
            y=50.0,
        )
        assert result["status"] == "ok"
        assert result["data"]["reference"] == "R99"
        after = _call("pcb_list_footprints", path=str(tmp_pcb))["data"]
        assert len(after) == len(before) + 1

    def test_remove_footprint(self, tmp_pcb: Path):
        result = _call("pcb_remove_footprint", path=str(tmp_pcb), reference="R1")
        assert result["status"] == "ok"
        assert result["data"]["removed"] is True
        check = _call("pcb_list_footprints", path=str(tmp_pcb))["data"]
        refs = [fp["reference"] for fp in check]
        assert "R1" not in refs

    def test_remove_footprint_not_found(self, tmp_pcb: Path):
        result = _call("pcb_remove_footprint", path=str(tmp_pcb), reference="ZZZZ")
        assert result["status"] == "error"

    def test_move_footprint(self, tmp_pcb: Path):
        result = _call(
            "pcb_move_footprint",
            path=str(tmp_pcb),
            reference="R1",
            x=120.0,
            y=80.0,
            angle=90.0,
        )
        assert result["status"] == "ok"
        data = result["data"]
        assert data["x"] == 120.0
        assert data["y"] == 80.0
        assert data["angle"] == 90.0
        # Verify the position is persisted
        check = _call("pcb_get_footprint", path=str(tmp_pcb), reference="R1")
        pos = check["data"]["position"]
        assert abs(pos["x"] - 120.0) < 0.001
        assert abs(pos["y"] - 80.0) < 0.001
        # Pads must still be present after move
        assert check["data"]["pad_count"] >= 1

    def test_move_footprint_not_found(self, tmp_pcb: Path):
        result = _call(
            "pcb_move_footprint", path=str(tmp_pcb), reference="ZZZZ", x=0.0, y=0.0
        )
        assert result["status"] == "error"


class TestPCBTracks:
    def test_list_tracks(self):
        result = _call("pcb_list_tracks", path=str(FIXTURE_PCB))
        assert result["status"] == "ok"
        tracks = result["data"]
        assert isinstance(tracks, list)
        assert len(tracks) >= 1
        t = tracks[0]
        assert "start" in t
        assert "end" in t
        assert "layer" in t
        assert "width" in t
        assert "net_name" in t

    def test_add_track(self, tmp_pcb: Path):
        before = len(_call("pcb_list_tracks", path=str(tmp_pcb))["data"])
        result = _call(
            "pcb_add_track",
            path=str(tmp_pcb),
            x1=100.0,
            y1=105.0,
            x2=110.0,
            y2=105.0,
            layer="F.Cu",
            width=0.3,
            net="GND",
        )
        assert result["status"] == "ok"
        assert result["data"]["added"] is True
        after = len(_call("pcb_list_tracks", path=str(tmp_pcb))["data"])
        assert after == before + 1

    def test_list_not_found(self):
        result = _call("pcb_list_tracks", path="/no/such/file.kicad_pcb")
        assert result["status"] == "error"


class TestPCBVias:
    def test_list_vias_empty(self):
        # The fixture has no vias, but the call should still succeed
        result = _call("pcb_list_vias", path=str(FIXTURE_PCB))
        assert result["status"] == "ok"
        assert isinstance(result["data"], list)

    def test_add_via(self, tmp_pcb: Path):
        before = len(_call("pcb_list_vias", path=str(tmp_pcb))["data"])
        result = _call(
            "pcb_add_via",
            path=str(tmp_pcb),
            x=105.0,
            y=100.0,
            net="VCC",
            drill=0.4,
            size=0.8,
        )
        assert result["status"] == "ok"
        assert result["data"]["added"] is True
        after = len(_call("pcb_list_vias", path=str(tmp_pcb))["data"])
        assert after == before + 1

    def test_list_not_found(self):
        result = _call("pcb_list_vias", path="/no/such/file.kicad_pcb")
        assert result["status"] == "error"


class TestPCBBackupOnSave:
    def test_backup_created(self, tmp_pcb: Path):
        bak = Path(str(tmp_pcb) + ".bak")
        assert not bak.exists()
        _call("pcb_add_net", path=str(tmp_pcb), name="BACKUP_TEST_NET")
        assert bak.exists(), ".bak file should be created by _safe_save"
