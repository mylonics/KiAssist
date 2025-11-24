# Technical Analysis: Python Desktop Backend Options

## Option 1: Eel

### Architecture
- **Rendering**: Chrome browser in app mode (`--app` flag)
- **Communication**: WebSocket-based RPC between Python and JavaScript
- **Distribution**: PyInstaller/Nuitka bundles Python + Chrome locator

### Pros
| Aspect | Detail |
|--------|--------|
| Simplicity | Minimal boilerplate; decorator-based API exposure (`@eel.expose`) |
| Development speed | Hot reload during development; familiar web dev workflow |
| API surface | Bidirectional JS ↔ Python calls with automatic serialization |
| Compatibility | Works with any web framework (Vue, React, vanilla JS) |

### Cons
| Aspect | Detail |
|--------|--------|
| Chrome dependency | Requires Chrome/Chromium installed on target system [1] |
| Binary size | 80-150 MB (Python runtime + dependencies, excludes Chrome) [8] |
| Startup time | 2-4 seconds (Chrome process initialization) [8] |
| Resource usage | 150-300 MB RAM baseline (Chrome rendering engine) [9] |
| Platform consistency | UI appearance varies by installed Chrome version |

### Packaging Complexity
- **Tools**: PyInstaller or Nuitka [5][6]
- **Cross-compilation**: Must build on each target OS [10]
- **Dependencies**: Requires Chrome detection logic; fallback to Edge (Windows) or default browser
- **Estimated binary size**: 80-150 MB (without Chrome)

### Code Complexity
```python
# Estimated LOC for migration: 150-250 lines
import eel
eel.init('dist')  # Vue.js build output
@eel.expose
def get_api_key(): ...
eel.start('index.html', size=(1024, 768))
```

---

## Option 2: pywebview

### Architecture
- **Rendering**: Native OS webview (Cocoa/WebKit macOS, EdgeHTML/WebView2 Windows, WebKit2GTK Linux)
- **Communication**: JavaScript bridge via `window.pywebview.api` object
- **Distribution**: PyInstaller/Nuitka bundles Python + minimal native dependencies

### Pros
| Aspect | Detail |
|--------|--------|
| Native integration | Uses OS-provided webview; no external browser dependency [2] |
| Binary size | 15-40 MB (Python runtime + minimal platform libs) [11] |
| Startup time | 0.5-1.5 seconds (native webview initialization) [8] |
| Resource usage | 50-120 MB RAM (native rendering) [9] |
| Platform consistency | Native look and feel per OS |
| Security | Sandboxed webview with OS security policies |

### Cons
| Aspect | Detail |
|--------|--------|
| WebView2 requirement | Windows requires WebView2 runtime (auto-installed Win 11, manual Win 10) [2] |
| GTK dependency | Linux requires GTK3 and WebKit2GTK packages [2] |
| API limitations | Asynchronous JS bridge; requires promise-based communication |
| Debugging | Limited dev tools (platform-dependent) |
| Browser compatibility | Vue.js must support older browser APIs (WebKit variations) |

### Packaging Complexity
- **Tools**: PyInstaller (recommended), cx_Freeze, or Nuitka [11]
- **Cross-compilation**: Must build on each target OS [10]
- **Windows**: Bundle WebView2 bootstrapper or require user installation
- **Linux**: Document GTK3/WebKit2GTK as system dependencies
- **Estimated binary size**: 15-40 MB

### Code Complexity
```python
# Estimated LOC for migration: 200-300 lines
import webview
class API:
    def get_api_key(self): ...
window = webview.create_window('App', 'dist/index.html', js_api=API())
webview.start()
```

---

## Option 3: Flask/FastAPI + pywebview

### Architecture
- **Rendering**: pywebview (native webview as in Option 2)
- **Communication**: RESTful HTTP API (Flask/FastAPI) running on localhost
- **Distribution**: Same as pywebview with added web framework dependency

### Pros
| Aspect | Detail |
|--------|--------|
| Separation of concerns | Backend API decoupled from UI rendering [3][4] |
| Testing | Standard HTTP API testing tools (pytest, requests) |
| Scalability | Can expose same API for web or mobile clients |
| Ecosystem | Access to Flask/FastAPI plugin ecosystems (CORS, auth, etc.) |
| Development | Can develop backend independently with API client tools |

### Cons
| Aspect | Detail |
|--------|--------|
| Complexity overhead | Requires HTTP server + webview coordination (threading/multiprocessing) |
| Binary size | 25-55 MB (adds Flask/FastAPI + dependencies to pywebview base) [5] |
| Port management | Must handle localhost port conflicts, selection logic |
| Latency | HTTP request overhead vs direct JS bridge (1-5ms per call) [HYPOTHESIS] |
| Startup coordination | Must ensure server ready before webview navigation |

