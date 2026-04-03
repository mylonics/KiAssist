Plan: KiAssist Architecture Evolution — MCP Tools, Multi-Provider AI, Context Management
TL;DR
Transform KiAssist from a simple Gemini chat wrapper into a full AI-powered KiCad engineering tool. Build a custom S-expression parser for all 4 KiCad file types (.kicad_sch, .kicad_sym, .kicad_mod, .kicad_pcb), expose operations through a single unified MCP server, add multi-provider AI support (Gemini + Claude + OpenAI) with function-calling, and implement Claude Code-inspired context management for persistent, multi-turn design sessions. The IPC save/reload workflow bridges live KiCad instances with file-based editing.

Key decisions:

Custom parser over kicad-sch-api (full control, unified across all 4 types)
MCP protocol as the tool interface (standard, extensible, usable by external AI clients)
Tiered context management inspired by Claude Code (summarization, project memory, file state cache)
Steps
Phase 1: Custom KiCad S-Expression Parser (kicad_parser module)
1.1 — Generic S-Expression Tokenizer
Create python-lib/kiassist_utils/kicad_parser/init.py as a new subpackage. Build a tokenizer in python-lib/kiassist_utils/kicad_parser/sexpr.py that:

Parses S-expressions into a nested Python tree (list[str | float | list])
Handles KiCad's quoting rules (all strings double-quoted in v6+, escape sequences)
Serializes back to S-expression text with correct formatting (indentation, number precision: 4 decimal for schematic, 6 for PCB)
Preserves round-trip fidelity (parse → modify → write should produce minimal diffs)
The sexpdata package is already available in the venv but writing a custom one gives better control over KiCad-specific formatting
1.2 — Base Data Model
Create python-lib/kiassist_utils/kicad_parser/models.py with typed dataclasses for common elements:

Position(x: float, y: float, angle: float = 0) — all in mm, Y-down coordinate system
Stroke(width: float, type: str, color: tuple) — line styles
Effects(font_size: tuple, bold: bool, italic: bool, justify: str, hide: bool) — text formatting
Property(key: str, value: str, position: Position, effects: Effects) — key-value pairs
UUID — wrapper around v4 UUID strings
Pts(points: list[Position]) — coordinate point lists
1.3 — Schematic File Model
Create python-lib/kiassist_utils/kicad_parser/schematic.py:

Schematic class: version, generator, uuid, paper, title_block, lib_symbols, items
SchematicSymbol: lib_id, position, unit, properties, pin_uuids, instances
Wire, Bus, Junction, NoConnect, BusEntry
Label, GlobalLabel, HierarchicalLabel
Sheet: position, size, properties, pins, instances
LibSymbol: name, units (sub-symbols), properties, pins
Methods: load(path), save(path), add_symbol(), remove_symbol(), add_wire(), find_symbols(), get_pin_positions(), get_connected_nets()
1.4 — Symbol Library Model
Create python-lib/kiassist_utils/kicad_parser/symbol_lib.py:

SymbolLibrary: version, generator, symbols
SymbolDef: name, extends, pin_numbers_hide, pin_names_config, properties, units
SymbolUnit: unit_number, style, graphics (rect/polyline/circle/arc/text), pins
Pin: electrical_type, graphic_style, position, length, name, number
Methods: load(path), save(path), add_symbol(), remove_symbol(), modify_symbol(), find_by_name()
1.5 — Footprint Model
Create python-lib/kiassist_utils/kicad_parser/footprint.py:

Footprint: name, layer, description, tags, attributes, graphics, pads, 3d_models
Pad: number, type (smd/thru_hole/connect/np_thru_hole), shape, position, size, drill, layers, net
FootprintGraphic: fp_text, fp_line, fp_rect, fp_circle, fp_arc, fp_poly
Methods: load(path), save(path), add_pad(), remove_pad(), renumber_pads(), modify_pad()
1.6 — PCB Model (Stub for Future)
Create python-lib/kiassist_utils/kicad_parser/pcb.py:

Basic PCBBoard class with load(), save(), read-only accessors for footprints, nets, tracks
Full PCB editing deferred to later phase per user's stated priorities
1.7 — Library Discovery
Create python-lib/kiassist_utils/kicad_parser/library.py:

