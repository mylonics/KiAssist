# Proposal: Installer Implementation for KiAssist

## Objective

Implement directory-based installers for KiAssist to eliminate runtime decompression overhead and provide standard installation experience on Windows, macOS, and Linux.

## Recommended Architecture

### Phase 1: PyInstaller Configuration Migration (Priority: High)

**Goal**: Convert from one-file to one-folder distribution mode.

**Implementation**:

1. **Modify `kiassist.spec`** (lines 49-69):

```python
# BEFORE (one-file mode)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,     # Remove this
    a.datas,        # Remove this
    [],
    name="KiAssist",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)

# AFTER (one-folder mode)
exe = EXE(
    pyz,
    a.scripts,
    [],              # Keep empty
    exclude_binaries=True,  # Add this flag
    name="KiAssist",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)

# Add COLLECT step for one-folder bundle
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='KiAssist',
)
```

2. **Update macOS BUNDLE configuration** (lines 72-83):

```python
if sys.platform == "darwin":
    app = BUNDLE(
        coll,           # Use coll instead of exe
        name="KiAssist.app",
        icon=None,
        bundle_identifier="com.kiassist.app",
        info_plist={
            "NSPrincipalClass": "NSApplication",
            "NSHighResolutionCapable": "True",
            "CFBundleVersion": "1.0.0",  # Add version
            "CFBundleShortVersionString": "1.0.0",  # Add version
        },
    )
```

**Expected Output**:
- Windows: `dist/KiAssist/KiAssist.exe` + supporting DLLs/files
- Linux: `dist/KiAssist/KiAssist` + supporting .so files
- macOS: `dist/KiAssist.app/` (app bundle with Contents/ subdirectory)

**Validation**:
```bash
# Test build on each platform
pyinstaller kiassist.spec --clean

# Verify startup time improvement
# Expected: 2-5 second reduction in launch time
```

---

### Phase 2: Windows Installer with Inno Setup (Priority: High)

**Goal**: Create `.exe` installer for Windows distribution.

**Implementation**:

1. **Install Inno Setup** (CI/CD):
```yaml
# .github/workflows/build.yml (Windows job)
- name: Install Inno Setup
  run: choco install innosetup -y
```

2. **Create `installer.iss`** (project root):

```ini
; Inno Setup Script for KiAssist

#define MyAppName "KiAssist"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "KiAssist Contributors"
#define MyAppURL "https://github.com/mylonics/NewKiAssist"
#define MyAppExeName "KiAssist.exe"

[Setup]
AppId={{E8F3C7A1-9B2D-4E5F-8C3A-1D2E3F4A5B6C}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
DefaultDirName={localappdata}\Programs\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
LicenseFile=LICENSE
OutputDir=installer-output
OutputBaseFilename=KiAssist-Setup-{#MyAppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "dist\KiAssist\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{app}"
```

3. **Build installer** (CI/CD):
```yaml
- name: Create Windows Installer
  if: matrix.platform == 'windows-latest'
  run: |
    "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installer.iss
  
- name: Upload Windows Installer
  if: matrix.platform == 'windows-latest'
  uses: actions/upload-artifact@v4
  with:
    name: windows-installer
    path: installer-output/KiAssist-Setup-*.exe
```

**Expected Output**: `KiAssist-Setup-1.0.0.exe` (~80-150 MB)

**Installation Path**: `C:\Users\<username>\AppData\Local\Programs\KiAssist\`

**Features**:
- No admin rights required (per-user installation)
- Start Menu shortcuts
- Optional desktop icon
- Uninstaller with entry in Control Panel
- Preserves API keys in Windows Credential Manager

---

### Phase 3: macOS DMG with create-dmg (Priority: High)

**Goal**: Create `.dmg` disk image for macOS distribution.

**Implementation**:

1. **Install create-dmg** (CI/CD):
```yaml
# .github/workflows/build.yml (macOS job)
- name: Install create-dmg
  run: brew install create-dmg
```

2. **Create DMG** (CI/CD):
```yaml
- name: Create macOS DMG
  if: matrix.platform == 'macos-latest'
  run: |
    create-dmg \
      --volname "KiAssist" \
      --window-pos 200 120 \
      --window-size 600 400 \
      --icon-size 100 \
      --icon "KiAssist.app" 175 190 \
      --hide-extension "KiAssist.app" \
      --app-drop-link 425 190 \
      "KiAssist-1.0.0.dmg" \
      "dist/KiAssist.app"

- name: Upload macOS DMG
  if: matrix.platform == 'macos-latest'
  uses: actions/upload-artifact@v4
  with:
    name: macos-installer
    path: KiAssist-*.dmg
