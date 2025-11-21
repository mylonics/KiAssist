# Implementation Proposal

## Recommended Approach

Use **Rust with NNG bindings** for Tauri backend implementation.

## Justification

### Architecture Decision

| Criterion | Rust Implementation | JavaScript/Python Bridge | Rationale |
|-----------|---------------------|-------------------------|-----------|
| **Performance** | Native speed | IPC overhead | Direct NNG binding faster than subprocess |
| **Reliability** | Type safety | Runtime errors | Rust type system prevents protocol errors |
| **Deployment** | Single binary | Python dependency | Tauri already uses Rust backend |
| **Maintenance** | Unified codebase | Multi-language | Fewer toolchain dependencies |

### Technology Stack

1. **Transport**: `nng-rs` or `runng` crate for NNG protocol
2. **Serialization**: `prost` crate for Protocol Buffers
3. **Async Runtime**: `tokio` for non-blocking socket operations
4. **Error Handling**: `thiserror` for typed error handling

## Implementation Plan

### Phase 1: Core IPC Client (Week 1)

```rust
// Proposed API
pub struct KiCadClient {
    socket_path: PathBuf,
    client_name: String,
    token: Option<String>,
    connection: Option<NngSocket>,
}

impl KiCadClient {
    pub fn connect(socket_path: PathBuf) -> Result<Self>;
    pub fn ping(&self) -> Result<()>;
    pub fn get_version(&self) -> Result<String>;
    pub fn get_open_documents(&self) -> Result<Vec<Document>>;
}
```

### Phase 2: Instance Discovery (Week 1-2)

```rust
pub struct InstanceDiscovery {
    platform: Platform,
}

impl InstanceDiscovery {
    pub fn find_all_instances() -> Result<Vec<KiCadInstance>>;
    pub fn get_socket_paths() -> Result<Vec<PathBuf>>;
}

pub struct KiCadInstance {
    socket_path: PathBuf,
    project_name: Option<String>,
    version: String,
}
```

### Phase 3: Tauri Integration (Week 2)

```rust
#[tauri::command]
async fn list_kicad_instances() -> Result<Vec<InstanceInfo>, String> {
    let discovery = InstanceDiscovery::new();
    let instances = discovery.find_all_instances()?;
    
    Ok(instances.iter().map(|i| InstanceInfo {
        socket_path: i.socket_path.display().to_string(),
        project_name: i.project_name.clone(),
        version: i.version.clone(),
    }).collect())
}

#[tauri::command]
async fn connect_to_instance(socket_path: String) -> Result<(), String> {
    let client = KiCadClient::connect(socket_path.into())?;
    // Store client in app state
    Ok(())
}
```

## Platform-Specific Implementation

### Linux/macOS Socket Discovery

```rust
fn find_unix_sockets() -> Result<Vec<PathBuf>> {
    let base_path = Path::new("/tmp/kicad");
    
    if !base_path.exists() {
        return Ok(vec![]);
    }
    
    glob::glob(base_path.join("api*.sock").to_str().unwrap())?
        .filter_map(Result::ok)
        .filter(|p| p.is_socket())
        .collect()
}
```

### Windows Named Pipe Discovery

```rust
#[cfg(target_os = "windows")]
fn find_windows_pipes() -> Result<Vec<PathBuf>> {
    use std::os::windows::fs::MetadataExt;
    
    let pipe_prefix = r"\\.\pipe\";
    let base_name = "kicad";
    
    // List pipes using Windows API or directory enumeration
    list_named_pipes(pipe_prefix)
        .into_iter()
        .filter(|name| name.contains(base_name))
        .map(|name| PathBuf::from(format!("{}{}", pipe_prefix, name)))
        .collect()
}
```

### Connection String Format

```rust
fn format_socket_uri(path: &Path) -> String {
    #[cfg(target_os = "windows")]
    {
        format!("ipc://{}", path.display())
    }
    
    #[cfg(not(target_os = "windows"))]
    {
        format!("ipc://{}", path.display())
    }
}
```

