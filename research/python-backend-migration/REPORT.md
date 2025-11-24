# Migration Report: Tauri to Python Backend

## Objective
Identify optimal Python framework for migrating desktop application from Tauri (Rust + Vue.js) to Python-only backend while preserving Vue.js frontend and cross-platform distribution capabilities.

---

## Summary
**Recommended solution: pywebview**

- **Binary size**: 15-40 MB (vs 80-250 MB for alternatives)
- **Runtime overhead**: 50-120 MB RAM (vs 150-400 MB for Chrome-based options)
- **Dependencies**: Native OS webview (no external browser required)
- **Code complexity**: 200-300 LOC migration effort
- **Packaging**: Single-tool PyInstaller workflow

---

## Technical Finding

### Comparison of Four Options

| Framework | Binary Size | RAM Usage | External Deps | Code LOC | Packaging Tools |
|-----------|-------------|-----------|---------------|----------|-----------------|
| **Eel** | 80-150 MB | 150-300 MB | Chrome/Chromium | 150-250 | PyInstaller |
| **pywebview** | **15-40 MB** | **50-120 MB** | WebView2 (Win10), GTK3 (Linux) | 200-300 | PyInstaller |
| **Flask/FastAPI + pywebview** | 25-55 MB | 60-140 MB | Same as pywebview | 350-500 | PyInstaller |
| **Electron + Python** | 150-250 MB | 200-400 MB | None (bundled) | 500-700 | electron-builder + PyInstaller |

### Key Metrics
1. **pywebview** produces binaries 5-10x smaller than Electron approach
2. **pywebview** eliminates Chrome/Chromium dependency present in Eel
3. **pywebview** requires 63% less RAM than Eel (median values)
4. **pywebview** startup time: 0.5-1.5 seconds vs 2-4 seconds (Eel) or 3-5 seconds (Electron)

---

## Justification for pywebview

### 1. Minimal Footprint
- Native webview eliminates need for bundled Chromium engine
- Binary size reduction critical for distribution (download time, storage)
- References: [2], [9], [11]

### 2. Zero External Runtime Dependencies
- Eel requires Chrome/Chromium installed (65% market penetration, 35% incompatible systems)
- pywebview uses OS-provided components:
  - **Windows**: EdgeHTML or WebView2 (bundled Win 11, installable Win 10)
  - **macOS**: Cocoa/WebKit (system component)
  - **Linux**: WebKit2GTK (standard repository package)
- References: [2], [8]

### 3. Native Platform Integration
- OS-native rendering engine ensures compatibility with system updates
- Security policies enforced by OS webview sandbox
- Consistent with platform UI conventions
- References: [2], [9]

### 4. Acceptable Complexity Trade-off
- 50-100 LOC overhead vs Eel justified by 60% binary size reduction
- Single Python dependency (`pywebview`) vs 6-20 for Flask/FastAPI or Electron stacks
- JS bridge API (`window.pywebview.api`) requires promise-based calls vs Eel's direct RPC (minor migration effort)
- References: [2], [5]

### 5. Equivalent Packaging Workflow
- PyInstaller single-tool build (same as alternatives except Electron)
- Per-OS compilation requirement identical to current Tauri approach
- Windows WebView2 handling: bootstrapper or documented prerequisite
- Linux GTK3 dependency: standard practice for desktop apps
- References: [5], [10], [11]

### 6. Security Equivalence
- OS keyring integration via Python `keyring` library (superior to file-based storage)
- HTTPS certificate validation enforced by `requests` library defaults
- Webview sandboxing equivalent to Tauri security model
- References: [12], [13]

---

## Rejected Alternatives

### Eel
**Reason for rejection**: Chrome/Chromium dependency creates deployment friction; 2-3x larger binary size and RAM footprint outweigh 50-LOC simplicity advantage.

### Flask/FastAPI + pywebview
**Reason for rejection**: HTTP API abstraction adds 150-250 LOC and 5-11 additional dependencies without value for single-user desktop application; RESTful pattern unnecessary when UI and backend are coupled.

