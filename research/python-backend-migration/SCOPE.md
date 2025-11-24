# Research Scope: Python Backend Migration

## Objective
Migrate Tauri (Rust + Vue.js) desktop application to Python-only backend architecture while preserving Vue.js frontend.

## Current Architecture
- **Frontend**: Vue.js 3.5.13 + Vite 6.0.3 + TypeScript 5.6.2
- **Backend**: Rust (Tauri 2) with:
  - tauri-plugin-store (persistent storage)
  - reqwest 0.12 (HTTP client)
  - kicad-api-rs (KiCAD integration)
  - Async runtime: tokio

## Required Capabilities
1. **Cross-platform support**: Windows, macOS, Linux
2. **Web UI rendering**: Vue.js application in desktop window
3. **Python backend features**:
   - Persistent API key storage
   - HTTP requests to Gemini API
   - File system operations (detect/list directories)
   - Static file serving (built Vue.js dist/)
4. **Distribution**: Standalone executable packaging

## Solution Space
Evaluate four primary approaches:
1. Eel (Chrome app mode)
2. pywebview (native webview)
3. Flask/FastAPI + pywebview
4. Electron + Python backend (zerorpc/similar)

## Evaluation Criteria
- Packaging complexity (binary creation, dependencies)
- Cross-platform compatibility (Windows, macOS, Linux)
- Code complexity (LOC, learning curve)
- Runtime overhead (memory, startup time)
- Maintenance burden (dependency count, update frequency)

## Constraints
- Must maintain existing Vue.js frontend (no rewrite)
- Must produce standalone executables
- Must support same platforms as current Tauri app
- Should minimize binary size where possible
