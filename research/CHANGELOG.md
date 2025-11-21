# Research Changelog

## 2025-11-21 - Initial Research

### Added
- **SCOPE.md**: Defined research objectives and constraints
- **REFERENCES.md**: Catalogued primary and secondary sources
- **ANALYSIS.md**: Detailed technical analysis of IPC protocol
- **GAPS.md**: Documented unverified assumptions and unknowns
- **PROPOSAL.md**: Implementation recommendations and architecture
- **REPORT.md**: Consolidated research findings

### Key Discoveries

1. **IPC Protocol Confirmed**:
   - Transport: NNG (Nanomsg Next-Generation)
   - Serialization: Protocol Buffers
   - Pattern: Request-Reply (Req0)

2. **Socket Locations Mapped**:
   - Linux: `/tmp/kicad/api*.sock`
   - macOS: `/tmp/kicad/api*.sock` (with Flatpak variant)
   - Windows: `%TEMP%\kicad\api*.sock` (requires `\\.\pipe\` prefix)

3. **Multi-Instance Detection**:
   - Socket enumeration via glob pattern
   - Validation via ping() with timeout
   - Project name extraction from get_open_documents()

4. **Authentication Mechanism**:
   - Token-based (KICAD_API_TOKEN)
   - Initial empty string for external clients
   - Server provides token in first response

### Sources Analyzed

- kicad-python (Official Python client) - 3 files
- atopile IPC implementation - 2 files
- KiCAD-MCP-Server - 1 file
- KiCAD official documentation (referenced)

### Metrics

- **Total references**: 6 sources
- **Code files reviewed**: 6 Python files
- **Platforms covered**: 3 (Windows, Linux, macOS)
- **API endpoints documented**: 5 core methods

### Status

- ✅ Research phase complete
- ✅ Technical specifications documented
- ✅ Implementation proposal drafted
- ⏳ Awaiting protobuf schema extraction
- ⏳ Rust NNG crate evaluation needed

### Next Actions

1. Extract `.proto` files from KiCAD source repository
2. Test `nng-rs` and `runng` crates for compatibility
3. Create proof-of-concept Rust implementation
4. Validate Windows named pipe access

---

## Format Notes

This changelog tracks research iterations and discoveries. Future updates will document:
- New findings that change recommendations
- Hypothesis verification results
- Gap resolution
- Implementation decisions
