# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec file for KiAssist cross-platform packaging."""

import os
import sys
from pathlib import Path

# Get the repository root directory
# SPECPATH is the directory where the spec file is located (repository root)
repo_root = Path(SPECPATH)
dist_dir = repo_root / "dist"
python_lib_dir = repo_root / "python-lib"

# Determine the platform
if sys.platform == "win32":
    platform_name = "windows"
elif sys.platform == "darwin":
    platform_name = "macos"
else:
    platform_name = "linux"

a = Analysis(
    [str(repo_root / "run_kiassist.py")],
    pathex=[str(python_lib_dir)],
    binaries=[],
    datas=[
        # Include the frontend dist directory
        (str(dist_dir), "dist"),
    ],
    hiddenimports=[
        "kiassist_utils",
        "kiassist_utils.main",
        "kiassist_utils.api_key",
        "kiassist_utils.gemini",
        "kiassist_utils.kicad_ipc",
        "keyring",
        "requests",
        "webview",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="KiAssist",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,  # No console window on Windows
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,  # No icon file currently
)

# macOS specific: create an app bundle
if sys.platform == "darwin":
    app = BUNDLE(
        exe,
        name="KiAssist.app",
        icon=None,  # No icon file currently
        bundle_identifier="com.kiassist.app",
        info_plist={
            "NSPrincipalClass": "NSApplication",
            "NSHighResolutionCapable": "True",
        },
    )