### Packaging Complexity
- **Tools**: PyInstaller or Nuitka [5][6]
- **Dependencies**: Flask (5 deps) or FastAPI (10+ deps including uvicorn, pydantic)
- **Threading**: Requires careful daemon thread/process management for server
- **Estimated binary size**: 25-55 MB

### Code Complexity
```python
# Estimated LOC for migration: 350-500 lines
from flask import Flask, send_from_directory
import webview, threading
app = Flask(__name__)
@app.route('/api/key')
def get_key(): ...
threading.Thread(target=lambda: app.run(port=5000), daemon=True).start()
webview.create_window('App', 'http://localhost:5000')
webview.start()
```

---

## Option 4: Electron + Python Backend

### Architecture
- **Rendering**: Chromium (bundled with Electron)
- **Communication**: zerorpc (ZeroMQ + MessagePack) or child_process/python-shell [14][15]
- **Distribution**: electron-builder packages Electron + Python interpreter

### Pros
| Aspect | Detail |
|--------|--------|
| Chromium consistency | Identical rendering across all platforms |
| Mature ecosystem | Extensive Electron tooling and community support |
| Developer tools | Full Chrome DevTools built-in |
| Node.js integration | Access to npm ecosystem alongside Python |

### Cons
| Aspect | Detail |
|--------|--------|
| Binary size | 150-250 MB (Electron framework + Python runtime) [HYPOTHESIS] |
| Resource usage | 200-400 MB RAM (Chromium + V8 + Python) [9] |
| Dual runtime overhead | Maintaining Node.js and Python environments |
| Complexity | Two languages, two build systems, IPC serialization |
| zerorpc maintenance | Library last updated 2020; potential abandonment risk [14] |
| Architecture mismatch | Returns to Electron after migrating away from similar Tauri |

### Packaging Complexity
- **Tools**: electron-builder + PyInstaller [5]
- **Dependencies**: zerorpc requires libzmq compilation; platform-specific builds
- **Two-stage build**: Bundle Python executable, then Electron app
- **Estimated binary size**: 150-250 MB

### Code Complexity
```python
# Estimated LOC for migration: 500-700 lines
# Python side (zerorpc)
import zerorpc
class API:
    def get_api_key(self): ...
s = zerorpc.Server(API())
s.bind("tcp://127.0.0.1:4242")
s.run()

// JavaScript side (Electron)
const zerorpc = require("zerorpc");
const client = new zerorpc.Client();
client.connect("tcp://127.0.0.1:4242");
```

---

## Comparison Matrix

| Criterion | Eel | pywebview | Flask/FastAPI + pywebview | Electron + Python |
|-----------|-----|-----------|---------------------------|-------------------|
| **Binary Size** | 80-150 MB | 15-40 MB | 25-55 MB | 150-250 MB |
| **RAM Usage** | 150-300 MB | 50-120 MB | 60-140 MB | 200-400 MB |
| **Startup Time** | 2-4 sec | 0.5-1.5 sec | 1-2 sec | 3-5 sec |
| **External Deps** | Chrome/Chromium | WebView2 (Win), GTK3 (Linux) | Same as pywebview | None (bundled) |
| **Code Complexity** | Low (150-250 LOC) | Medium (200-300 LOC) | High (350-500 LOC) | Very High (500-700 LOC) |
| **Packaging Steps** | 1 tool | 1 tool | 1 tool | 2 tools |
| **Cross-platform Build** | Per-OS | Per-OS | Per-OS | Per-OS |
| **API Pattern** | JS ↔ Python RPC | JS bridge object | RESTful HTTP | zerorpc/stdio |
| **Dev Tools** | Chrome DevTools | Limited/platform-dependent | Limited/platform-dependent | Full Chrome DevTools |
| **Maintenance Burden** | Low (2 deps) | Low (1 dep) | Medium (6-12 deps) | High (20+ deps) |

---

## Benchmarks

### Build Size Comparison (Linux x64, estimated from references [5][8][11])
- **Eel + PyInstaller**: 95 MB
- **pywebview + PyInstaller**: 22 MB
- **Flask + pywebview + PyInstaller**: 38 MB
- **Electron + Python**: 180 MB

### Dependency Count (Python packages only)
- **Eel**: eel, bottle (2 packages)
- **pywebview**: pywebview (1 package)
- **Flask + pywebview**: flask, werkzeug, jinja2, click, itsdangerous, pywebview (6 packages)
- **FastAPI + pywebview**: fastapi, uvicorn, pydantic, starlette, pywebview (10+ packages)

---

## References Used
[1] Eel Documentation  
[2] pywebview Documentation  
[3] Flask Documentation  
[4] FastAPI Documentation  
[5] PyInstaller Documentation  
[6] Nuitka Documentation  
[8] pywebview vs Eel Discussion  
[9] Python GUI Framework Benchmarks  
[10] PyInstaller Cross-platform Support  
[11] pywebview Packaging Guide  
[14] zerorpc Documentation  
[15] python-shell (npm)
