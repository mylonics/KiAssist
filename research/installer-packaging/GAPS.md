# Knowledge Gaps and Assumptions

## 1. PyInstaller One-Folder Build Size

**Gap**: Precise disk usage for KiAssist in one-folder mode is not measured.

**Assumption**: [HYPOTHESIS] One-folder build will be 80-150 MB based on:
- pywebview framework size (~30-50 MB with dependencies)
- Python runtime libraries (~20-40 MB)
- Frontend dist bundle (size unknown)
- Keyring and requests libraries (~5-10 MB)

**Impact**: Medium - affects installer size and download time

**Validation**: Build prototype in one-folder mode and measure actual disk usage

---

## 2. Frontend Bundle Size

**Gap**: Size of Vue.js frontend bundle in `dist/` directory not specified.

**Assumption**: [HYPOTHESIS] Frontend bundle is 5-15 MB based on typical Vue.js applications with minimal dependencies.

**Impact**: Low - contributes to total application size but does not affect architecture decision

**Validation**: Check `dist/` directory size after `npm run build`

---

## 3. Inno Setup Version Compatibility

**Gap**: Compatibility of Inno Setup with Python 3.11+ and latest PyInstaller not verified.

**Assumption**: [HYPOTHESIS] Inno Setup 6.3+ is compatible with PyInstaller 6.0+ output based on community examples in PyInstaller wiki [Ref 16].

**Impact**: Low - Inno Setup is file-based and version-agnostic for payloads

**Validation**: Test Inno Setup script with actual PyInstaller output

---

## 4. macOS Notarization Requirement for CI/CD

**Gap**: Whether automated notarization can be integrated into GitHub Actions without manual intervention.

**Assumption**: [HYPOTHESIS] macOS notarization can be automated using `notarytool` with stored credentials in GitHub Secrets based on Apple Developer documentation [Ref 8].

**Impact**: Medium - affects release automation capability

**Validation**: Review Apple Developer documentation for headless notarization workflow

---

## 5. AppImage Runtime Compatibility

**Gap**: Whether AppImage runtime works with pywebview's WebKit backend on all Linux distributions.

**Assumption**: [HYPOTHESIS] AppImage bundles all dependencies except base system libraries (glibc, X11/Wayland, WebKit), which are expected to be present on modern Linux desktop systems.

**Impact**: Medium - affects Linux distribution compatibility

**Validation**: Test AppImage on Debian, Ubuntu, Fedora, Arch Linux virtual machines

**Mitigation**: If WebKit dependency causes issues, document required system packages or consider Flatpak as alternative.

---

## 6. Secret Service Availability on Linux

**Gap**: Percentage of Linux desktop systems with Secret Service API available.

**Assumption**: [HYPOTHESIS] 90%+ of Linux desktop installations have Secret Service available via GNOME Keyring, KDE Wallet, or compatible backend.

**Impact**: Low - keyring library has fallback to file-based storage [Ref 18]

**Validation**: Test on minimal Linux installations (server-turned-desktop)

---

## 7. Windows SmartScreen Impact

**Gap**: User experience impact of SmartScreen warnings for unsigned executables.

**Assumption**: [HYPOTHESIS] 30-50% of users will be deterred by SmartScreen "Unknown publisher" warnings, reducing adoption.

**Impact**: Medium - affects initial distribution strategy

**Validation**: User testing with unsigned installer

**Mitigation**: 
- Provide clear installation instructions with screenshots
- Obtain code signing certificate for official releases
- Establish reputation with Microsoft SmartScreen over time

---

## 8. Incremental Update Feasibility

**Gap**: Whether directory-based installation enables efficient incremental updates (e.g., only replacing changed .pyc files).

**Assumption**: [HYPOTHESIS] Incremental updates are **not practical** because:
- PyInstaller output is opaque (compiled bytecode)
- No stable file-level diffing between versions
- Installer tools typically replace entire application directory

**Impact**: Low - full reinstall is acceptable for desktop applications

**Validation**: Compare file-level diffs between PyInstaller builds of different versions