Discover KiCad's installed symbol/footprint libraries by reading sym-lib-table and fp-lib-table files
Search project-local and global library tables
Resolve library nicknames to file paths
Cache library metadata for fast lookups
1.8 — Tests
Create test files in tests:

test_sexpr.py — round-trip parsing, edge cases
test_schematic_parser.py — load real .kicad_sch files, verify model, modify and re-save
test_symbol_lib_parser.py — load .kicad_sym files, symbol CRUD
test_footprint_parser.py — load .kicad_mod files, pad operations
Phase 2: Unified MCP Server
2.1 — MCP Server Scaffold
Create python-lib/kiassist_utils/mcp_server.py:

Use the mcp Python package (FastMCP) — add mcp>=1.0.0 to pyproject.toml dependencies
Register as console script: kiassist-mcp = "kiassist_utils.mcp_server:main" for stdio transport
Also provide an in_process_call(tool_name, args) entry point for direct invocation from KiAssistAPI (bypassing MCP protocol overhead when tools are called from within the app)
2.2 — Schematic Tools (~15 tools)

Tool	Description
schematic_open	Load a schematic file, return summary (component count, sheets, page size)
schematic_list_symbols	List all placed symbols with reference, value, footprint, position
schematic_get_symbol	Get detailed info for a specific symbol (properties, pins, connections)
schematic_add_symbol	Place a symbol from a library at a position with given properties
schematic_remove_symbol	Remove a symbol by reference designator
schematic_modify_symbol	Update symbol properties (value, footprint, custom fields)
schematic_add_wire	Add a wire between two points
schematic_connect_pins	Auto-route a wire between two pin references (e.g., "R1:1" to "U1:VCC")
schematic_add_label	Add a net label or global label at a position
schematic_get_nets	List all nets and their connected pins
schematic_find_pins	Find pins by name pattern, type, or symbol reference
schematic_get_power_pins	Get all power pins for a specific symbol (for decoupling cap use case)
schematic_add_junction	Add a junction point
schematic_add_no_connect	Add a no-connect marker
schematic_search	Fuzzy search across references, values, properties
2.3 — Symbol Library Tools (~8 tools)

Tool	Description
symbol_lib_open	Open a symbol library, list all symbols
symbol_lib_get_symbol	Get full symbol definition (pins, graphics, properties)
symbol_lib_create_symbol	Create a new symbol with specified pins, graphics, properties
symbol_lib_modify_symbol	Modify an existing symbol (add/remove pins, change graphics)
symbol_lib_delete_symbol	Remove a symbol from a library
symbol_lib_add_pin	Add a pin to a symbol
symbol_lib_bulk_update	Apply a transformation to all symbols in a library (e.g., add field)
symbol_lib_list_libraries	Discover all available symbol libraries
2.4 — Footprint Tools (~8 tools)

Tool	Description
footprint_open	Open a footprint file, return summary
footprint_get_details	Get full footprint definition (pads, graphics)
footprint_create	Create a new footprint with pads and graphics
footprint_modify	Modify footprint properties
footprint_add_pad	Add a pad to a footprint
footprint_remove_pad	Remove a pad
footprint_renumber_pads	Renumber pads (e.g., increment by 1, insert new pad 1)
footprint_list_libraries	Discover all available footprint libraries
2.5 — IPC Bridge Tools (~5 tools, using kipy)

Tool	Description
kicad_list_instances	List running KiCad instances with open projects
kicad_get_project_info	Get detailed info about an open project (files, editors)
kicad_save_schematic	Trigger save in KiCad before file editing (keyboard automation fallback if no IPC command)
kicad_reload_schematic	Trigger reload after file editing
kicad_get_board_info	Read-only: get PCB info via kipy (nets, footprints, layer stackup)
2.6 — Project Context Tools (~3 tools)

Tool	Description
project_get_context	Get project summary: all schematics, libraries, BOM, design rules
project_read_memory	Read KIASSIST.md project memory file
project_write_memory	Update KIASSIST.md with design decisions, preferences
Phase 3: Provider-Agnostic AI Interface
3.1 — Abstract Provider Interface
Create python-lib/kiassist_utils/ai/init.py and python-lib/kiassist_utils/ai/base.py:

