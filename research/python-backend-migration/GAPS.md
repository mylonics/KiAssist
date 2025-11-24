# Knowledge Gaps and Hypotheses

## Unconfirmed Technical Claims

### 1. HTTP Request Latency (Flask/FastAPI + pywebview)
**Gap**: Actual latency overhead of HTTP requests vs direct JS bridge  
**[HYPOTHESIS]**: 1-5ms per API call based on localhost loopback performance  
**Impact**: Medium - affects UI responsiveness for frequent API calls  
**Resolution**: Requires benchmark of `fetch('http://localhost:5000/api')` vs `window.pywebview.api.call()`

### 2. Electron + Python Bundle Size
**Gap**: Precise binary size for Electron + PyInstaller combination  
**[HYPOTHESIS]**: 150-250 MB based on Electron (~150 MB) + PyInstaller (~30-100 MB)  
**Impact**: Low - confirms Electron as largest option  
**Resolution**: Build proof-of-concept and measure actual output

### 3. WebView2 Adoption Rate (Windows 10)
**Gap**: Percentage of Windows 10 systems with WebView2 pre-installed  
**Current data**: Bundled with Windows 11; manual install required for Windows 10  
**[HYPOTHESIS]**: ~60-70% of Windows 10 users have WebView2 (via Edge updates)  
**Impact**: Medium - affects deployment friction  
**Resolution**: Microsoft telemetry data not publicly available; conservative assumption is manual install required

### 4. GTK3/WebKit2GTK Linux Distribution Coverage
**Gap**: Availability of WebKit2GTK in default package repositories  
**Current data**: Available in Ubuntu/Debian/Fedora/Arch official repos  
**[HYPOTHESIS]**: 95%+ of desktop Linux distributions include WebKit2GTK in repos  
**Impact**: Low - documented dependency, users can install  
**Resolution**: Survey of top 10 Linux distributions confirmed in references

### 5. pywebview Browser API Compatibility
**Gap**: Specific JavaScript/CSS features unsupported by native webviews  
**Known issues**: WebKit (macOS/Linux) may not support latest ES2023 features  
**[HYPOTHESIS]**: Vue.js 3.5.13 build targets ES2015+, compatible with WebKit 605.1.15+ (macOS 10.14+)  
**Impact**: Medium - may require Vue.js build config changes  
**Resolution**: Requires testing built Vue app in pywebview on all platforms

### 6. Eel Chrome Detection Reliability
**Gap**: Failure rate of Chrome/Chromium detection across platforms  
**[HYPOTHESIS]**: <5% failure rate on user systems (Chrome market share ~65%)  
**Impact**: Medium - requires fallback browser handling  
**Resolution**: Eel documentation describes fallback to Edge (Windows), default browser (Linux/macOS)

### 7. pywebview Startup Time Variance
**Gap**: Actual startup time distribution across hardware configurations  
**[HYPOTHESIS]**: 0.5-1.5 seconds on SSD with modern CPU; 2-3 seconds on HDD  
**Impact**: Low - acceptable for desktop application  
**Resolution**: Requires benchmarking on minimum spec hardware

### 8. API Key Storage Security
**Gap**: Best practice for persistent storage of API keys in Python desktop app  
**Options**: 
  - OS keyring (keyring library) - most secure [12]
  - Encrypted file (cryptography library + user-derived key)
  - Plain JSON (current Tauri plugin-store approach)  
**[HYPOTHESIS]**: OS keyring provides strongest security without user password prompts  
**Impact**: High - security requirement  
**Resolution**: Implement keyring with fallback to encrypted file storage

### 9. Gemini API Python Client
**Gap**: Official Google Gemini API client availability  
**Current**: `google-generativeai` Python package exists (official)  
**Impact**: Low - confirmed available  
**Resolution**: Verified at https://pypi.org/project/google-generativeai/

### 10. PyInstaller macOS Notarization
**Gap**: Complexity of notarizing PyInstaller-built macOS apps  
**Known**: Requires Apple Developer account ($99/year)  
**[HYPOTHESIS]**: Notarization adds 2-3 build steps but is automated via `pyinstaller` hooks  
**Impact**: Medium - required for macOS distribution without security warnings  
**Resolution**: Document in deployment guide; same requirement as current Tauri approach

---

## Assumptions Requiring Validation

1. **Vue.js build compatibility**: Vite-built Vue.js app serves correctly from `file://` protocol (pywebview) and `http://localhost` (Flask/FastAPI)
2. **KiCAD file access**: Python has equivalent file system permissions as Rust/Tauri for accessing KiCAD project directories
3. **Cross-platform testing**: Build pipeline requires macOS, Windows, Linux build agents (same as current Tauri)
4. **User system requirements**: Minimum Python version (3.8+) is acceptable for target user base
5. **Migration effort**: Estimated LOC counts accurate within ±30%

---

## Missing Benchmarks

| Metric | Status | Impact |
|--------|--------|--------|
| Actual API call latency (all options) | Not measured | Medium |
| Memory usage under load (multiple API calls) | Not measured | Low |
| Binary size variance across platforms | Not measured | Low |
| First-time startup vs warm startup | Not measured | Low |
| WebView2 installer size (Windows 10) | Known: ~100 MB | Medium |

---

## Recommended Research Actions (Out of Scope)

1. Build minimal proof-of-concept with pywebview + Vue.js to validate startup time and API bridge
2. Benchmark HTTP latency (Flask) vs JS bridge (pywebview) with 100 sequential API calls
3. Test Vue.js build in macOS WebKit, Windows WebView2, Linux WebKit2GTK
4. Validate keyring library compatibility on all three platforms
5. Measure PyInstaller output size for pywebview + requests + keyring on each OS

---

## Security Considerations

**Gap**: HTTPS requirement for Gemini API calls from desktop app  
**Current**: No explicit HTTPS validation mentioned in Gemini API docs  
**[HYPOTHESIS]**: Gemini API enforces HTTPS; Python `requests` library validates certificates by default  
**Impact**: Low - standard practice  
**Resolution**: Confirmed by `requests` library default behavior (verify=True)
