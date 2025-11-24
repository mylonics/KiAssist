# Proposal

## Recommendation

Based on source code analysis, the following approach is recommended for KiAssist Windows support:

### 1. Default Socket Path Construction

Use the same path format as kipy (official KiCad Python library):

```python
import platform
from tempfile import gettempdir

def get_default_socket_path() -> str:
    if platform.system() == 'Windows':
        return f'ipc://{gettempdir()}\\kicad\\api.sock'
    else:
        return 'ipc:///tmp/kicad/api.sock'
```

### 2. Socket Discovery Strategy

| Platform | Strategy |
|----------|----------|
| Linux/macOS | Enumerate files in `/tmp/kicad/*.sock` |
| Windows | Use default path OR `KICAD_API_SOCKET` environment variable |

For Windows, filesystem enumeration is not viable because:
- Named pipes don't create files in the temp directory
- Pipe enumeration requires Windows-specific API calls (complex)

### 3. Environment Variable Support

Always check `KICAD_API_SOCKET` environment variable first:

```python
import os

socket_path = os.environ.get('KICAD_API_SOCKET')
if socket_path is None:
    socket_path = get_default_socket_path()
```

This allows users to specify custom paths and is consistent with kipy behavior.

### 4. Multi-Instance Handling

For multiple KiCad instances on Windows:
- First instance: `api.sock`
- Subsequent instances: `api-{PID}.sock`

Without pipe enumeration, multi-instance support options:
1. Use `KICAD_API_SOCKET` environment variable
2. Try known PID-based patterns
3. Accept manual socket path configuration

---

## Implementation Changes Required

### No Changes Needed
- Current IPC URL format is correct
- NNG handles `\\.\pipe\` prefix automatically

### Socket Discovery (Windows-specific)
- **Do NOT** enumerate `%TEMP%\kicad\` for `.sock` files
- **DO** use default path directly or environment variable

### UI/UX Considerations
- Add clear messaging when socket not found on Windows
- Consider adding manual socket path input option
- Document that KiCad must be running before KiAssist connects

---

## Justification

1. **Consistency with kipy**: Using the same path format ensures compatibility
2. **Simplicity**: Default path is predictable and reliable
3. **Flexibility**: Environment variable allows advanced users to customize
4. **No double-prefix**: NNG handles Windows pipe prefix internally
