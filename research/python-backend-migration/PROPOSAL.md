# Proposal: pywebview as Primary Solution

## Recommendation
**pywebview** (Option 2) is the optimal Python backend framework for migrating the Tauri application.

---

## Justification

### 1. Minimal Footprint (Binary Size and Runtime)
- **Binary size**: 15-40 MB vs 80-250 MB for alternatives
- **RAM usage**: 50-120 MB vs 150-400 MB for Chrome-based solutions
- **Reasoning**: Native webview eliminates bundled browser engine; critical for distribution bandwidth and user storage

### 2. No External Runtime Dependencies (Chrome/Chromium)
- **Eel limitation**: Requires Chrome/Chromium installed on user system (~65% market share leaves 35% without)
- **pywebview advantage**: Uses OS-provided webview (100% coverage on supported OSes)
- **Reasoning**: Eliminates deployment risk and support burden for missing dependencies

### 3. Native Platform Integration
- **macOS**: Cocoa/WebKit (same engine as Safari)
- **Windows**: EdgeHTML or WebView2 (Microsoft-maintained, bundled with Win 11)
- **Linux**: WebKit2GTK (standard desktop component)
- **Reasoning**: OS-native rendering provides consistent behavior with system updates; better integration with platform conventions

### 4. Acceptable Code Complexity
- **LOC estimate**: 200-300 lines (vs 150-250 for Eel, 350-700 for alternatives)
- **API surface**: Simple JS bridge via `window.pywebview.api`
- **Reasoning**: Moderate complexity justified by significant footprint and dependency advantages; learning curve acceptable for single migration

### 5. Straightforward Packaging
- **Single tool**: PyInstaller (mature, well-documented)
- **Platform builds**: Standard per-OS compilation (same as Tauri)
- **Special handling**: 
  - Windows: WebView2 bootstrapper or documented prerequisite
  - Linux: GTK3/WebKit2GTK as system dependency (standard practice)
- **Reasoning**: Packaging complexity equivalent to current Tauri workflow; no dual-runtime orchestration needed

### 6. Security Alignment
- **Sandboxed webview**: OS-level security policies enforced
- **API key storage**: Python `keyring` library leverages OS credential stores (Keychain, Credential Manager, Secret Service)
- **HTTPS**: Python `requests` library enforces certificate validation
- **Reasoning**: Security posture equivalent to Tauri; OS keyring superior to file-based storage

---

## Trade-off Analysis vs Alternatives

### vs Eel (Option 1)
| Criterion | pywebview | Eel |
|-----------|-----------|-----|
| Binary size | **15-40 MB** | 80-150 MB |
| Chrome dependency | **None** | Required |
| Startup time | **0.5-1.5s** | 2-4s |
| RAM usage | **50-120 MB** | 150-300 MB |

**Decision**: 2-3x smaller footprint and eliminated dependency outweigh Eel's simpler API (50 LOC difference negligible).

### vs Flask/FastAPI + pywebview (Option 3)
| Criterion | pywebview | Flask/FastAPI + pywebview |
|-----------|-----------|---------------------------|
| Code complexity | **200-300 LOC** | 350-500 LOC |
| Dependencies | **1 package** | 6-12 packages |
| API latency | **Direct JS bridge** | HTTP overhead (1-5ms) |
| Binary size | **15-40 MB** | 25-55 MB |

**Decision**: HTTP API abstraction adds complexity without value for single-user desktop app; RESTful pattern unnecessary when UI and backend ship together.

### vs Electron + Python (Option 4)
| Criterion | pywebview | Electron + Python |
|-----------|-----------|-------------------|
| Binary size | **15-40 MB** | 150-250 MB |
| RAM usage | **50-120 MB** | 200-400 MB |
| Code complexity | **200-300 LOC** | 500-700 LOC |
| Maintenance | **Low (1 dep)** | High (20+ deps) |

**Decision**: Electron contradicts migration goal (moving away from Electron-like Tauri); 10x larger footprint unjustifiable for equivalent functionality.

---

## Risk Mitigation

### Risk 1: WebView2 Not Installed (Windows 10)
- **Likelihood**: Medium (~30-40% of Windows 10 systems)
- **Mitigation**: 
  1. Detect WebView2 at startup
  2. Display download link (100 MB, Microsoft-hosted)
  3. Alternative: Bundle WebView2 bootstrapper in installer
- **Fallback**: Document WebView2 as system requirement (standard practice)

### Risk 2: Vue.js Browser API Compatibility
- **Likelihood**: Low (Vue 3 targets ES2015+, supported by WebKit 605+)
- **Mitigation**:
  1. Test Vite build in all three platform webviews
  2. Adjust Vite `build.target` if necessary (e.g., `es2015`)
  3. Enable Vite legacy plugin if needed
- **Validation**: Required in proof-of-concept phase

### Risk 3: Limited Developer Tools
- **Impact**: Medium (debugging harder than Chrome DevTools)
- **Mitigation**:
  1. Enable pywebview debug mode during development (`debug=True`)
  2. Use browser dev tools on local HTTP server for UI debugging
  3. Use Python logging/debugger for backend logic
- **Acceptance**: Trade-off for production benefits (size, dependencies)

---

## Implementation Approach

### Phase 1: Core Migration (Week 1)
1. Install pywebview: `pip install pywebview`
2. Create Python backend with API class exposing Tauri command equivalents
3. Update Vue.js to call `window.pywebview.api.*` instead of `@tauri-apps/api`
4. Implement API key storage using `keyring` library
5. Port Gemini API calls using `requests` library

### Phase 2: File System Integration (Week 1)
1. Port KiCAD file detection logic from Rust to Python
2. Implement directory listing with `pathlib` or `os` module
3. Test cross-platform path handling

### Phase 3: Packaging (Week 2)
1. Configure PyInstaller spec file
2. Build on Windows, macOS, Linux
3. Test WebView2 detection/installation on Windows 10
4. Validate GTK3 dependency documentation for Linux
5. Create installation guides per platform

### Phase 4: Testing (Week 2)
1. Functional testing of all API endpoints
2. Cross-platform UI rendering validation
3. Performance benchmarking (startup time, memory usage)
4. Security audit (API key storage, HTTPS validation)

---

## Success Metrics

| Metric | Target | Measurement |
|--------|--------|-------------|
| Binary size | <50 MB | PyInstaller output file size |
| Startup time | <2 seconds | Time from launch to UI interactive |
| RAM usage (idle) | <150 MB | Task Manager/Activity Monitor |
| Code delta | <400 LOC | Git diff stats |
| Platform coverage | 100% (Win/Mac/Linux) | Manual testing matrix |

---

## Alternative Fallback

If pywebview proves incompatible with Vue.js build (browser API issues):
1. **Fallback to Eel**: Sacrifice binary size (80-150 MB) for broader browser compatibility
2. **Reasoning**: Chrome engine guarantees Vue.js compatibility; documented Chrome dependency acceptable if technical blocker encountered
3. **Trigger**: Inability to resolve WebKit API compatibility within 1 week of testing

---

## References
- [2] pywebview Documentation
- [5] PyInstaller Documentation
- [8] pywebview vs Eel Discussion
- [9] Python GUI Framework Benchmarks
- [11] pywebview Packaging Guide
- [12] keyring Python Library
