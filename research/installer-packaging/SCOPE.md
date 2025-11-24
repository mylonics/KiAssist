# Research Scope: Installer Packaging for KiAssist

## Objective

Identify installer solutions for PyInstaller-based cross-platform desktop application to eliminate runtime decompression overhead and enable proper application updates.

## Context

- **Application**: KiAssist (KiCAD AI Assistant)
- **Current packaging**: PyInstaller single-file executables
  - Windows: `KiAssist.exe` (single file)
  - Linux: `KiAssist` (single file)
  - macOS: `KiAssist.app` (app bundle)
- **Backend**: Python 3.8+ with pywebview, requests, keyring
- **Frontend**: Vue.js (bundled)
- **Build system**: PyInstaller 6.0.0+
- **Data storage**: OS keyring (Windows Credential Manager, macOS Keychain, Linux Secret Service)

## Problem Statement

Single-file executables require decompression to temporary directory at each launch, causing:
1. Slower startup time
2. Disk I/O overhead
3. No persistent installation directory for updates

## Research Questions

### 1. Installer Tools
What installer creation tools are appropriate for each platform (Windows, macOS, Linux)?

**Constraints**:
- Must support directory-based PyInstaller distributions
- Must integrate with existing build workflows
- Must be well-maintained and production-ready
- Must support automated builds (CI/CD compatible)

### 2. PyInstaller Configuration
How to modify `kiassist.spec` to produce directory-based distributions instead of single-file executables?

**Constraints**:
- Must preserve cross-platform compatibility
- Must include frontend assets (`dist/` directory)
- Must maintain hidden imports for dependencies

### 3. Installation Directories
Where should installers place the application on each OS?

**Requirements**:
- Follow OS-specific conventions
- Support per-user and system-wide installation
- Comply with sandboxing and permissions models

### 4. Update Strategy
How to preserve user data (API keys) during application updates?

**Constraints**:
- API keys stored via OS keyring (already persisted)
- Must not corrupt existing keyring data
- Must handle version upgrades gracefully

### 5. Best Practices
What are established patterns for PyInstaller-based installer creation?

**Focus areas**:
- Dependency management
- Code signing requirements
- Uninstaller implementation
- File association handling

## Out of Scope

- Auto-update mechanisms (separate feature)
- Custom UI for installer
- Multiple language support for installer
- Package signing infrastructure setup
- App store distribution (Microsoft Store, Mac App Store)

## Success Criteria

Research deliverable must provide:
1. Specific tool recommendations with version constraints
2. Modified PyInstaller configuration example
3. Standard installation paths for each OS
4. Confirmation that keyring data persists across updates
5. Integration examples for CI/CD workflows

## Timeline

Research completion target: Single session (current)
