# Analysis: Installer Packaging Solutions

## 1. Windows Installer Tools Comparison

| Tool | Format | Complexity | PyInstaller Support | CI/CD | Code Signing | License | Status |
|------|--------|------------|---------------------|-------|--------------|---------|--------|
| **Inno Setup** | .exe installer | Low | Native | Yes (CLI) | Yes | Free (BSD-like) | Active, v6.3+ |
| **NSIS** | .exe installer | Medium | Native | Yes (CLI) | Yes | Free (zlib/libpng) | Active, v3.10+ |
| **WiX Toolset** | .msi | High | Native | Yes (CLI) | Yes | Free (MS-RL) | Active, v4.0+ |
| **PyInstaller + ZIP** | .zip | Very Low | Native | Yes | No | N/A | N/A |

**Metrics**:
- Inno Setup: ~2 MB installer overhead, 1-5 second install time
- NSIS: ~500 KB installer overhead, 1-3 second install time  
- WiX: ~3 MB installer overhead, 3-10 second install time
- ZIP: 0 KB overhead, manual extraction

**Verdict**: **Inno Setup** recommended [Ref 4]
- Best balance of simplicity and features
- Excellent PyInstaller integration examples [Ref 16]
- Free, no licensing restrictions
- Script-based configuration (`.iss` files)
- Built-in uninstaller generation
- Supports both per-user and system-wide installation
- Windows Registry integration
- Start Menu shortcuts creation

**Alternative**: NSIS for smaller installer size, WiX for enterprise MSI requirements.

---

## 2. macOS Packaging Solutions Comparison

| Tool | Format | Complexity | Distribution | Notarization | License | Status |
|------|--------|------------|--------------|--------------|---------|--------|
| **DMG (create-dmg)** | .dmg disk image | Low | Download | Yes | Free (MIT) | Active |
| **PKG (pkgbuild)** | .pkg installer | Medium | Download/Store | Yes | Built-in | Active |
| **App Bundle Only** | .app | Very Low | Download | Yes | N/A | N/A |
| **Homebrew Cask** | Formula | Low | Homebrew | N/A | Free (BSD) | Active |

**Metrics**:
- DMG: ~2 MB overhead, drag-and-drop install (0 seconds)
- PKG: ~1 MB overhead, 5-15 second install time
- App Bundle: 0 KB overhead, manual copy
- Homebrew: Distributed via git repository

**Verdict**: **DMG with create-dmg** recommended [Ref 9]
- Standard distribution method for macOS applications
- User-friendly drag-and-drop installation to `/Applications`
- No installer UI complexity
- PyInstaller app bundles work directly
- Supports notarization for Gatekeeper [Ref 8]
- Automated creation via create-dmg tool
- Background image customization for branding

**Alternative**: PKG for pre/post-install scripts, Homebrew Cask for developer distribution.

---

## 3. Linux Packaging Solutions Comparison

| Tool | Format | Distributions | Complexity | Repository | License | Status |
|------|--------|---------------|------------|------------|---------|--------|
| **AppImage** | .AppImage | All | Low | No | Free (MIT) | Active |
| **DEB (dpkg-deb)** | .deb | Debian/Ubuntu | Medium | Yes | Free (GPL) | Active |
| **RPM (rpmbuild)** | .rpm | Fedora/RHEL | Medium | Yes | Free (GPL) | Active |
| **Flatpak** | .flatpak | All | High | Yes | Free (LGPL) | Active |
| **Tarball** | .tar.gz | All | Very Low | No | N/A | N/A |

**Metrics**:
- AppImage: ~5 MB overhead, 0 seconds (run directly), works on 95%+ distros
- DEB: ~500 KB overhead, 1-3 second install, Debian/Ubuntu only (~60% desktop Linux)
- RPM: ~500 KB overhead, 1-3 second install, Fedora/RHEL only (~15% desktop Linux)
- Flatpak: ~10 MB overhead, 10-30 second install, sandboxed
- Tarball: ~200 KB overhead, manual extraction

**Verdict**: **AppImage** as primary, **DEB** as secondary [Ref 13, 11]
- AppImage provides universal compatibility without dependencies
- Single file, no installation required (can be made executable)
- No root access needed
- DEB for users preferring system integration (menu entries, updates via apt)
- RPM optional for Red Hat ecosystem

