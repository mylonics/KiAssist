# Knowledge Gaps

## Resolved Questions

### Q1: Does KiCad create a .sock file on Windows?
- **Answer**: No. NNG uses Windows named pipes which do not create filesystem entries.
- **Confidence**: 100%
- **Source**: KiCad source code analysis + NNG documentation [Ref 1, 3, 4]

### Q2: What is the named pipe path format?
- **Answer**: `\\.\pipe\C:\Users\<user>\AppData\Local\Temp\kicad\api.sock`
- **Confidence**: 95% (derived from source code analysis)
- **Source**: NNG source code showing `IPC_PIPE_PREFIX` prepending [Ref 4, 5]

### Q3: Does KiCad use the same path structure on Windows?
- **Answer**: Yes. KiCad uses `wxStandardPaths::GetTempDir()` + `/kicad/api.sock` on all non-macOS platforms.
- **Confidence**: 100%
- **Source**: `api_server.cpp` source code analysis [Ref 1]

---

## Remaining Gaps

### Gap 1: Windows Named Pipe Discovery for Multiple Instances
- **Question**: How do we enumerate all KiCad instances on Windows?
- **[HYPOTHESIS]**: The naming pattern `api.sock`, `api-{PID}.sock` is consistent. Pipe enumeration requires Windows-specific approaches such as using `os.listdir(r'\\.\pipe')` in Python, or iterating through potential PID-based paths.
- **Impact**: Multi-instance support may require Windows-specific discovery code
- **Validation**: Test on Windows with multiple KiCad instances running

### Gap 2: KiCad Folder Creation on Windows
- **Question**: Does KiCad create the `%TEMP%\kicad\` folder on Windows even though no socket file is placed there?
- **[HYPOTHESIS]**: Yes, `PATHS::EnsurePathExists()` is called before socket creation, creating the folder structure.
- **Impact**: Low - folder presence doesn't help with discovery
- **Validation**: Test on Windows with KiCad running

### Gap 3: Path Separator Normalization
- **Question**: Does NNG normalize backslashes to forward slashes in the pipe name?
- **[HYPOTHESIS]**: No, backslashes are passed directly to `CreateNamedPipe`, making them part of the pipe name.
- **Impact**: Low - Windows named pipe API handles mixed separators
- **Validation**: Test connection with both separator styles

### Gap 4: UAC and Security Context
- **Question**: Can a standard user process connect to a named pipe created by an elevated KiCad instance?
- **[HYPOTHESIS]**: Yes, KiCad does not appear to set restrictive security descriptors on the pipe.
- **Impact**: Medium - could affect users running KiCad as administrator
- **Validation**: Test connection from non-elevated process to elevated KiCad

### Gap 5: Long Path Support
- **Question**: Does the named pipe work correctly if the temp path exceeds 260 characters?
- **[HYPOTHESIS]**: May fail on older Windows versions without long path support enabled.
- **Impact**: Low - temp paths rarely exceed 260 characters
- **Validation**: Test with artificially long temp path

---

## Hypotheses Summary

| ID | Hypothesis | Status | Priority |
|----|-----------|--------|----------|
| H1 | No .sock file is created on Windows | **Confirmed** | - |
| H2 | Named pipe path is `\\.\pipe\{TEMP}\kicad\api.sock` | High confidence | High |
| H3 | Multi-instance uses `api-{PID}.sock` pattern | Unconfirmed | Medium |
| H4 | `%TEMP%\kicad\` folder is created | Unconfirmed | Low |
| H5 | Standard user can connect to elevated KiCad | Unconfirmed | Low |
