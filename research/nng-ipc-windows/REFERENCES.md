# References

## 1. NNG Official IPC Transport Documentation
- **Source**: `nanomsg/nng` repository, `docs/ref/tran/ipc.md`
- **URL**: https://github.com/nanomsg/nng/blob/master/docs/ref/tran/ipc.md
- **Date accessed**: 2025-11-24
- **Relevance**: Official documentation specifying IPC URI formats and Windows named pipe handling

## 2. NNG Windows IPC Header
- **Source**: `nanomsg/nng` repository, `src/platform/windows/win_ipc.h`
- **URL**: https://github.com/nanomsg/nng/blob/master/src/platform/windows/win_ipc.h
- **Date accessed**: 2025-11-24
- **Relevance**: Defines `IPC_PIPE_PREFIX` as `\\.\pipe\`

## 3. NNG Windows IPC Dial Implementation
- **Source**: `nanomsg/nng` repository, `src/platform/windows/win_ipcdial.c`
- **URL**: https://github.com/nanomsg/nng/blob/master/src/platform/windows/win_ipcdial.c
- **Date accessed**: 2025-11-24
- **Relevance**: Shows how NNG internally prepends `IPC_PIPE_PREFIX` to URL path

## 4. kicad-python (kipy) Source Code
- **Source**: `/home/runner/.local/lib/python3.12/site-packages/kipy/kicad.py`
- **Package version**: 0.5.0 (from PyPI)
- **Date accessed**: 2025-11-24
- **Relevance**: Official KiCad Python API implementation showing socket path construction

## 5. pynng Package
- **Source**: PyPI, version 0.8.1
- **URL**: https://pypi.org/project/pynng/
- **Date accessed**: 2025-11-24
- **Relevance**: Python bindings for NNG used by kicad-python

## 6. NNG IPC Transport Source
- **Source**: `nanomsg/nng` repository, `src/sp/transport/ipc/ipc.c`
- **URL**: https://github.com/nanomsg/nng/blob/master/src/sp/transport/ipc/ipc.c
- **Date accessed**: 2025-11-24
- **Relevance**: Core IPC transport implementation using `nng_stream_dialer_alloc_url`