```

**Expected Output**: `KiAssist-1.0.0.dmg` (~80-150 MB)

**Installation**: User drags `KiAssist.app` to `/Applications` folder

**Features**:
- Standard macOS installation UX
- Drag-and-drop to Applications
- Background image with instructions (optional)
- Preserves API keys in macOS Keychain

**Code Signing** (future):
```yaml
- name: Sign and Notarize
  if: matrix.platform == 'macos-latest'
  env:
    APPLE_ID: ${{ secrets.APPLE_ID }}
    APPLE_PASSWORD: ${{ secrets.APPLE_APP_PASSWORD }}
    TEAM_ID: ${{ secrets.APPLE_TEAM_ID }}
  run: |
    # Sign app bundle
    codesign --force --deep --sign "Developer ID Application: <TeamID>" dist/KiAssist.app
    
    # Create DMG
    create-dmg ...
    
    # Notarize
    xcrun notarytool submit KiAssist-1.0.0.dmg \
      --apple-id "$APPLE_ID" \
      --password "$APPLE_PASSWORD" \
      --team-id "$TEAM_ID" \
      --wait
    
    # Staple notarization
    xcrun stapler staple KiAssist-1.0.0.dmg
```

---

### Phase 4: Linux AppImage (Priority: Medium)

**Goal**: Create self-contained `.AppImage` for universal Linux compatibility.

**Implementation**:

1. **Create AppDir structure**:
```bash
KiAssist.AppDir/
├── AppRun (symlink to usr/bin/kiassist)
├── kiassist.desktop
├── kiassist.png (256x256 icon)
└── usr/
    └── bin/
        └── kiassist/ (PyInstaller output)
```

2. **Create `.desktop` file** (`kiassist.desktop`):
```ini
[Desktop Entry]
Type=Application
Name=KiAssist
Comment=KiCAD AI Assistant
Exec=kiassist
Icon=kiassist
Categories=Development;Electronics;
Terminal=false
```

3. **Build AppImage** (CI/CD):
```yaml
- name: Create Linux AppImage
  if: matrix.platform == 'ubuntu-latest'
  run: |
    # Download appimagetool
    wget https://github.com/AppImage/AppImageKit/releases/download/continuous/appimagetool-x86_64.AppImage
    chmod +x appimagetool-x86_64.AppImage
    
    # Create AppDir structure
    mkdir -p KiAssist.AppDir/usr/bin
    cp -r dist/KiAssist KiAssist.AppDir/usr/bin/kiassist
    
    # Create desktop file and icon
    cp kiassist.desktop KiAssist.AppDir/
    cp kiassist.png KiAssist.AppDir/
    
    # Create AppRun symlink
    ln -s usr/bin/kiassist/KiAssist KiAssist.AppDir/AppRun
    
    # Build AppImage
    ./appimagetool-x86_64.AppImage KiAssist.AppDir KiAssist-1.0.0-x86_64.AppImage

- name: Upload Linux AppImage
  if: matrix.platform == 'ubuntu-latest'
  uses: actions/upload-artifact@v4
  with:
    name: linux-appimage
    path: KiAssist-*-x86_64.AppImage
```

**Expected Output**: `KiAssist-1.0.0-x86_64.AppImage` (~80-150 MB)

**Usage**: 
```bash
chmod +x KiAssist-1.0.0-x86_64.AppImage
./KiAssist-1.0.0-x86_64.AppImage
```

**Features**:
- No installation required
- Runs on most Linux distributions
- Self-contained (except system libraries)
- Preserves API keys via Secret Service API

---

### Phase 5: Linux DEB Package (Priority: Low)

**Goal**: Provide system-integrated package for Debian/Ubuntu users.

**Implementation**:

1. **Create DEB control structure**:
```
kiassist_1.0.0_amd64/
├── DEBIAN/
│   ├── control
│   └── postinst (optional)
└── opt/
    └── kiassist/
        └── (PyInstaller output)
```

2. **Create `DEBIAN/control`**:
```
Package: kiassist
Version: 1.0.0
Section: electronics
Priority: optional
Architecture: amd64
Depends: libwebkit2gtk-4.1-0, libgirepository-1.0-1, python3-gi
Maintainer: KiAssist Contributors <kiassist@example.com>
Description: KiCAD AI Assistant
 Desktop application for AI-assisted KiCAD PCB design.
