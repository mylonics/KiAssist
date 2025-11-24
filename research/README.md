# Python Backend Migration Research

**Date**: 2025-11-23  
**Status**: Complete  
**Recommendation**: pywebview

---

## Quick Summary

Analyzed 4 Python frameworks for migrating Tauri (Rust + Vue.js) desktop app to Python-only backend:

| Option | Binary Size | RAM Usage | Verdict |
|--------|-------------|-----------|---------|
| **pywebview** ✅ | 15-40 MB | 50-120 MB | **RECOMMENDED** |
| Eel | 80-150 MB | 150-300 MB | Rejected (Chrome dependency) |
| Flask/FastAPI + pywebview | 25-55 MB | 60-140 MB | Rejected (unnecessary complexity) |
| Electron + Python | 150-250 MB | 200-400 MB | Rejected (contradicts migration goal) |

---

## Why pywebview?

1. **Smallest footprint**: 60% smaller than Eel, 10x smaller than Electron
2. **No external dependencies**: Uses OS-native webview (no Chrome required)
3. **Native integration**: Platform-specific rendering (WebKit/WebView2/GTK)
4. **Simple packaging**: Single PyInstaller build workflow
5. **Equivalent security**: OS keyring integration, webview sandboxing

---

## Read More

- **[REPORT.md](python-backend-migration/REPORT.md)**: Complete findings and recommendation
- **[ANALYSIS.md](python-backend-migration/ANALYSIS.md)**: Detailed comparison table with pros/cons
- **[PROPOSAL.md](python-backend-migration/PROPOSAL.md)**: Implementation roadmap and risk mitigation
- **[GAPS.md](python-backend-migration/GAPS.md)**: Unconfirmed assumptions requiring validation
- **[SCOPE.md](python-backend-migration/SCOPE.md)**: Research objectives and constraints
- **[REFERENCES.md](python-backend-migration/REFERENCES.md)**: 15 technical references
- **[CHANGELOG.md](python-backend-migration/CHANGELOG.md)**: Research activity log

---

## Next Steps

1. Build proof-of-concept with pywebview + Vue.js
2. Test on Windows, macOS, Linux
3. Validate Vue.js WebKit compatibility
4. Configure PyInstaller build
5. Proceed with 2-week implementation (see PROPOSAL.md Phase 1-4)