AIProvider abstract class with methods:
chat(messages, tools, system_prompt) -> AIResponse — multi-turn with tool definitions
chat_stream(messages, tools, system_prompt) -> AsyncIterator[AIChunk] — streaming variant
get_context_window() -> int — model's max context size
get_max_output_tokens() -> int
supports_tool_calling() -> bool
AIMessage dataclass: role (system/user/assistant/tool), content, tool_calls, tool_results
AIToolCall dataclass: id, name, arguments
AIToolResult dataclass: tool_call_id, content, is_error
3.2 — Gemini Provider
Refactor gemini.py → python-lib/kiassist_utils/ai/gemini.py:

Implement AIProvider interface
Convert MCP tool schemas to Gemini function declarations
Map AIMessage ↔ Gemini's Content format
Add streaming via generate_content_stream()
Preserve existing model map (3.1-pro, 3-flash, 3.1-flash-lite)
3.3 — Claude Provider
Create python-lib/kiassist_utils/ai/claude.py:

Add anthropic>=0.30.0 to dependencies
Implement AIProvider for Claude (Sonnet 4, Opus 4)
Map tool schemas to Claude's tool format
Support extended thinking for complex operations
3.4 — OpenAI Provider
Create python-lib/kiassist_utils/ai/openai.py:

Add openai>=1.30.0 to dependencies
Implement AIProvider for GPT-4o, o3, etc.
Map tool schemas to OpenAI's function-calling format
3.5 — Tool Execution Engine
Create python-lib/kiassist_utils/ai/tool_executor.py:

ToolExecutor class that:
Receives AIToolCall from the AI response
Dispatches to MCP tools via in-process call (not over stdio)
Returns AIToolResult to feed back into conversation
Implements the agentic loop: AI response → extract tool calls → execute → feed results back → repeat until AI gives final text response
Configurable max iterations (prevent runaway loops)
Parallel tool execution when calls are independent
3.6 — API Key Management
Extend api_key.py to support multiple provider keys. Current keyring service name is kiassist. Add namespaced keys: kiassist-gemini, kiassist-claude, kiassist-openai.

Phase 4: Context Management (Claude Code-inspired)
4.1 — Conversation History Store
Create python-lib/kiassist_utils/context/init.py and python-lib/kiassist_utils/context/history.py:

ConversationStore class: append-only JSONL storage per project at {project_dir}/.kiassist/history.jsonl
Each entry: {session_id, timestamp, role, content, tool_calls, tool_results, token_count}
Session resume: load previous session's messages for /continue command
Max 100 sessions retained, older purged
4.2 — Token Counting & Context Window Management
Create python-lib/kiassist_utils/context/tokens.py:

Token estimation using provider-reported usage (from API responses) — same approach as Claude Code
ContextWindowManager class:
Tracks cumulative token usage across the conversation
Auto-summarize at 80% of context window: call the AI with "summarize the conversation so far" and replace old messages with summary
Result trimming: tool results exceeding a configurable budget (e.g., 4000 chars) are truncated with "...[truncated, full result saved to disk]"
Protected tail: always keep last 5 messages untouched during summarization
4.3 — System Prompt Construction
Create python-lib/kiassist_utils/context/prompts.py:

Three-layer system prompt (Claude Code pattern):
Base prompt: KiCad assistant identity, available tools, response format instructions
Project context: Auto-injected from project files — schematic summary (component list, nets), library paths, design rules. Read from KIASSIST.md if it exists
Dynamic context: Currently selected project, which editors are open, active schematic sheet
Store base prompt as public/agents/kicad-assistant.md
Cache project context per session (refresh on project switch)
4.4 — Project Memory (KIASSIST.md)
Create python-lib/kiassist_utils/context/memory.py:

Read/write {project_dir}/KIASSIST.md — analogous to Claude Code's CLAUDE.md
Contains: design decisions, preferred components (e.g., "use 100nF 0402 caps for decoupling"), naming conventions, constraints
AI can read this at session start and write to it when the user makes design decisions
Exposed as MCP tools (project_read_memory, project_write_memory)
4.5 — File State Cache
Create python-lib/kiassist_utils/context/file_cache.py:

LRU cache tracking which files the AI has "seen" (read via tools) — prevents redundant re-reading
Track file modification timestamps to invalidate cache when files change externally
When injecting project context, skip files already in the model's context
Phase 5: IPC Save/Reload Workflow
5.1 — Save Detection
In the IPC bridge tools, before any file edit:

