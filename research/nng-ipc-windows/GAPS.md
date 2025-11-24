# Knowledge Gaps

## Resolved Questions

### Q1: Does NNG require manual `\\.\pipe\` prefix?
- **Answer**: No. NNG automatically prepends `\\.\pipe\` to the path on Windows.
- **Confidence**: 100% (verified in source code)
- **Source**: `win_ipcdial.c` line with `IPC_PIPE_PREFIX "%s"`

### Q2: What is the correct IPC URL format on Windows?
- **Answer**: `ipc://{path}` where `{path}` is the file path without `\\.\pipe\`
- **Confidence**: 100% (verified in kipy official implementation)
- **Source**: kipy `kicad.py` `_default_socket_path()` function

---

## Remaining Gaps

### Gap 1: KiCad Socket File Discovery on Windows
- **Question**: Does KiCad actually create a file at `%TEMP%\kicad\api.sock` on Windows?
- **[HYPOTHESIS]**: KiCad creates the named pipe but may not create a visible file in the filesystem
- **Impact**: Socket discovery via filesystem scanning may not work on Windows
- **Validation**: Test on Windows with running KiCad instance

### Gap 2: Multiple KiCad Instance Detection on Windows
- **Question**: How are multiple KiCad instances differentiated on Windows?
- **[HYPOTHESIS]**: KiCad uses `api.sock`, `api-1.sock`, `api-2.sock` naming pattern
- **Impact**: Multi-instance support may require different detection mechanism on Windows
- **Validation**: Run multiple KiCad instances on Windows and inspect named pipes

### Gap 3: Named Pipe Enumeration on Windows
- **Question**: Can we programmatically enumerate `\\.\pipe\*` to find KiCad sockets?
- **[HYPOTHESIS]**: Windows API `FindFirstFile("\\.\pipe\*")` or similar can list pipes
- **Impact**: May need Windows-specific socket discovery code
- **Validation**: Test pipe enumeration on Windows

### Gap 4: Path Separator Handling
- **Question**: Does NNG normalize path separators (`/` vs `\`) on Windows?
- **[HYPOTHESIS]**: NNG passes the path directly to CreateFileA, which accepts both
- **Impact**: Low - Windows API handles both separators
- **Validation**: Test with forward slashes in Windows IPC path
