# Analysis

## Key Finding: NNG Automatically Prepends `\\.\pipe\` on Windows

### Source Code Evidence

**From `win_ipc.h` [Ref 2]:**
```c
#define IPC_PIPE_PREFIX "\\\\.\\pipe\\"
```

**From `win_ipcdial.c` [Ref 3]:**
```c
nng_err
nni_ipc_dialer_alloc(nng_stream_dialer **dp, const nng_url *url)
{
    // ...
    if ((rv = nni_asprintf(&d->path, IPC_PIPE_PREFIX "%s", url->u_path)) != 0) {
        // ...
    }
    // ...
}
```

This confirms that NNG automatically prepends `\\.\pipe\` to the path extracted from the URL.

### Official Documentation Excerpt [Ref 1]

> **TIP:** On Windows, all names are prefixed by `\\.\pipe\` and do not reside in the normal file system.

This explicitly states that NNG handles the prefix internally.

---

## IPC URL Format Comparison

| Platform | User-Provided URL | Actual Named Pipe Path | Notes |
|----------|------------------|----------------------|-------|
| Windows | `ipc://kicad/api.sock` | `\\.\pipe\kicad\api.sock` | NNG prepends prefix automatically |
| Windows | `ipc://C:\Users\X\Temp\kicad\api.sock` | `\\.\pipe\C:\Users\X\Temp\kicad\api.sock` | Full path becomes pipe name |
| Linux/macOS | `ipc:///tmp/kicad/api.sock` | `/tmp/kicad/api.sock` | Uses UNIX domain sockets |

---

## kipy (kicad-python) Implementation [Ref 4]

```python
def _default_socket_path() -> str:
    path = os.environ.get('KICAD_API_SOCKET')
    if path is not None:
        return path
    if platform.system() == 'Windows':
        return f'ipc://{gettempdir()}\\kicad\\api.sock'
    else:
        return 'ipc:///tmp/kicad/api.sock'
```

**Analysis:**
- kipy uses `ipc://` scheme directly with the file path
- Does NOT prepend `\\.\pipe\` manually
- Example Windows path: `ipc://C:\Users\<user>\AppData\Local\Temp\kicad\api.sock`
- NNG handles the conversion to `\\.\pipe\C:\Users\<user>\AppData\Local\Temp\kicad\api.sock`

---

## Path Comparison Table

| Implementation | Windows URL Format | Correct? |
|---------------|-------------------|----------|
| kipy (official) | `ipc://{TEMP}\kicad\api.sock` | ✓ Yes |
| KiAssist current | `ipc://{socket_file}` | ✓ Yes |
| Manual `\\.\pipe\` prefix | `ipc://\\.\pipe\{TEMP}\kicad\api.sock` | ✗ Wrong (double prefix) |

---

## Critical Notes

1. **Do NOT manually add `\\.\pipe\`** - NNG adds this internally
2. **Use forward slashes or escaped backslashes** in the path
3. **Windows named pipes do not use the filesystem** - the path becomes the pipe name
4. **The `.sock` extension is arbitrary** - it's just part of the pipe name on Windows
