# References

## 1. KiCad API Server Source Code
- **Source**: `KiCad/kicad-source-mirror` repository
- **File**: `common/api/api_server.cpp`
- **URL**: https://github.com/KiCad/kicad-source-mirror/blob/master/common/api/api_server.cpp
- **Date accessed**: 2025-11-24
- **Relevance**: Official KiCad API server implementation showing socket path construction and NNG initialization

## 2. KiCad KINNG Library Implementation
- **Source**: `KiCad/kicad-source-mirror` repository
- **File**: `libs/kinng/src/kinng.cpp`
- **URL**: https://github.com/KiCad/kicad-source-mirror/blob/master/libs/kinng/src/kinng.cpp
- **Date accessed**: 2025-11-24
- **Relevance**: KiCad's NNG wrapper showing how socket URLs are passed to NNG

## 3. NNG Official IPC Transport Documentation
- **Source**: `nanomsg/nng` repository
- **File**: `docs/ref/tran/ipc.md`
- **URL**: https://github.com/nanomsg/nng/blob/master/docs/ref/tran/ipc.md
- **Date accessed**: 2025-11-24
- **Relevance**: Official documentation specifying IPC URI formats and Windows named pipe handling

## 4. NNG Windows IPC Implementation Headers
- **Source**: `nanomsg/nng` repository
- **File**: `src/platform/windows/win_ipc.h`
- **URL**: https://github.com/nanomsg/nng/blob/master/src/platform/windows/win_ipc.h
- **Date accessed**: 2025-11-24
- **Relevance**: Defines `IPC_PIPE_PREFIX` as `\\.\pipe\`

## 5. NNG Windows IPC Dial Implementation
- **Source**: `nanomsg/nng` repository
- **File**: `src/platform/windows/win_ipcdial.c`
- **URL**: https://github.com/nanomsg/nng/blob/master/src/platform/windows/win_ipcdial.c
- **Date accessed**: 2025-11-24
- **Relevance**: Shows how NNG internally prepends `IPC_PIPE_PREFIX` to URL path

## 6. kicad-python (kipy) Official Library
- **Source**: `kicad/kicad-python` repository (GitLab), mirror at `timblakely/kicad-python`
- **File**: `kipy/kicad.py`
- **URL**: https://github.com/timblakely/kicad-python/blob/master/kipy/kicad.py
- **Date accessed**: 2025-11-24
- **Relevance**: Official KiCad Python API client showing expected socket path format

## 7. wxWidgets GetTempDir Documentation
- **Source**: wxWidgets documentation
- **URL**: https://docs.wxwidgets.org/3.0/classwx_standard_paths.html
- **Relevance**: Documents that `wxStandardPaths::GetTempDir()` returns platform-specific temp directory
