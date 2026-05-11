# KiAssist — KiCad Interface Reference

This document lists every KiCad-related MCP tool implemented in KiAssist. All 55 tools
are exposed via the **MCP interface** (FastMCP in `mcp_server.py`) and are callable by any
MCP-compatible AI client (e.g. Claude Desktop, Cursor) or via `in_process_call()`.

The majority of tools work via the **custom S-expression parser** (`kicad_parser/`) —
reading or writing KiCad files directly on disk with no live KiCad instance needed.
Tools that require a running KiCad instance are grouped separately in the
[KiCad IPC Bridge](#4-kicad-ipc-bridge) section below.

---

## 1. Schematic Editor Tools (`.kicad_sch`)

All schematic tools operate via the **custom S-expression parser** (`kicad_parser/schematic.py`).
Writes use `_safe_save()` (atomic write with `.bak` backup).

| Tool | Description | Read / Write |
|---|---|:---:|
| `schematic_open` | Load a schematic file and return a summary (component count, wire count, sheet count, page size, title) | read |
| `schematic_list_symbols` | List all placed symbols with reference, value, footprint, lib ID, and position | read |
| `schematic_get_symbol` | Get detailed info for a specific symbol: properties, pin positions, and net connections | read |
| `schematic_add_symbol` | Place a symbol from a library at a given position with specified properties; saves the schematic | write |
| `schematic_remove_symbol` | Remove a symbol by reference designator and save | write |
| `schematic_modify_symbol` | Update properties (value, footprint, custom fields) on a placed symbol and save | write |
| `schematic_add_wire` | Add a wire segment between two coordinates and save | write |
| `schematic_connect_pins` | Auto-route a straight wire between two pin references (e.g. `"R1:1"` → `"U1:VCC"`) and save | write |
| `schematic_add_label` | Add a net label or global label at a position and save | write |
| `schematic_get_nets` | List all nets and their connected pins | read |
| `schematic_find_pins` | Find pins matching a reference designator or pin name pattern | read |
| `schematic_get_power_pins` | Get all power pins for a specific symbol (useful for decoupling cap placement) | read |
| `schematic_add_junction` | Add a junction marker at a coordinate and save | write |
| `schematic_add_no_connect` | Add a no-connect marker at a coordinate and save | write |
| `schematic_search` | Fuzzy search for symbols across references, values, and properties | read |

---

## 2. Symbol Library Tools (`.kicad_sym`)

All symbol library tools operate via the **custom S-expression parser** (`kicad_parser/symbol_lib.py`).

| Tool | Description | Read / Write |
|---|---|:---:|
| `symbol_lib_open` | Open a symbol library and list all symbol names | read |
| `symbol_lib_get_symbol` | Get the full definition of a symbol: pins, graphics, properties | read |
| `symbol_lib_create_symbol` | Create a new symbol with specified pins, graphics, and properties; saves the library | write |
| `symbol_lib_modify_symbol` | Modify an existing symbol's properties (description, keywords, datasheet, custom fields) and save | write |
| `symbol_lib_delete_symbol` | Remove a symbol from a library and save | write |
| `symbol_lib_add_pin` | Add a pin to an existing symbol and save | write |
| `symbol_lib_bulk_update` | Apply a property update to every symbol in a library (e.g. add/overwrite a field) and save | write |
| `symbol_lib_list_libraries` | Discover all available symbol libraries by scanning `sym-lib-table` files (project + global) | read |

---

## 3. Footprint Tools (`.kicad_mod`)

All footprint tools operate via the **custom S-expression parser** (`kicad_parser/footprint.py`).

| Tool | Description | Read / Write |
|---|---|:---:|
| `footprint_open` | Open a footprint file and return a summary (pad count, layer, description, tags) | read |
| `footprint_get_details` | Get the full footprint definition: all pads, graphics primitives, 3D models | read |
| `footprint_create` | Create a new footprint file with specified pads and properties | write |
| `footprint_modify` | Modify footprint top-level properties (description, tags, attributes) and save | write |
| `footprint_add_pad` | Add a pad (SMD, through-hole, NPTH, etc.) to an existing footprint and save | write |
| `footprint_remove_pad` | Remove a pad by pad number and save | write |
| `footprint_renumber_pads` | Renumber all pads sequentially starting from a given number and save | write |
| `footprint_list_libraries` | Discover all available footprint libraries by scanning `fp-lib-table` files (project + global) | read |

---

## 4. KiCad IPC Bridge

The IPC bridge connects KiAssist to a **live running KiCad instance** via the KiCad IPC API,
implemented in `kicad_ipc.py` and `ipc_workflow.py` using the `kicad-python` (`kipy`) library.
This requires KiCad 9+ with the API server enabled (Preferences → Plugins → Enable API server).

### How it works

Communication happens over a platform-specific socket (`/tmp/kicad/api*.sock` on Linux/macOS,
or a Windows named pipe). KiAssist discovers all running KiCad instances by scanning for these
socket files and probing each one. The IPC API exposes three operations that KiAssist uses:

- **`SaveDocument`** — flush KiCad's in-memory state to disk (used before a file edit)
- **`RevertDocument`** — discard in-memory changes and re-read the file from disk
- **`RefreshEditor`** — force a UI redraw after a revert (used after a file edit)

### IPC Bridge MCP Tools

| Tool | Description |
|---|---|
| `kicad_list_instances` | Detect all running KiCad instances and return socket paths, project names, and version strings |
| `kicad_get_project_info` | Get schematics, PCB files, library paths, and live-open status for a `.kicad_pro` project |
| `kicad_save_schematic` | Trigger `SaveDocument` via the IPC API to flush unsaved KiCad changes to disk |
| `kicad_reload_schematic` | Trigger `RevertDocument` + `RefreshEditor` via the IPC API to reload the on-disk file into KiCad |
| `kicad_get_board_info` | Read board summary (net count, footprint count, track count, layer stackup) from a PCB file |
| `kicad_check_file_status` | Check whether a KiCad file is currently open in a live KiCad instance and report its mtime/backup state |
| `kicad_edit_file_pipeline` | Orchestrate the full pipeline: IPC save → direct file edit (custom parser) → IPC reload; rolls back on error |

### `kicad_edit_file_pipeline` — Combined Workflow

`kicad_edit_file_pipeline` is the recommended way to edit any KiCad file that may be open in a
live KiCad instance. It ties the IPC bridge and the custom parser together:

1. **IPC detection** — checks whether the file is currently open in KiCad
2. **IPC save** (if open) — calls `SaveDocument` to flush KiCad's state to disk
3. **File lock** — acquires an OS advisory lock to prevent concurrent modifications
4. **Custom parser edit** — invokes the specified tool to modify the file on disk
5. **Rollback** — restores the `.bak` backup automatically if the edit fails
6. **IPC reload** (if open and edit succeeded) — calls `RevertDocument` + `RefreshEditor`

---

## 5. PCB Editor Tools (`.kicad_pcb`)

All PCB tools operate via the **custom S-expression parser** (`kicad_parser/pcb.py`).

| Tool | Description | Read / Write |
|---|---|:---:|
| `pcb_open` | Open a PCB file and return a full board summary (nets, footprints, tracks, vias, layer stackup) | read |
| `pcb_new` | Create a new, empty PCB file and write it to disk | write |
| `pcb_get_layer_stackup` | Get the ordered list of copper layers in a PCB file | read |
| `pcb_list_nets` | List all nets defined in a PCB file | read |
| `pcb_add_net` | Add a new named net to a PCB file and save | write |
| `pcb_list_footprints` | List all footprint instances placed on the board | read |
| `pcb_get_footprint` | Get detailed information for a specific board footprint (pads, nets, layer, position) | read |
| `pcb_add_footprint` | Place a footprint on the board and save | write |
| `pcb_remove_footprint` | Remove a footprint from the board by reference designator and save | write |
| `pcb_move_footprint` | Move and/or rotate a footprint to a new position and save | write |
| `pcb_list_tracks` | List all copper track segments in a PCB file | read |
| `pcb_add_track` | Add a copper track segment (start/end/layer/width) to a PCB file and save | write |
| `pcb_list_vias` | List all vias in a PCB file | read |
| `pcb_add_via` | Add a copper via (position/size/drill/layers) to a PCB file and save | write |

---

## 6. Project Context Tools

These tools provide project-level context and AI memory persistence via the
**custom parser** and file system access.

| Tool | Description | Read / Write |
|---|---|:---:|
| `project_get_context` | Aggregate a project summary: BOM from all schematics, symbol/footprint library paths, design-rule files, KIASSIST.md presence | read |
| `project_read_memory` | Read the `KIASSIST.md` project memory file (design decisions, preferred components, naming conventions) | read |
| `project_write_memory` | Write or update the `KIASSIST.md` project memory file | write |

---

## Summary

| Category | Total Tools | Custom Parser |
|---|:---:|:---:|
| Schematic Editor | 15 | 15 |
| Symbol Library | 8 | 8 |
| Footprint | 8 | 8 |
| KiCad IPC Bridge | 7 | 3 |
| PCB Editor | 14 | 14 |
| Project Context | 3 | 3 |
| **Total** | **55** | **51** |

All 55 tools are exposed via the MCP interface. The 4 IPC-only bridge tools
(`kicad_list_instances`, `kicad_save_schematic`, `kicad_reload_schematic`, and the
live-status part of `kicad_check_file_status`) require a running KiCad 9+ instance
and do not use the custom parser.

---

## Architecture Overview

```
AI Client (Claude Desktop, Cursor, etc.)
        │
        │ MCP protocol (stdio)
        ▼
 ┌─────────────────────────────────────────────┐
 │            KiAssist MCP Server              │
 │          (mcp_server.py / FastMCP)          │
 │                                             │
 │  ┌──────────────────┐  ┌─────────────────┐ │
 │  │  Custom S-Expr   │  │   KiCad IPC     │ │
 │  │  Parser          │  │   Bridge        │ │
 │  │  (kicad_parser/) │  │   (kicad_ipc.py)│ │
 │  └────────┬─────────┘  └───────┬─────────┘ │
 └───────────┼────────────────────┼────────────┘
             │                    │
             │ direct file I/O    │ IPC socket
             ▼                    ▼
   .kicad_sch / .kicad_sym    KiCad 9+ (live)
   .kicad_mod / .kicad_pcb    via kicad-python
   files on disk              (kipy / kicad-api)
```

---

## Agent Contract — How the AI uses these tools

This section documents the **contract** the streaming chat agent expects every
MCP tool to honour, plus the canonical "tool flows" for the most common user
intents.  When you add or modify an `@mcp.tool()`, update this section so
future agents (and reviewers) understand the expected behaviour.

### Tool envelope

Every MCP tool returns a JSON dict:

```json
{ "status": "ok",    "data": { ... } }
{ "status": "error", "message": "human-readable reason" }
```

The streaming dispatch (`KiAssistAPI._execute_mcp_tool`) JSON-encodes this
envelope and forwards it back to the model with the `is_error` flag set when
`status == "error"`.

### Focused-agent tool filtering

The `set_focused_agent("schematic-agent" | "pcb-agent" | …)` API filters which
tools the model sees on each turn:

| `focused_agent`        | Tool prefixes exposed                                 |
| ---------------------- | ----------------------------------------------------- |
| `None` / unspecified   | All tools                                             |
| `schematic-agent`      | `schematic_*`, `kicad_*`, `library_*`, `part_*`, `web_search` |
| `symbol-agent`         | `symbol_*`, `library_*`, `part_*`                     |
| `footprint-agent`      | `footprint_*`, `library_*`, `part_*`                  |
| `pcb-agent`            | `pcb_*`, `kicad_*`, `library_*`                       |
| `requirements-agent`   | `project_*`, `web_search`                             |

Smaller tool surfaces dramatically improve tool-selection accuracy on smaller
models.

### Mutating-tool round-trip

Tools in `KiAssistAPI._MUTATING_SCHEMATIC_TOOLS` (e.g. `schematic_add_symbol`,
`schematic_add_wire`, `schematic_save`) are wrapped in
`SchematicEditPipeline` when called with a `path` argument:

1. If the file is open in KiCad, `kicad_save_schematic` is invoked first.
2. An advisory file lock is acquired.
3. The MCP tool runs.
4. On error, the `.bak` backup is restored.
5. The lock is released.
6. If the file is still open in KiCad, `kicad_reload_schematic` triggers a UI refresh.

`_safe_save` writes both a canonical `<file>.bak` and a timestamped
`<file>.bak.<unix-ms>` rotation; only the most recent
`_BAK_RETENTION_COUNT` (5) rotations are kept.

### Canonical agent flows

#### "Add a 100 nF decoupling cap on U1's VCC pin"

```
schematic_open(path)
  → schematic_get_power_pins(path, reference="U1")
  → library_search(query="cap", kind="symbol")
  → schematic_add_symbol(path, lib_id="Device:C", reference="C?", value="100nF", at=...)
  → schematic_connect_pins(path, ref_a="U1", pin_a="VCC", ref_b="C?", pin_b="1")
  → kicad_save_schematic(path)   # auto-injected by SchematicEditPipeline
```

#### "Build a blank schematic for an STM32 dev board"

```
project_create(directory="…", name="stm32-devboard")
  → library_search(query="stm32", kind="symbol")
  → schematic_add_symbol(path, lib_id="MCU_ST_STM32F4:STM32F407VETx", …)
  → schematic_add_symbol(path, lib_id="Device:C", …)            # decoupling caps
  → schematic_add_symbol(path, lib_id="Connector:USB_C_Receptacle_USB2.0", …)
  → schematic_save(path)
```

#### "Tell me about U1"

```
schematic_query(path, question="U1 pinout")     # one tool, not five
```

#### "What's in this project?"

The streaming chat injects the tiny project header automatically (project
name, sheet count, BOM size, KIASSIST.md memory).  For deeper detail:

```
project_get_context(project_path)               # full synthesized blob
schematic_query(path, question="summary")       # per-sheet stats
```

### Adding a new MCP tool — checklist

1. Decorate the function with `@mcp.tool()` so FastMCP exports its schema.
2. Validate the file path with `_validate_path(...)` before reading.
3. Return the standard envelope via `_ok(data)` or `_err(msg)`.
4. If the tool **mutates** a `.kicad_sch`, add it to
   `KiAssistAPI._MUTATING_SCHEMATIC_TOOLS` so the live KiCad save/reload
   round-trip is automatic.
5. If the tool is agent-specific, add its prefix to
   `KiAssistAPI._AGENT_TOOL_PREFIXES`.
6. Add a unit test in `tests/test_mcp_server.py` that calls it via the
   `_call(...)` helper (which exercises `in_process_call`).
7. Document the canonical flow above when it unlocks a new user intent.