**Trade-offs**:
- AppImage: No automatic updates, no system integration by default
- DEB/RPM: Distribution-specific, requires package maintenance

---

## 4. PyInstaller Configuration: One-Folder vs One-File

| Mode | Startup Time | Disk Usage | Update Size | Code Complexity |
|------|--------------|------------|-------------|-----------------|
| **One-Folder** | 0.1-0.5s | 80-150 MB | 80-150 MB | Low |
| **One-File** | 2-5s | 40-60 MB (+ temp) | 40-60 MB | Low |

**Decompression overhead** (One-File mode):
- Windows: 2-4 seconds (to `%TEMP%`)
- macOS: 1-2 seconds (to `/var/folders/`)
- Linux: 1-3 seconds (to `/tmp`)

**Verdict**: **One-Folder mode** recommended [Ref 2]
- Eliminates decompression step entirely
- Startup time improvement: 2-5 seconds → 0.1-0.5 seconds
- Enables incremental updates (update only changed files)
- Installers manage directory structure
- Disk usage increase: ~2x, but acceptable for desktop application

**Configuration change**:
```python
# Current (One-File)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,  # ← Bundled into executable
    a.datas,     # ← Bundled into executable
    # ...
)

# Proposed (One-Folder)
exe = EXE(
    pyz,
    a.scripts,
    # Remove a.binaries and a.datas from EXE
    # ...
)

coll = COLLECT(
    exe,
    a.binaries,  # ← Separate files
    a.datas,     # ← Separate files
    # ...
)
```

---

## 5. Standard Installation Directories

### Windows [Ref 7]

| Scope | Path | Purpose | Permissions |
|-------|------|---------|-------------|
| Per-User | `%LOCALAPPDATA%\Programs\KiAssist\` | Recommended | User-writable |
| System-wide | `C:\Program Files\KiAssist\` | Traditional | Admin required |
| Portable | User-defined | Optional | User-writable |

**Recommendation**: Per-user installation to `%LOCALAPPDATA%\Programs\KiAssist\` (e.g., `C:\Users\<username>\AppData\Local\Programs\KiAssist\`)
- No admin rights required
- Standard for modern Windows applications
- Isolated per user account

**Data storage**:
- Executable: `%LOCALAPPDATA%\Programs\KiAssist\KiAssist.exe`
- API keys: Windows Credential Manager (separate, persists automatically)

### macOS [Ref 10]

| Scope | Path | Purpose | Permissions |
|-------|------|---------|-------------|
| Per-User | `~/Applications/KiAssist.app` | Optional | User-writable |
| System-wide | `/Applications/KiAssist.app` | Standard | Admin required |

**Recommendation**: System-wide installation to `/Applications/KiAssist.app`
- Standard location for macOS applications
- Visible in Launchpad and Spotlight
- App bundle structure (self-contained)

**Data storage**:
- Executable: `/Applications/KiAssist.app/Contents/MacOS/KiAssist`
- API keys: macOS Keychain (separate, persists automatically)

### Linux [Ref 15, 14]

| Scope | Path | Purpose | Permissions |
|-------|------|---------|-------------|
| Per-User | `~/.local/bin/kiassist/` | AppImage | User-writable |
| System-wide (DEB/RPM) | `/opt/kiassist/` | Third-party apps | Admin required |
| System-wide (distro) | `/usr/bin/kiassist` | Distro packages | Admin required |

**Recommendation**: 
- **AppImage**: `~/Applications/KiAssist.AppImage` or `~/.local/bin/KiAssist.AppImage`
- **DEB/RPM**: `/opt/kiassist/` with symlink to `/usr/local/bin/kiassist`

**Desktop integration**:
- `.desktop` file in `~/.local/share/applications/` (per-user) or `/usr/share/applications/` (system-wide)

**Data storage**:
- API keys: Secret Service API via keyring (separate, persists automatically)

---

## 6. Update Strategy and Data Persistence

### API Key Persistence Analysis

| Platform | Storage Mechanism | Installer Impact | Persistence Across Updates |
|----------|-------------------|------------------|---------------------------|
| Windows | Credential Manager | None | **YES** - Separate credential store |
| macOS | Keychain | None | **YES** - Separate keychain database |
| Linux | Secret Service | None | **YES** - Separate secret store |

**Finding**: API keys stored via OS keyring **automatically persist** across application updates [Ref 18, 19, 20, 21].

**Rationale**:
1. Keyring stores credentials in OS-managed databases separate from application files
2. KiAssist uses service name `"KiAssist"` and key name `"gemini_api_key"` as identifiers
3. Reinstalling/updating application does not modify keyring databases
4. No special installer logic required for data preservation

**Verification** (from `api_key.py`):
```python
SERVICE_NAME = "KiAssist"  # Identifier in OS keyring
KEY_NAME = "gemini_api_key"  # Key identifier