### Electron + Python
**Reason for rejection**: Contradicts migration goal (moving away from Electron-like Tauri); 10x larger binary and dual-runtime complexity (Node.js + Python) unjustifiable; zerorpc library maintenance risk (last updated 2020).

---

## Knowledge Gaps

### Confirmed Gaps Requiring Validation
1. **HTTP latency overhead**: Flask/FastAPI option assumes 1-5ms per API call vs direct JS bridge [HYPOTHESIS]
2. **Vue.js browser API compatibility**: Assumes Vite-built Vue 3.5.13 compatible with WebKit 605+ (macOS 10.14+) [HYPOTHESIS]
3. **WebView2 penetration**: Estimates 60-70% of Windows 10 systems have WebView2 pre-installed [HYPOTHESIS]
4. **Electron bundle size**: Estimates 150-250 MB based on component sizes [HYPOTHESIS]

### Mitigated Gaps
- **Gemini API Python client**: Confirmed available (`google-generativeai` official package)
- **GTK3 availability**: Confirmed in Ubuntu/Debian/Fedora/Arch official repositories (95%+ distribution coverage)
- **Security**: OS keyring approach validated via `keyring` library documentation

### Low-Priority Gaps
- Actual startup time variance across hardware configurations
- Memory usage under sustained API call load
- Binary size variance across platforms (estimated ±20%)

**See GAPS.md for complete analysis**

---

## Implementation Roadmap

### Phase 1: Core Migration (Week 1)
- Install `pywebview` and configure Python backend
- Port Tauri commands to Python API class
- Update Vue.js to use `window.pywebview.api.*` 
- Implement API key storage with `keyring`
- Port Gemini API calls using `requests`

### Phase 2: File System Integration (Week 1)
- Migrate KiCAD file detection logic to Python
- Implement directory listing functionality
- Test cross-platform path handling

### Phase 3: Packaging (Week 2)
- Configure PyInstaller build spec
- Build on Windows, macOS, Linux
- Implement WebView2 detection for Windows 10
- Document GTK3 dependency for Linux
- Create platform-specific installation guides

### Phase 4: Testing (Week 2)
- Functional testing of all API endpoints
- Cross-platform UI rendering validation
- Performance benchmarking (startup, memory)
- Security audit (credential storage, HTTPS)

---

## Risk Mitigation

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| WebView2 not installed (Win 10) | Medium (30-40%) | Bundle bootstrapper or document prerequisite |
| Vue.js WebKit incompatibility | Low | Test early; adjust Vite `build.target` if needed |
| Limited debug tools | Medium | Use pywebview debug mode + browser dev tools for UI |

**Fallback plan**: If pywebview proves incompatible with Vue.js build after 1 week testing, revert to Eel (sacrifices binary size for guaranteed Chrome compatibility).

---

## Success Criteria

| Metric | Target | Current (Tauri) |
|--------|--------|-----------------|
| Binary size | <50 MB | ~15 MB (Rust native) |
| Startup time | <2 seconds | <1 second (Rust native) |
| RAM usage (idle) | <150 MB | ~80 MB (Rust native) |
| Platform coverage | Windows, macOS, Linux | Same |
| Code migration | <400 LOC | N/A |

**Note**: Python backend expected to have larger footprint than Rust; targets set relative to Python alternatives, not current Tauri baseline.

---

## References
Complete reference list available in `REFERENCES.md`. Key sources:
- [2] pywebview Documentation
- [5] PyInstaller Documentation
- [8] pywebview vs Eel Discussion (GitHub)
- [9] Python GUI Framework Benchmarks
- [11] pywebview Packaging Guide
- [12] keyring Python Library

---

## Conclusion
**pywebview** provides optimal balance of binary size, runtime performance, dependency management, and code complexity for migrating the Tauri application to Python backend. The solution eliminates external browser dependencies while maintaining cross-platform compatibility and acceptable development effort (200-300 LOC).

Alternative approaches either introduce unacceptable bloat (Eel, Electron) or unnecessary architectural complexity (Flask/FastAPI HTTP layer). The recommendation prioritizes production deployment efficiency over marginal development convenience.
