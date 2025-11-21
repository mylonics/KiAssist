# Knowledge Gaps and Hypotheses

## Confirmed Facts

1. ✓ IPC uses NNG (Nanomsg) transport with Protocol Buffers serialization
2. ✓ Socket paths follow pattern: `/tmp/kicad/api*.sock` (Unix) or `%TEMP%\kicad\api*.sock` (Windows)
3. ✓ Windows requires `\\.\pipe\` prefix for named pipe connections
4. ✓ Multiple instances create separate socket files (e.g., `api.sock`, `api-1.sock`)
5. ✓ Authentication uses token exchange (initial empty, server provides token)

## Unverified Assumptions

### [HYPOTHESIS] Socket Naming Convention
- **Assumption**: Multiple instances increment socket names as `api.sock`, `api-1.sock`, `api-2.sock`
- **Evidence**: atopile code shows pattern `api*.sock` glob
- **Risk**: Medium - May differ across KiCAD versions
- **Verification**: Test with multiple KiCAD instances on each platform

### [HYPOTHESIS] Project Name Location
- **Assumption**: Project name available via `document.project.path` attribute
- **Evidence**: atopile code shows `Path(self.board.document.project.path)`
- **Risk**: Low - Observed in production code
- **Verification**: Query `GetOpenDocuments` and inspect document structure

### [HYPOTHESIS] Windows Named Pipe Enumeration
- **Assumption**: `os.listdir(r"\\.\pipe\\")` works on Windows for pipe discovery
- **Evidence**: atopile code uses this approach
- **Risk**: High - May require special permissions or Windows API calls
- **Verification**: Test on Windows 10/11 with non-admin user

### [HYPOTHESIS] Connection Timeout Recommendations
- **Assumption**: 5000ms timeout sufficient for discovery, 2000ms for operations
- **Evidence**: atopile uses 5000ms for ping during discovery
- **Risk**: Low - Conservative timeout values
- **Verification**: Measure actual response times on different systems

## Unknown Implementation Details

### Missing Information: Rust NNG Bindings
- **Question**: Which Rust crate provides NNG support equivalent to pynng?
- **Candidates**: `nng-rs`, `runng`, or direct FFI to libnng
- **Impact**: Critical for Rust implementation
- **Action Required**: Evaluate Rust NNG libraries for compatibility

### Missing Information: Protobuf Schema
- **Question**: Where are the `.proto` files defining API messages?
- **Location**: Likely in KiCAD source tree (not found in Python client)
- **Impact**: Medium - Needed for Rust protobuf generation
- **Action Required**: Search KiCAD source repository

### Missing Information: Board vs Project Distinction
- **Question**: Can a single KiCAD instance have multiple boards/projects open?
- **Evidence**: `get_board()` retrieves first document only
- **Impact**: Medium - Affects UI design for instance selection
- **Action Required**: Test KiCAD 9.0 multi-document behavior

### Missing Information: Socket Cleanup
- **Question**: Are stale socket files removed on KiCAD exit?
- **Risk**: Stale sockets may cause false positives in detection
- **Impact**: Medium - May need ping validation
- **Action Required**: Test socket lifecycle

## Platform-Specific Unknowns

### Windows
1. Named pipe permissions and access requirements
2. Behavior when KiCAD runs as different user
3. Interaction with antivirus software

### Linux
1. Flatpak vs. native installation differences
2. SELinux/AppArmor socket access policies
3. Multi-user socket access scenarios

### macOS
1. Sandboxing restrictions on socket access
2. Flatpak installation behavior (less common)
3. M1/M2 ARM vs. Intel differences

## API Completeness Questions

### Undocumented Endpoints
- **Question**: What other methods exist beyond documented ones?
- **Source**: Full protobuf schema needed
- **Impact**: Low - Core functionality covered

### Version Compatibility
- **Question**: IPC API changes between KiCAD 9.0.x versions?
- **Evidence**: Version checking built into kicad-python
- **Impact**: Medium - May need version-specific handling
- **Action Required**: Monitor KiCAD release notes

## Security Considerations

### [HYPOTHESIS] Local-Only Access
- **Assumption**: IPC sockets only accessible locally (no network exposure)
- **Evidence**: Unix socket and named pipe are local-only by design
- **Risk**: Low - Inherent to transport mechanism
- **Verification**: Confirm no TCP socket fallback exists

### Token Security
- **Question**: Is token validated per-request or per-session?
- **Evidence**: Code shows token in header of each request
- **Impact**: Low - Local-only communication
- **Action Required**: Document token handling in implementation

## Performance Unknowns

### Concurrent Access
- **Question**: Can multiple external clients connect to same socket?
- **Evidence**: NNG supports multiple clients on Req/Rep pattern
- **Impact**: Medium - May affect UI refresh strategy
- **Action Required**: Test concurrent access scenarios

### Message Size Limits
- **Question**: Maximum protobuf message size supported?
- **Impact**: Low - Unlikely to hit limits with metadata queries
- **Action Required**: Document if implementing board data transfer
