# Report: NNG IPC Path Handling on Windows

## Objective

Determine the correct format for IPC paths with NNG (nanomsg-next-gen) on Windows, specifically whether manual `\\.\pipe\` prefixing is required.

---

## Summary

**NNG automatically prepends `\\.\pipe\` to IPC paths on Windows.** The user should NOT manually add this prefix.

| Question | Answer |
|----------|--------|
| Manual `\\.\pipe\` required? | **No** - NNG adds it internally |
| Correct URL format | `ipc://{path}` (e.g., `ipc://C:\Users\X\Temp\kicad\api.sock`) |
| KiAssist implementation correct? | **Yes** - matches official kipy |

---

## Key Findings

### 1. NNG Internal Behavior (Verified in Source Code)

From `win_ipc.h`:
```c
#define IPC_PIPE_PREFIX "\\\\.\\pipe\\"
```

From `win_ipcdial.c`:
```c
nni_asprintf(&d->path, IPC_PIPE_PREFIX "%s", url->u_path)
```

NNG extracts the path from the `ipc://` URL and prepends `\\.\pipe\` automatically.

### 2. Official Documentation Confirmation

From NNG docs (`ipc.md`):
> **TIP:** On Windows, all names are prefixed by `\\.\pipe\` and do not reside in the normal file system.

### 3. kipy Implementation Validation

The official kicad-python library uses:
```python
if platform.system() == 'Windows':
    return f'ipc://{gettempdir()}\\kicad\\api.sock'
```

This produces URLs like `ipc://C:\Users\<user>\AppData\Local\Temp\kicad\api.sock` - no manual `\\.\pipe\` prefix.

---

## Justification

The current KiAssist implementation is correct because:

1. It uses the same URL format as the official kipy library
2. NNG handles the Windows named pipe prefix conversion internally
3. Adding `\\.\pipe\` manually would result in a double prefix

---

## Gaps

| Gap | Impact | Validation Needed |
|-----|--------|-------------------|
| Windows socket file discovery | Socket enumeration via filesystem may fail | Test on Windows |
| Named pipe visibility | Pipes don't appear in normal filesystem | May need Windows API enumeration |

See `GAPS.md` for detailed hypotheses.

---

## Recommendations

1. **No code changes needed** for IPC path construction
2. **Test on Windows** with running KiCad instance to validate socket discovery
3. Consider adding `KICAD_API_SOCKET` environment variable support (optional)