Check if the target file is open in a running KiCad instance (via kicad_list_instances)
Compare file modification time with last known save time
If file appears modified (unsaved changes in KiCad), warn the user and request save
5.2 — Trigger Save
Since kipy doesn't expose a schematic save command:

Primary: Use keyboard automation (pyautogui or ctypes on Windows) to send Ctrl+S to the KiCad schematic editor window
Fallback: Prompt the user to save manually via a frontend notification
Future: Monitor KiCad IPC API updates for native save command support
5.3 — File Edit Pipeline
Orchestrated by the tool executor:

kicad_save_schematic → ensure file saved
Execute file modification tool(s) via custom parser
kicad_reload_schematic → trigger KiCad to reload
5.4 — Trigger Reload

Primary: Send Ctrl+Shift+R (or equivalent KiCad reload shortcut) via keyboard automation
Alternative: Use a file watcher approach — some KiCad versions auto-detect external modifications and prompt reload
Fallback: Notify user to reload manually
5.5 — Concurrency Safety

File lock mechanism around edit operations (prevent concurrent modifications)
Backup original file before modification (.kicad_sch.bak)
Rollback on parser errors
Phase 6: Frontend Updates
6.1 — Multi-Provider UI
Update ChatBox.vue:

Add provider selector (Gemini / Claude / OpenAI) alongside model selector
Provider-specific model dropdown
Multi-key configuration in the API settings modal
6.2 — Streaming Responses

Replace current "loading dots" with token-by-token streaming display
Render assistant messages as Markdown (add marked or markdown-it dependency)
6.3 — Tool Execution Visibility

Show tool calls in the chat as collapsible cards (tool name, arguments, result preview)
Progress indicator for multi-step tool chains ("Adding decoupling caps: 3/8 complete")
6.4 — Session Management

Session list sidebar or dropdown
"Continue previous session" option
Session export/import
6.5 — Type Updates
Update pywebview.ts with new backend API methods for multi-provider support, streaming, and session management.

Phase 7: Backend API Updates
7.1 — Refactor KiAssistAPI
Update main.py:

Replace direct Gemini calls with provider-agnostic AIProvider interface
Add send_message_stream() for streaming responses
Add set_provider(provider, model) method
Add get_providers() to list available providers
Integrate tool executor into the chat loop
Add session management methods: get_sessions(), resume_session(), export_session()
7.2 — Remove kicad-sch-api dependency
Update pyproject.toml:

Remove kicad-sch-api>=0.5.6 from dependencies
Add mcp>=1.0.0, anthropic>=0.30.0, openai>=1.30.0
Keep kicad-python>=0.6.0 for IPC
Add pyautogui>=0.9.54 for keyboard automation (save/reload)
Verification
Unit tests:

S-expression parser: round-trip tests with real KiCad files, edge cases (special chars, large files)
Schematic model: load → modify → save → re-load, verify modifications persist
Symbol/footprint models: same CRUD cycle verification
MCP tools: test each tool in isolation with fixture files
AI providers: mock API tests for message format conversion, tool schema mapping
Context manager: token counting, summarization triggers, history persistence
Integration tests:

End-to-end: "Add a decoupling cap to U1:VCC" → verify schematic file has the new component and wire
Multi-provider: same prompt yields equivalent tool calls across Gemini/Claude/OpenAI
IPC workflow: mock save/reload cycle with file modification in between
Manual validation:

Open modified .kicad_sch files in KiCad — verify they render correctly
Test the MCP server with Claude Desktop as an external client
Run the full decoupling capacitor workflow on a real STM32 project
Decisions
Custom parser over kicad-sch-api: Full control over all 4 file types, no external dependency risk, unified architecture
Unified MCP server: Single server exposes schematic + symbol + footprint + IPC tools under one namespace
Keyboard automation for save/reload: Necessary workaround since kipy lacks schematic IPC commands; documented as a temporary approach
JSONL for history: Append-only, crash-safe, same as Claude Code pattern
Three-layer system prompt: Base + project context + dynamic state for optimal AI grounding
sexpdata as reference, not dependency: Write custom parser for KiCad-specific formatting control (precision, indentation, ordering)