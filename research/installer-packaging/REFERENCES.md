# References: Installer Packaging Research

## PyInstaller Documentation

[1] PyInstaller Documentation - "Using Spec Files"  
https://pyinstaller.org/en/stable/spec-files.html  
Official documentation for PyInstaller spec file configuration.

[2] PyInstaller Documentation - "One-Folder vs One-File Mode"  
https://pyinstaller.org/en/stable/operating-mode.html#bundling-to-one-folder  
Comparison of bundling modes, performance implications.

[3] PyInstaller Documentation - "Platform-Specific Options"  
https://pyinstaller.org/en/stable/usage.html#platform-specific-options  
Platform-specific bundling and executable options.

## Windows Installer Tools

[4] Inno Setup Documentation  
https://jrsoftware.org/isinfo.php  
Free installer creator for Windows applications. Version 6.x stable.

[5] NSIS (Nullsoft Scriptable Install System)  
https://nsis.sourceforge.io/  
Open-source installer creation system for Windows.

[6] WiX Toolset Documentation  
https://wixtoolset.org/documentation/  
Windows Installer XML toolset for creating MSI packages.

[7] Microsoft - "Windows Application Installation Guidelines"  
https://docs.microsoft.com/en-us/windows/win32/msi/installation-best-practices  
Official Microsoft guidelines for application installation paths.

## macOS Packaging

[8] Apple Developer - "Distributing Your App Outside the Mac App Store"  
https://developer.apple.com/documentation/xcode/distributing-your-app-outside-the-mac-app-store  
Official Apple guidance for macOS app distribution.

[9] create-dmg (GitHub: sindresorhus/create-dmg)  
https://github.com/sindresorhus/create-dmg  
Tool for creating DMG installers for macOS applications.

[10] Apple - "File System Programming Guide - Standard Directories"  
https://developer.apple.com/library/archive/documentation/FileManagement/Conceptual/FileSystemProgrammingGuide/FileSystemOverview/FileSystemOverview.html  
macOS standard installation directory conventions.

## Linux Packaging

[11] Debian New Maintainers' Guide - "Packaging Tutorial"  
https://www.debian.org/doc/manuals/maint-guide/  
Guide for creating .deb packages for Debian/Ubuntu.

[12] Fedora Packaging Guidelines  
https://docs.fedoraproject.org/en-US/packaging-guidelines/  
RPM packaging standards for Fedora/RHEL distributions.

[13] AppImage Documentation  
https://docs.appimage.org/  
Self-contained application format for Linux distributions.

[14] FreeDesktop.org - "Desktop Entry Specification"  
https://specifications.freedesktop.org/desktop-entry-spec/latest/  
Standard for .desktop files and application integration on Linux.

[15] Filesystem Hierarchy Standard (FHS) 3.0  
https://refspecs.linuxfoundation.org/FHS_3.0/fhs/index.html  
Linux directory structure standard, defines `/opt`, `/usr/local`, etc.

## Cross-Platform Installer Tools

[16] PyInstaller + Inno Setup Integration Examples  
https://github.com/pyinstaller/pyinstaller/wiki/FAQ#how-to-create-windows-installer  
Community examples of PyInstaller with installer tools.

[17] electron-builder Documentation  
https://www.electron.build/  
Cross-platform installer tool (reference for patterns, though Electron-specific).

## OS Keyring Documentation

[18] Python keyring Library Documentation  
https://github.com/jaraco/keyring  
Official documentation for the keyring library used in KiAssist.

[19] Windows Credential Manager API  
https://docs.microsoft.com/en-us/windows/win32/secauthn/credential-manager  
Windows credential storage system documentation.

[20] macOS Keychain Services  
https://developer.apple.com/documentation/security/keychain_services  
macOS secure storage API documentation.

[21] Linux Secret Service API (freedesktop.org)  
https://specifications.freedesktop.org/secret-service/  
Linux credential storage specification.

## CI/CD Integration

[22] GitHub Actions - "Building and Testing Python"  
https://docs.github.com/en/actions/automating-builds-and-tests/building-and-testing-python  
CI/CD integration patterns for Python applications.

[23] GitHub Actions - upload-artifact  
https://github.com/actions/upload-artifact  
Artifact management for build outputs in GitHub Actions.
