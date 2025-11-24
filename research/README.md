# KiAssist Research Documentation

This directory contains structured research reports for technical decisions in the KiAssist project.

---

## 1. Python Backend Migration Research

**Date**: 2025-11-23  
**Status**: Complete  
**Recommendation**: pywebview

### Quick Summary

Analyzed 4 Python frameworks for migrating Tauri (Rust + Vue.js) desktop app to Python-only backend:

| Option | Binary Size | RAM Usage | Verdict |
|--------|-------------|-----------|---------|
| **pywebview** ✅ | 15-40 MB | 50-120 MB | **RECOMMENDED** |
| Eel | 80-150 MB | 150-300 MB | Rejected (Chrome dependency) |
| Flask/FastAPI + pywebview | 25-55 MB | 60-140 MB | Rejected (unnecessary complexity) |
| Electron + Python | 150-250 MB | 200-400 MB | Rejected (contradicts migration goal) |

### Read More

- **[REPORT.md](python-backend-migration/REPORT.md)**: Complete findings and recommendation
- **[ANALYSIS.md](python-backend-migration/ANALYSIS.md)**: Detailed comparison table with pros/cons
- **[PROPOSAL.md](python-backend-migration/PROPOSAL.md)**: Implementation roadmap and risk mitigation
- **[GAPS.md](python-backend-migration/GAPS.md)**: Unconfirmed assumptions requiring validation
- **[SCOPE.md](python-backend-migration/SCOPE.md)**: Research objectives and constraints
- **[REFERENCES.md](python-backend-migration/REFERENCES.md)**: 15 technical references
- **[CHANGELOG.md](python-backend-migration/CHANGELOG.md)**: Research activity log

---

## 2. Installer Packaging Research

**Date**: 2025-11-24  
**Status**: Complete  
**Recommendation**: One-folder PyInstaller + platform-specific installers

### Quick Summary

Researched installer solutions for PyInstaller-based cross-platform distribution:

| Platform | Installer Tool | Format | Installation Path |
|----------|---------------|--------|-------------------|
| **Windows** | Inno Setup 6.3+ | `.exe` setup | `%LOCALAPPDATA%\Programs\KiAssist\` |
| **macOS** | create-dmg | `.dmg` disk image | `/Applications/KiAssist.app` |
| **Linux** | AppImage (primary) | `.AppImage` | `~/.local/bin/` |
| **Linux** | DEB (secondary) | `.deb` package | `/opt/kiassist/` |

### Key Findings

1. **Performance**: One-folder mode eliminates 2-5 second decompression overhead
2. **Data Persistence**: API keys stored via OS keyring persist automatically across updates
3. **CI/CD Integration**: All tools available on GitHub Actions runners
4. **Code Signing**: Required for macOS, recommended for Windows, optional for Linux

### Read More

- **[REPORT.md](installer-packaging/REPORT.md)**: Complete findings and recommendations
- **[ANALYSIS.md](installer-packaging/ANALYSIS.md)**: Detailed comparison of installer tools and configurations
- **[PROPOSAL.md](installer-packaging/PROPOSAL.md)**: Implementation roadmap with code examples
- **[GAPS.md](installer-packaging/GAPS.md)**: Knowledge gaps and validation strategies
- **[SCOPE.md](installer-packaging/SCOPE.md)**: Research objectives and constraints
- **[REFERENCES.md](installer-packaging/REFERENCES.md)**: 23 technical references
- **[CHANGELOG.md](installer-packaging/CHANGELOG.md)**: Research activity log

---

## Research Template

Each research topic follows the same structure:

1. **SCOPE.md** - Objective, constraints, success criteria
2. **REFERENCES.md** - Numbered technical sources
3. **ANALYSIS.md** - Comparative analysis with tables
4. **GAPS.md** - Knowledge gaps tagged with `[HYPOTHESIS]`
5. **PROPOSAL.md** - Implementation roadmap
6. **REPORT.md** - Executive summary with recommendations
7. **CHANGELOG.md** - Research activity log
