# KiCAD IPC Research Report

## Objective

Research KiCAD's IPC API for implementing instance detection and project selection in a Tauri + Vue application.

## Summary

KiCAD 9.0+ provides an IPC API using **NNG (Nanomsg Next-Generation)** transport with **Protocol Buffers** serialization. Each KiCAD instance creates a socket file (Unix) or named pipe (Windows) that external applications can connect to. Multiple instances are detectable via socket file enumeration.

### Key Findings

| Aspect | Specification |
|--------|---------------|
| **Protocol** | NNG Request-Reply pattern with Protocol Buffers |
| **Transport** | Unix Domain Sockets (Linux/macOS), Named Pipes (Windows) |
| **Socket Location** | `/tmp/kicad/api*.sock` (Unix), `%TEMP%\kicad\api*.sock` (Windows) |
| **Windows Prefix** | `\\.\pipe\` required for named pipe connections |
| **Authentication** | Token-based (initial empty string, server provides token) |
| **Multi-Instance** | Supported via numbered socket files (api-1.sock, api-2.sock, etc.) |
| **Version** | Requires KiCAD 9.0 or later |

## Justification

### Recommended Implementation: Rust + NNG

**Rationale**: Native Rust implementation provides:
1. **Performance**: Direct NNG bindings without IPC overhead
2. **Type Safety**: Rust's type system prevents protocol errors
3. **Integration**: Aligns with Tauri's Rust backend architecture
4. **Deployment**: Single binary with no Python runtime dependency

### Platform-Specific Socket Paths

| Platform | Default Path | Alternative Path |
|----------|--------------|------------------|
| Linux | `/tmp/kicad/api*.sock` | - |
| macOS | `/tmp/kicad/api*.sock` | `~/.var/app/org.kicad.KiCad/cache/tmp/kicad/api*.sock` (Flatpak) |
| Windows | `%TEMP%\kicad\api*.sock` | Must prepend `\\.\pipe\` for connection |

### Connection Workflow

```
1. Discovery Phase:
   - Enumerate socket files matching pattern
   - Filter: Unix sockets (Linux/macOS) or named pipes (Windows)
   
2. Validation Phase:
   - For each socket: Create NNG client connection
   - Send ping() command with 5000ms timeout
   - Discard unresponsive sockets
   
3. Instance Information:
   - Call get_open_documents(DocumentType.DOCTYPE_PCB)
   - Extract project.path and project.board_filename
   - Display project name to user
   
4. Selection:
   - User selects instance from list
   - Establish persistent connection
   - Store token for subsequent API calls
```

### API Endpoints

| Method | Purpose | Parameters | Returns |
|--------|---------|------------|---------|
| `ping()` | Validate connection | None | `Empty` |
| `get_version()` | Get KiCAD version | None | `KiCadVersion` |
| `get_open_documents(type)` | List open files | `DocumentType` | `List<DocumentSpecifier>` |
| `get_board()` | Get active PCB | None | `Board` object |
| `get_project(doc)` | Get project details | `DocumentSpecifier` | `Project` object |

### Implementation Stack

```
Frontend (Vue):
├── Instance List Component
│   ├── Auto-refresh every 2s
│   ├── Display: Project name, version, socket path
│   └── Selection handler
│
Backend (Rust/Tauri):
├── nng-rs or runng (NNG transport)
├── prost (Protocol Buffers)
├── tokio (Async runtime)
└── Commands:
    ├── list_kicad_instances() -> Vec<InstanceInfo>
    └── connect_to_instance(socket_path) -> Result<()>
```

## Gaps

### Unverified Hypotheses

1. **[HYPOTHESIS]** Windows named pipe enumeration via `\\.\pipe\` directory listing
   - **Risk**: Medium - May require Windows API calls
   - **Mitigation**: Test on Windows 10/11, fallback to registry lookup

2. **[HYPOTHESIS]** Socket naming convention follows `api.sock`, `api-1.sock`, `api-2.sock`
   - **Risk**: Low - Observed in production code
   - **Mitigation**: Use glob pattern `api*.sock` for flexibility

3. **[HYPOTHESIS]** Project name available in `document.project.path`
   - **Risk**: Low - Confirmed in atopile implementation
   - **Mitigation**: Fallback to "Unnamed Project" if unavailable

### Missing Information

1. **Protobuf Schema Files**: 
   - Not included in kicad-python library
   - **Action**: Extract from KiCAD source repository
   - **Impact**: Critical for Rust implementation

2. **Rust NNG Crate Selection**:
   - Candidates: `nng-rs`, `runng`
   - **Action**: Evaluate compatibility with KiCAD's NNG version
   - **Impact**: High - Affects implementation feasibility

3. **Multi-Document Behavior**:
   - Can one instance have multiple projects open?
   - **Action**: Test KiCAD 9.0 multi-document support
   - **Impact**: Medium - Affects UI design

## References

1. **kicad-python** (Official): https://github.com/timblakely/kicad-python - MIT License
2. **atopile IPC**: https://github.com/atopile/atopile - Production implementation
3. **KiCAD Docs**: https://dev-docs.kicad.org/en/apis-and-binding/ipc-api/
4. **pynng**: NNG bindings for Python (reference implementation)
5. **Protocol Buffers**: Google protobuf specification

---

## Actionable Next Steps

### Immediate (Week 1)
1. ✅ Research complete - documented in this report
2. Extract protobuf schema from KiCAD source
3. Evaluate Rust NNG crates (`nng-rs` vs `runng`)
4. Create proof-of-concept: Rust NNG connection to KiCAD

### Short-term (Week 2)
5. Implement socket discovery for all platforms
6. Add protobuf message definitions
7. Create Tauri commands for instance listing
8. Build Vue component for instance selection

### Testing
9. Test on Windows 10/11 (named pipes)
10. Test on Linux (Ubuntu, Fedora)
11. Test on macOS (Intel and ARM)
12. Verify Flatpak compatibility (Linux/macOS)

---

**Report Generated**: 2025-11-21  
**KiCAD Version Target**: 9.0+  
**Implementation Language**: Rust (Backend), TypeScript/Vue (Frontend)  
**Status**: Research phase complete, ready for implementation
