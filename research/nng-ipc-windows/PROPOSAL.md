# Proposal

## Recommendation

**No changes required to the current IPC path construction logic.**

The current implementation in `/home/runner/work/NewKiAssist/NewKiAssist/python-lib/kiassist_utils/kicad_ipc.py` is correct:

```python
def socket_path_to_uri(socket_file: Path) -> str:
    return f"ipc://{socket_file}"
```

This matches the official kipy implementation exactly.

---

## Verification Checklist

- [x] NNG automatically prepends `\\.\pipe\` on Windows (verified in source)
- [x] kipy uses `ipc://{path}` format without manual prefix (verified in source)
- [x] KiAssist uses same format as kipy (verified in local code)

---

## Potential Future Improvements

### 1. Windows-Specific Socket Discovery (Optional)

Current filesystem-based socket discovery may not work reliably on Windows since named pipes don't appear in the normal filesystem.

**Alternative approach for Windows:**
```python
import ctypes
from ctypes import wintypes

def discover_kicad_pipes_windows() -> List[str]:
    """Enumerate Windows named pipes matching KiCad pattern."""
    # Use Windows API to enumerate \\.\pipe\* and filter for kicad patterns
    # Example: \\.\pipe\C:\Users\<user>\AppData\Local\Temp\kicad\api.sock
    pass
```

**Priority**: Low (only needed if filesystem discovery fails on Windows)

### 2. KICAD_API_SOCKET Environment Variable Support

Consider checking `KICAD_API_SOCKET` environment variable first, matching kipy behavior:

```python
def _default_socket_path() -> str:
    env_path = os.environ.get('KICAD_API_SOCKET')
    if env_path is not None:
        return env_path
    # Fall back to platform-specific default
```

**Priority**: Medium (improves compatibility with KiCad plugin workflows)

---

## Implementation Timeline

| Phase | Task | Priority | Status |
|-------|------|----------|--------|
| 0 | Verify current implementation is correct | High | ✓ Complete |
| 1 | Add KICAD_API_SOCKET env var support | Medium | Pending |
| 2 | Test on Windows with running KiCad | High | Pending |
| 3 | Implement Windows pipe enumeration (if needed) | Low | Pending |
