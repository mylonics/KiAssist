# Technical Analysis

## IPC Protocol Comparison

| Aspect | Details | Source |
|--------|---------|--------|
| **Transport** | NNG (Nanomsg Next-Generation) with Request-Reply pattern | kipy/client.py [1] |
| **Serialization** | Protocol Buffers (protobuf) | kipy/client.py [1] |
| **Socket Type** | Unix Domain Sockets (Linux/macOS), Named Pipes (Windows) | paths.py [2] |
| **Connection Pattern** | Client connects via `pynng.Req0()` | kipy/client.py [1] |

## Platform-Specific IPC Paths

| Platform | Base Path | Pattern | Example |
|----------|-----------|---------|---------|
| **Linux** | `/tmp/kicad/` | `api*.sock` | `/tmp/kicad/api.sock`, `/tmp/kicad/api-1.sock` |
| **macOS** | `/tmp/kicad/` | `api*.sock` | `/tmp/kicad/api.sock` |
| **macOS (Flatpak)** | `~/.var/app/org.kicad.KiCad/cache/tmp/kicad/` | `api*.sock` | Special case for Flatpak installation |
| **Windows** | `%TEMP%\kicad\` | `api*.sock` | `C:\Users\...\AppData\Local\Temp\kicad\api.sock` |
| **Windows (Connection)** | Prepend `\\.\pipe\` | N/A | Must use Windows pipe prefix |

## Socket Discovery Algorithm

### Linux/macOS
```
1. Get base path: /tmp/kicad/
2. Glob pattern: api*.sock
3. Filter: Check file exists and is socket
4. Return: List of socket paths
```

### Windows
```
1. List directory: \\.\pipe\
2. Filter: Files relative to %TEMP%\kicad\
3. Pattern match: api*.sock
4. Return: List of named pipe paths
```

## Authentication Mechanism

| Component | Purpose | Flow |
|-----------|---------|------|
| **KICAD_API_TOKEN** | Environment variable | Set by KiCAD when launching plugins |
| **Initial Token** | Empty string for external clients | Client sends empty token initially |
| **Token Exchange** | Server returns token in first response | Client stores and reuses token |
| **Client Name** | Unique identifier | Auto-generated or user-specified |

## API Endpoints

| Method | Purpose | Request Type | Response Type |
|--------|---------|--------------|---------------|
| `ping()` | Connection test | `Ping` | `Empty` |
| `get_version()` | Get KiCAD version | `GetVersion` | `GetVersionResponse` |
| `get_open_documents()` | List open documents | `GetOpenDocuments` | `GetOpenDocumentsResponse` |
| `get_board()` | Get active PCB | N/A | `Board` object |
| `get_project()` | Get project info | Document specifier | `Project` object |

## Instance Detection Strategy

### Single Instance Detection
```
1. Try default socket path
2. Attempt connection with timeout (5000ms recommended)
3. Send ping() command
4. Return connection status
```

### Multi-Instance Detection
```
1. Enumerate all socket files matching pattern
2. For each socket:
   a. Create KiCad client
   b. Attempt ping() with timeout
   c. On success, add to valid instances list
3. Return list of connected clients
```

### Project Name Retrieval
```
For each connected instance:
1. Call get_open_documents(DocumentType.DOCTYPE_PCB)
2. If documents exist:
   a. Get document[0].project.path
   b. Extract project name from path
3. Return project name or "Unnamed"
```

## Configuration Requirements

| Setting | Location | Default | Purpose |
|---------|----------|---------|---------|
| `api.enable_server` | `kicad_common.json` | `false` | Enable IPC API |
| Config Path (Linux) | `~/.config/kicad/9.0/` | N/A | User settings |
| Config Path (Windows) | `%APPDATA%\kicad\9.0\` | N/A | User settings |
| Config Path (macOS) | `~/Library/Preferences/kicad/9.0/` | N/A | User settings |

## Performance Characteristics

| Operation | Timeout | Blocking | Notes |
|-----------|---------|----------|-------|
| Connection | 2000ms (default) | Yes | Configurable via timeout_ms |
| Ping | 5000ms | Yes | Used for discovery |
| API Call | 2000ms | Yes | Per-request timeout |
| Socket Discovery | N/A | No | File system operation |

## Error Handling

| Error Type | Cause | Handling |
|------------|-------|----------|
| `ConnectionError` | Socket not accessible | Return empty instance list |
| `ApiError` | Invalid request/response | Log and skip instance |
| `FileNotFoundError` | Config path missing | Use platform defaults |
| `pynng.NNGException` | Transport layer error | Treat as disconnection |