## Protobuf Schema Integration

### Option 1: Pre-generated Code
- Extract `.proto` files from KiCAD source
- Generate Rust code using `prost-build`
- Include in repository as `src/proto/`

### Option 2: Runtime Protobuf
- Use `prost-reflect` for dynamic protobuf
- Load schema at runtime
- Higher flexibility, slight performance cost

**Recommendation**: Option 1 for type safety and performance

## Error Handling Strategy

```rust
#[derive(Debug, thiserror::Error)]
pub enum IpcError {
    #[error("Connection failed: {0}")]
    ConnectionError(String),
    
    #[error("Socket not found at {0}")]
    SocketNotFound(PathBuf),
    
    #[error("API error: {0}")]
    ApiError(String),
    
    #[error("Timeout waiting for response")]
    Timeout,
    
    #[error("Protobuf decode error: {0}")]
    DecodeError(#[from] prost::DecodeError),
}
```

## Vue Frontend Integration

```typescript
// types/kicad.ts
export interface KiCadInstance {
  socketPath: string;
  projectName?: string;
  version: string;
}

// composables/useKiCad.ts
export function useKiCad() {
  const instances = ref<KiCadInstance[]>([]);
  const selectedInstance = ref<KiCadInstance | null>(null);
  
  const refreshInstances = async () => {
    instances.value = await invoke('list_kicad_instances');
  };
  
  const connectToInstance = async (instance: KiCadInstance) => {
    await invoke('connect_to_instance', { 
      socketPath: instance.socketPath 
    });
    selectedInstance.value = instance;
  };
  
  return {
    instances,
    selectedInstance,
    refreshInstances,
    connectToInstance,
  };
}
```

## Testing Strategy

### Unit Tests
- Socket path resolution on each platform
- Protobuf message encoding/decoding
- Error handling paths

### Integration Tests
- Mock NNG socket server
- Full request-response cycle
- Multi-instance detection

### Platform Tests
- Windows named pipe access
- Linux/macOS socket permissions
- Flatpak special cases

## Deployment Considerations

### Dependencies
- Add to `Cargo.toml`: `nng`, `prost`, `tokio`, `glob`
- No Python runtime required
- All dependencies static-linked in Tauri binary

### Configuration
```toml
# tauri.conf.json allowlist
{
  "tauri": {
    "allowlist": {
      "fs": {
        "scope": [
          "/tmp/kicad/**",
          "$TEMP/kicad/**",
          "$HOME/.var/app/org.kicad.KiCad/cache/tmp/kicad/**"
        ]
      }
    }
  }
}
```

## Performance Targets

| Operation | Target | Worst Case |
|-----------|--------|------------|
| Instance discovery | < 100ms | < 500ms |
| Single instance connection | < 50ms | < 200ms |
| API call (ping) | < 10ms | < 100ms |
| Full refresh cycle | < 200ms | < 1s |

## Security Measures

1. **Path Validation**: Ensure socket paths are within expected directories
2. **Timeout Enforcement**: Prevent indefinite hangs on dead sockets
3. **Token Storage**: Store tokens in memory only, never persist
4. **Permission Checks**: Verify socket file permissions before connection

## Alternative: Fallback to Python Bridge

If Rust NNG integration proves difficult:

```rust
#[tauri::command]
async fn list_kicad_instances_python() -> Result<Vec<InstanceInfo>, String> {
    // Call Python script with embedded kicad-python library
    let output = Command::new("python")
        .arg("-m")
        .arg("kicad_bridge")
        .arg("list")
        .output()?;
    
    serde_json::from_slice(&output.stdout)?
}
```

**Drawback**: Requires Python runtime in deployment
**Mitigation**: Bundle Python with PyInstaller or use PyO3 for embedded Python
