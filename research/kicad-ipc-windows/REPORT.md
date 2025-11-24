# Report: KiCad IPC API on Windows

## Objective

Determine how KiCad creates and manages its IPC API endpoint on Windows, specifically regarding file creation, named pipe paths, and socket discovery.

---

## Summary

| Question | Answer |
|----------|--------|
| Does KiCad create a `.sock` file on Windows? | **No** - Windows uses named pipes, not files |
| Socket path construction | `ipc://{TEMP}\kicad\api.sock` |
| Actual named pipe path | `\\.\pipe\C:\Users\<user>\AppData\Local\Temp\kicad\api.sock` |
| File-based discovery on Windows | **Not viable** - no files are created |

---

## Key Findings

### 1. No `.sock` File on Windows

KiCad's API server creates an NNG IPC endpoint using the same path construction on all platforms:

```cpp
socket.AssignDir( wxStandardPaths::Get().GetTempDir() );
socket.AppendDir( wxS( "kicad" ) );
socket.SetFullName( wxS( "api.sock" ) );
```

However, on Windows, NNG uses **Windows named pipes** instead of UNIX domain sockets. Named pipes:
- Exist only in the kernel's pipe namespace (`\\.\pipe\`)
- Do **not** create any file in the filesystem
- Cannot be discovered by directory listing

### 2. Named Pipe Path Format

NNG automatically transforms the IPC URL path to a Windows named pipe:

| Component | Value |
|-----------|-------|
| IPC URL | `ipc://C:\Users\<user>\AppData\Local\Temp\kicad\api.sock` |
| NNG prefix | `\\.\pipe\` |
| Final pipe name | `\\.\pipe\C:\Users\<user>\AppData\Local\Temp\kicad\api.sock` |

The transformation is automatic - **do not** manually add `\\.\pipe\` to the path.

### 3. Lock File Not Created on Windows

The socket lock file logic (`api.lock`) is wrapped in `#ifndef __WINDOWS__`, meaning:
- No lock file is created on Windows
- No file cleanup is needed on Windows
- The only artifact is the named pipe itself (managed by the kernel)

### 4. Multiple Instance Handling

When `api.sock` is already in use, KiCad falls back to PID-based naming:
```cpp
socket.SetFullName( wxString::Format( wxS( "api-%lu.sock" ), ::wxGetProcessId() ) );
```

This creates pipes like:
- First instance: `\\.\pipe\...\kicad\api.sock`
- Second instance: `\\.\pipe\...\kicad\api-12345.sock`

---

## Justification

The current KiAssist implementation for IPC path construction is correct because:

1. It matches the official kipy library implementation
2. NNG handles Windows named pipe prefix internally
3. No manual `\\.\pipe\` prefix is required

---

## Gaps

| Gap | Impact | Status |
|-----|--------|--------|
| Windows socket discovery via file enumeration | Cannot discover sockets via `glob()` | Confirmed issue |
| Multi-instance enumeration on Windows | Cannot easily find all KiCad instances | Open - requires Windows API |

See `GAPS.md` for detailed hypotheses and validation needs.

---

## Recommendations

1. **Do not** attempt file-based socket discovery on Windows
2. **Use** default socket path (`ipc://{TEMP}\kicad\api.sock`) directly
3. **Support** `KICAD_API_SOCKET` environment variable for custom paths
4. **Document** that KiCad must be running before KiAssist can connect
5. **Consider** adding manual socket path configuration in UI for advanced users