```

3. **Build DEB** (CI/CD):
```yaml
- name: Create DEB package
  if: matrix.platform == 'ubuntu-latest'
  run: |
    # Create directory structure
    mkdir -p kiassist_1.0.0_amd64/opt/kiassist
    mkdir -p kiassist_1.0.0_amd64/DEBIAN
    mkdir -p kiassist_1.0.0_amd64/usr/share/applications
    mkdir -p kiassist_1.0.0_amd64/usr/local/bin
    
    # Copy files
    cp -r dist/KiAssist/* kiassist_1.0.0_amd64/opt/kiassist/
    cp kiassist.desktop kiassist_1.0.0_amd64/usr/share/applications/
    
    # Create control file
    cat > kiassist_1.0.0_amd64/DEBIAN/control << EOF
    Package: kiassist
    Version: 1.0.0
    ...
    EOF
    
    # Create symlink
    ln -s /opt/kiassist/KiAssist kiassist_1.0.0_amd64/usr/local/bin/kiassist
    
    # Build DEB
    dpkg-deb --build kiassist_1.0.0_amd64

- name: Upload DEB package
  if: matrix.platform == 'ubuntu-latest'
  uses: actions/upload-artifact@v4
  with:
    name: linux-deb
    path: kiassist_*.deb
```

**Expected Output**: `kiassist_1.0.0_amd64.deb` (~80-150 MB)

**Installation**:
```bash
sudo dpkg -i kiassist_1.0.0_amd64.deb
sudo apt-get install -f  # Install dependencies
```

**Installation Path**: `/opt/kiassist/`

**Features**:
- System integration (menu entry, desktop file)
- Dependency management via apt
- Standard uninstall via `apt remove kiassist`
- Preserves API keys in Secret Service

---

## Version Management

**Recommendation**: Inject version from single source of truth.

**Implementation**:

1. **Add version to `pyproject.toml`** (already exists):
```toml
[project]
version = "1.0.0"
```

2. **Read version in `kiassist.spec`**:
```python
import tomli  # or tomllib for Python 3.11+

with open('python-lib/pyproject.toml', 'rb') as f:
    config = tomli.load(f)
    version = config['project']['version']
```

3. **Pass version to installers**:
- Inno Setup: Use `#define MyAppVersion` from environment variable
- create-dmg: Use in filename
- AppImage: Use in filename
- DEB: Use in control file version field

---

## File Structure After Implementation

```
NewKiAssist/
├── kiassist.spec (modified)
├── installer.iss (new - Windows)
├── kiassist.desktop (new - Linux)
├── kiassist.png (new - icon)
├── .github/
│   └── workflows/
│       └── build.yml (modified)
├── dist/ (after PyInstaller)
│   └── KiAssist/ (one-folder output)
│       ├── KiAssist.exe (Windows)
│       ├── KiAssist (Linux)
│       └── *.dll, *.so, *.dylib (dependencies)
├── installer-output/ (after Inno Setup)
│   └── KiAssist-Setup-1.0.0.exe
└── KiAssist-1.0.0.dmg (after create-dmg)
```

---

## Rollout Strategy

### Stage 1: Development (Week 1)
- [ ] Modify `kiassist.spec` for one-folder mode
- [ ] Test build on all three platforms
- [ ] Measure startup time improvement
- [ ] Verify API key persistence

### Stage 2: Installer Prototypes (Week 2)
- [ ] Create Inno Setup script for Windows
- [ ] Create create-dmg workflow for macOS
- [ ] Create AppImage workflow for Linux
- [ ] Test installers manually on each platform

### Stage 3: CI/CD Integration (Week 3)
- [ ] Update `.github/workflows/build.yml`
- [ ] Add installer tool installation steps
- [ ] Add installer creation steps
- [ ] Upload installer artifacts instead of raw executables

### Stage 4: Documentation (Week 4)
- [ ] Update README.md with installation instructions
- [ ] Create INSTALLATION.md with platform-specific guides
- [ ] Document uninstallation process
- [ ] Document upgrade process (API key preservation)

### Stage 5: Optional Enhancements
- [ ] Add application icons (.ico, .icns, .png)
- [ ] Obtain code signing certificates (Windows, macOS)
- [ ] Create DEB/RPM packages for Linux
- [ ] Add update check mechanism (future feature)

---

## Risk Mitigation

| Risk | Probability | Impact | Mitigation |
|------|-------------|--------|------------|
| One-folder mode breaks functionality | Low | High | Test thoroughly on all platforms before release |
| Installer tools unavailable in CI | Low | Medium | Use fallback to ZIP/tarball if tool installation fails |
| Code signing delays release | High | Low | Release unsigned for initial versions, add signing later |
| Linux dependencies missing | Medium | Medium | Document dependencies clearly; provide AppImage as primary |
| Keyring data corruption | Very Low | High | Test upgrade path extensively; keyring is read-only during update |

---

## Success Metrics

1. **Startup Time**: 2-5 second reduction (from ~3-5s to ~0.5-1s)
2. **Installation UX**: One-click install on Windows/macOS, double-click on Linux
3. **Update Preservation**: 100% API key retention across updates
4. **Distribution Size**: <200 MB for all installers
5. **CI/CD Build Time**: <10 minutes per platform

---

## Conclusion

Proposed architecture provides:
- Professional installation experience on all platforms
- Significant performance improvement (2-5s startup time reduction)
- Zero user impact on data preservation (API keys persist automatically)
- Maintainable CI/CD integration with standard tools
- Low implementation complexity (4-week timeline)

**Recommendation**: Proceed with phased implementation starting with Phase 1 (PyInstaller configuration) and Phase 2 (Windows Inno Setup).
