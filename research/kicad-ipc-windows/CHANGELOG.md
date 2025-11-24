# Changelog

## 2025-11-24

### Initial Research

- Created research documentation for KiCad IPC API Windows behavior
- Analyzed KiCad source code (`api_server.cpp`, `kinng.cpp`)
- Analyzed NNG library source code (`win_ipc.h`, `win_ipcdial.c`)
- Reviewed official kipy library implementation

### Key Findings

1. **Confirmed**: No `.sock` file is created on Windows
2. **Confirmed**: KiCad uses same path construction on all non-macOS platforms
3. **Confirmed**: NNG automatically prepends `\\.\pipe\` prefix on Windows
4. **Identified**: Windows socket discovery cannot use file enumeration

### Files Created

- `SCOPE.md` - Research objectives and constraints
- `REFERENCES.md` - Source code and documentation references
- `ANALYSIS.md` - Detailed technical analysis
- `GAPS.md` - Knowledge gaps and hypotheses
- `PROPOSAL.md` - Implementation recommendations
- `REPORT.md` - Executive summary of findings
