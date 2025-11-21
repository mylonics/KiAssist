# KiCAD IPC Instance Detection Implementation

## Overview

This implementation provides robust client IPC handling for detecting and connecting to multiple KiCAD instances. It uses the official `kicad-api-rs` library (v0.1.0) which provides Rust bindings to the KiCAD IPC API.

## Architecture

### Backend (Rust)

#### Module: `src-tauri/src/kicad_ipc.rs`

The backend module handles all IPC operations:

1. **Platform-Specific Socket Discovery**
   - **Windows**: Searches for socket files in `%TEMP%\kicad\api*.sock`
   - **Linux/macOS**: Searches for socket files in `/tmp/kicad/api*.sock`

2. **Socket File Enumeration**
   - Discovers all files matching the pattern `api*.sock`
   - Supports multiple instances (e.g., `api.sock`, `api-1.sock`, `api-2.sock`)
   - Sorts results for consistent ordering

3. **Instance Validation**
   - Attempts to connect to each discovered socket
   - Validates connectivity by retrieving version information
   - Silently ignores non-responsive sockets

4. **Project Information Extraction**
   - Queries for open documents using `DocumentType::DOCTYPE_PCB`
   - Extracts project name from the first open PCB document
   - Displays "No Project Open" if no documents are found

5. **URI Format Handling**
   - Uses `ipc://` scheme for all platforms
   - NNG library handles platform-specific pipe conversion internally
   - Windows pipe path (`\\.\pipe\`) is managed by NNG automatically

#### Data Structure

```rust
pub struct KiCadInstance {
    pub socket_path: String,    // Full IPC URI (e.g., "ipc:///tmp/kicad/api.sock")
    pub project_name: String,    // Project name or "No Project Open"
    pub display_name: String,    // Formatted display string
    pub version: String,         // KiCAD version string
}
```

#### Tauri Command

```rust
#[tauri::command]
fn detect_kicad_instances() -> Vec<KiCadInstance>
```

This command is exposed to the frontend and returns a list of all detected and validated KiCAD instances.

### Frontend (Vue/TypeScript)

#### Component: `src/components/KiCadInstanceSelector.vue`

The UI component provides instance selection with the following features:

1. **Auto-Detection**
   - Automatically detects instances when the component mounts
   - Shows loading spinner during detection

2. **Single Instance Handling**
   - Auto-selects when only one instance is found
   - Displays instance information with checkmark icon
   - Shows "Connected to KiCAD" status

3. **Multiple Instance Handling**
   - Presents dropdown select menu
   - Displays all detected instances with project names
   - Shows detailed information for selected instance

4. **Error Handling**
   - Displays user-friendly error messages
   - Suggests starting KiCAD if no instances found

5. **Manual Refresh**
   - Refresh button to re-scan for instances
   - Useful when KiCAD is started after the application

## Platform-Specific Implementation

### Windows

- **Socket Directory**: `%TEMP%\kicad\` (typically `C:\Users\<user>\AppData\Local\Temp\kicad\`)
- **Socket Files**: `api.sock`, `api-1.sock`, etc.
- **IPC URI Format**: `ipc://C:\Users\<user>\AppData\Local\Temp\kicad\api.sock`
- **Named Pipe Conversion**: Handled internally by NNG library

### Linux/macOS

- **Socket Directory**: `/tmp/kicad/`
- **Socket Files**: `api.sock`, `api-1.sock`, etc.
- **IPC URI Format**: `ipc:///tmp/kicad/api.sock`
- **Unix Domain Sockets**: Used directly by NNG

## Usage Example

When a user opens the application:

1. The `KiCadInstanceSelector` component mounts and calls `detectInstances()`
2. The frontend invokes the Rust `detect_kicad_instances` command
3. The backend:
   - Searches the platform-specific socket directory
   - Finds all `api*.sock` files
   - Attempts to connect to each socket
   - Retrieves version and project information
   - Returns valid instances to the frontend
4. The frontend displays:
   - Single instance: Auto-selected with confirmation
   - Multiple instances: Dropdown menu for selection
   - No instances: Error message with instructions

## Testing

Unit tests are provided in `src-tauri/src/kicad_ipc.rs`:

- `test_get_ipc_socket_dir()`: Verifies platform-specific socket directory
- `test_socket_path_to_uri()`: Validates IPC URI format construction

Run tests with:
```bash
cd src-tauri
cargo test
```

## Dependencies

### Rust
- `kicad-api-rs = "0.1.0"` - Official KiCAD IPC API bindings
- `serde` - Serialization for Tauri commands
- `tauri` - Application framework

### TypeScript/Vue
- `@tauri-apps/api` - Tauri frontend API
- `vue` - UI framework

## Security Considerations

1. **File System Access**: Only reads from platform-specific temp directories
2. **Network Connections**: Only connects to local IPC sockets (no network exposure)
3. **Input Validation**: Socket paths are validated before connection attempts
4. **Error Handling**: Failed connections are silently ignored (no sensitive information leaked)
5. **No Credentials**: IPC connections use token-based authentication managed by KiCAD

## Future Enhancements

Potential improvements for future versions:

1. **Connection Persistence**: Store selected instance for reconnection
2. **Instance Monitoring**: Detect when KiCAD instances are closed
3. **Multi-Instance Operations**: Support operations across multiple instances
4. **Connection Status Indicator**: Real-time connection health monitoring
5. **Advanced Filtering**: Filter instances by project type or status

## Known Limitations

1. **No Hot-Reload Detection**: Requires manual refresh if KiCAD starts after the app
2. **First Instance Only**: Currently only uses the first open PCB for project name
3. **No Connection Pooling**: Each detection creates new connections

## References

- [KiCAD API Documentation](https://dev-docs.kicad.org)
- [kicad-api-rs Repository](https://gitlab.com/kicad/code/kicad-rs)
- [NNG Library](https://nng.nanomsg.org/)