**Note**: Full application reinstall is standard practice; this is not a limitation.

---

## 9. GitHub Actions Runner Disk Space

**Gap**: Whether GitHub Actions runners have sufficient disk space for building installers on all platforms.

**Assumption**: [HYPOTHESIS] GitHub-hosted runners provide 14 GB free disk space, sufficient for:
- Source code (~100 MB)
- Node modules (~200 MB)
- Python environment (~500 MB)
- PyInstaller build (~150 MB)
- Installer creation (~200 MB)
- Total: ~1.2 GB

**Impact**: Low - disk space constraints are unlikely

**Validation**: Monitor runner disk usage during builds

**Mitigation**: Use `actions/cache` to reduce redundant downloads

---

## 10. Version Numbering Strategy

**Gap**: No version numbering scheme defined in current codebase.

**Assumption**: [HYPOTHESIS] Semantic versioning (MAJOR.MINOR.PATCH) will be adopted based on industry standard.

**Impact**: Low - affects installer naming and upgrade logic

**Validation**: Review project requirements for versioning

**Recommendation**: Add version to `pyproject.toml` and inject into installers

---

## 11. Uninstaller Cleanup Requirements

**Gap**: What files/registry keys should uninstallers remove vs. preserve.

**Assumption**: [HYPOTHESIS] Uninstallers should:
- Remove application files (always)
- Remove Start Menu shortcuts (always)
- Remove Desktop shortcuts (always)
- Preserve API keys in keyring (default)
- Offer option to "Remove saved data" that clears keyring (optional)

**Impact**: Medium - affects user experience during uninstall

**Validation**: Review user expectations for data preservation

---

## 12. Cross-Version Compatibility

**Gap**: Whether API key storage format is stable across application versions.

**Assumption**: [HYPOTHESIS] Keyring storage format is stable because:
- Service name `"KiAssist"` is hardcoded (constant)
- Key name `"gemini_api_key"` is hardcoded (constant)
- OS keyring APIs are backward compatible

**Impact**: High - affects upgrade reliability

**Validation**: Test upgrade from v1.0 to v2.0 with stored API key

**Mitigation**: Do not change `SERVICE_NAME` or `KEY_NAME` constants in future versions

---

## 13. Python Runtime Dependencies in One-Folder Mode

**Gap**: Whether PyInstaller one-folder mode requires any additional runtime dependencies not bundled.

**Assumption**: [HYPOTHESIS] PyInstaller bundles all Python runtime dependencies except:
- Windows: Visual C++ Redistributable (usually pre-installed)
- macOS: None (self-contained)
- Linux: glibc, libz (standard system libraries)

**Impact**: Medium - affects installation documentation

**Validation**: Test on clean Windows/macOS/Linux installations without development tools

---

## 14. Application Icon Handling

**Gap**: Current spec file has `icon=None` - no icon file specified.

**Assumption**: [HYPOTHESIS] Application will use default platform icons until custom icons are created.

**Impact**: Low - cosmetic issue, does not affect functionality

**Validation**: Check if default icons are acceptable

**Recommendation**: Create `.ico` (Windows), `.icns` (macOS), `.png` (Linux) icon files for professional appearance

---

## 15. Startup Time Measurement Precision

**Gap**: Actual startup time improvements not measured on target hardware.

**Assumption**: [HYPOTHESIS] One-folder mode provides 2-5 second startup improvement based on:
- Decompression benchmark estimates (1-3 seconds)
- File I/O overhead reduction (0.5-2 seconds)

**Impact**: Low - improvement direction is confirmed, exact magnitude varies by hardware

**Validation**: Benchmark startup time on Windows, macOS, Linux with both modes

---

## Summary of High-Impact Gaps

1. **macOS notarization automation** (Gap #4) - required for production macOS releases
2. **Cross-version compatibility** (Gap #12) - critical for user data preservation
3. **AppImage runtime compatibility** (Gap #5) - affects Linux distribution strategy

All other gaps have low-medium impact and can be validated during implementation phase.
