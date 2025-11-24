# Analysis

## Key Source Code Evidence

### KiCad API Server Socket Path Construction [Ref 1]

From `api_server.cpp`:

```cpp
void KICAD_API_SERVER::Start()
{
    // ...
    wxFileName socket;
#ifdef __WXMAC__
    socket.AssignDir( wxS( "/tmp" ) );
#else
    socket.AssignDir( wxStandardPaths::Get().GetTempDir() );
#endif
    socket.AppendDir( wxS( "kicad" ) );
    socket.SetFullName( wxS( "api.sock" ) );
    
    // ...
    
    // On Windows, there is NO #ifndef __WINDOWS__ check for socket path
    // This means the same path construction is used on all non-macOS platforms
    
    m_server = std::make_unique<KINNG_REQUEST_SERVER>(
            fmt::format( "ipc://{}", socket.GetFullPath().ToStdString() ) );
```

**Critical observation**: KiCad uses the same path construction on Windows as on Linux. It creates an `ipc://` URL using the temp directory path, with no Windows-specific handling for named pipes.

### Windows-Specific Lock File Logic [Ref 1]

```cpp
#ifndef __WINDOWS__
    // Lock file logic for stale socket cleanup
    // This is skipped on Windows because:
    // 1. Windows doesn't use UNIX domain sockets
    // 2. Named pipes don't leave stale files
#endif
```

The `#ifndef __WINDOWS__` block only affects the lock file and stale socket cleanup - the socket path itself is NOT platform-specific.

### NNG Internal Path Transformation [Ref 4, 5]

From NNG `win_ipc.h`:
```c
#define IPC_PIPE_PREFIX "\\\\.\\pipe\\"
```

From NNG `win_ipcdial.c`:
```c
nni_asprintf(&d->path, IPC_PIPE_PREFIX "%s", url->u_path)
```

NNG automatically prepends `\\.\pipe\` to any path provided via `ipc://` URLs on Windows.

---

## Platform Behavior Comparison

| Aspect | Linux/macOS | Windows |
|--------|-------------|---------|
| Socket type | UNIX domain socket | Named pipe |
| Path construction | `%TEMP%/kicad/api.sock` | `%TEMP%\kicad\api.sock` |
| NNG URL format | `ipc:///tmp/kicad/api.sock` | `ipc://C:\Users\X\AppData\Local\Temp\kicad\api.sock` |
| Actual endpoint | File at `/tmp/kicad/api.sock` | Named pipe at `\\.\pipe\C:\Users\X\AppData\Local\Temp\kicad\api.sock` |
| File system presence | Socket file exists | **No file created** |
| Discovery method | `os.listdir()` | Named pipe enumeration |

---

## Answer Summary

### Q1: Where does KiCad create its IPC endpoint on Windows?

KiCad creates an NNG IPC endpoint with the URL format:
```
ipc://{TEMP}\kicad\api.sock
```

Where `{TEMP}` is typically `C:\Users\<username>\AppData\Local\Temp`.

NNG internally transforms this to the Windows named pipe:
```
\\.\pipe\C:\Users\<username>\AppData\Local\Temp\kicad\api.sock
```

### Q2: Does KiCad create a `.sock` file in `%TEMP%\kicad\` on Windows?

**No.** On Windows:
- NNG uses Windows named pipes, not UNIX domain sockets
- Named pipes exist only in the kernel's named pipe namespace (`\\.\pipe\`)
- No file is created in the filesystem at `%TEMP%\kicad\api.sock`
- The lock file logic (which creates `api.lock`) is skipped with `#ifndef __WINDOWS__`

### Q3: Does the `.sock` file contain the pipe path?

**N/A.** No `.sock` file is created on Windows.

### Q4: What is the actual named pipe path format?

The named pipe path follows this format:
```
\\.\pipe\{path_from_ipc_url}
```

For KiCad's default socket:
- IPC URL: `ipc://C:\Users\<user>\AppData\Local\Temp\kicad\api.sock`
- Named pipe: `\\.\pipe\C:\Users\<user>\AppData\Local\Temp\kicad\api.sock`

Note: The backslashes in the path become part of the pipe name.

---

## Implications for Socket Discovery

| Platform | Discovery Method | Feasibility |
|----------|-----------------|-------------|
| Linux/macOS | `glob('/tmp/kicad/*.sock')` | Works - socket files exist |
| Windows | `glob('%TEMP%/kicad/*.sock')` | **Fails** - no files exist |
| Windows | Named pipe enumeration | Complex, requires Windows API |
| All | `KICAD_API_SOCKET` env var | Recommended approach |

---

## Named Pipe Enumeration on Windows

To discover KiCad sockets on Windows programmatically:

```python
# Option 1: Assume default path (simplest)
import tempfile
socket_path = f'ipc://{tempfile.gettempdir()}\\kicad\\api.sock'

# Option 2: Enumerate named pipes (complex)
# Requires ctypes or pywin32 to call FindFirstFile on \\.\pipe\*
# Filter for pipes containing 'kicad' and 'api' in the name
```

The default path approach is recommended since KiCad uses a predictable path structure.