# Stored via OS APIs:
keyring.set_password(SERVICE_NAME, KEY_NAME, api_key)
keyring.get_password(SERVICE_NAME, KEY_NAME)
```

### Update Best Practices

1. **Clean uninstall option**: Remove application files, preserve keyring data
2. **Upgrade installation**: Overwrite application directory, preserve keyring data automatically
3. **Version metadata**: Store version in application metadata for upgrade detection
4. **Backup recommendation**: Document that API keys persist (user guide)

---

## 7. CI/CD Integration Patterns

### Current Build Workflow (from `.github/workflows/build.yml`)

```yaml
jobs:
  build:
    matrix:
      platform: [ubuntu-latest, macos-latest, windows-latest]
    steps:
      - Build frontend (npm run build)
      - Build PyInstaller (pyinstaller kiassist.spec --clean)
      - Upload artifacts
```

### Proposed Installer Integration

| Platform | Build Step | Installer Tool | Output Artifact |
|----------|------------|----------------|-----------------|
| Windows | PyInstaller → Inno Setup | `iscc installer.iss` | `KiAssist-Setup-{version}.exe` |
| macOS | PyInstaller → create-dmg | `create-dmg KiAssist.app` | `KiAssist-{version}.dmg` |
| Linux (AppImage) | PyInstaller → appimagetool | `appimagetool KiAssist/` | `KiAssist-{version}.AppImage` |
| Linux (DEB) | PyInstaller → dpkg-deb | `dpkg-deb --build` | `kiassist_{version}_amd64.deb` |

**Implementation**:
1. Add installer creation step after PyInstaller build
2. Use platform-specific conditionals in GitHub Actions
3. Upload installer artifacts instead of raw executables
4. Tag releases with version number for download

**Tool availability**:
- Inno Setup: Available via Chocolatey on Windows runners [Ref 4]
- create-dmg: Available via Homebrew on macOS runners [Ref 9]
- appimagetool: Available as AppImage on Linux runners [Ref 13]
- dpkg-deb: Built-in on Ubuntu runners [Ref 11]

---

## 8. Code Signing Requirements

| Platform | Requirement | Tool | Cost | Impact if Missing |
|----------|-------------|------|------|-------------------|
| Windows | Optional (recommended) | SignTool.exe | $100-500/year (cert) | SmartScreen warning |
| macOS | Required (notarization) | codesign, notarytool | $99/year (dev account) | Gatekeeper blocks |
| Linux | Optional | gpg | Free | None (no standard) |

**Recommendations**:
1. **macOS**: Mandatory for distribution outside TestFlight - requires Apple Developer account [Ref 8]
2. **Windows**: Highly recommended to avoid SmartScreen warnings - use EV or OV certificate
3. **Linux**: Optional GPG signature for package verification

**Initial release**: Distribute unsigned (Windows/Linux) or self-signed (macOS dev builds) with user instructions
**Production release**: Obtain certificates for Windows and macOS

---

## Summary of Recommendations

| Platform | Installer Tool | Distribution Format | Installation Path | Code Signing |
|----------|----------------|---------------------|-------------------|--------------|
| **Windows** | Inno Setup 6.3+ | `.exe` setup | `%LOCALAPPDATA%\Programs\KiAssist\` | Recommended |
| **macOS** | create-dmg | `.dmg` disk image | `/Applications/KiAssist.app` | Required |
| **Linux** | AppImage (primary), DEB (secondary) | `.AppImage`, `.deb` | `~/.local/bin/` or `/opt/kiassist/` | Optional |

**PyInstaller mode**: One-Folder (directory-based distribution)

**Update strategy**: Overwrite installation directory; keyring data persists automatically
