# Scope

## Objective

Research how NNG (nanomsg-next-gen) handles IPC on Windows, specifically:

1. Correct format for IPC paths on Windows with NNG
2. Whether NNG requires manual prepending of `\\.\pipe\` prefix on Windows
3. How the `ipc://` URL scheme should be formatted on Windows

## Context

KiCad uses NNG for IPC communication. On Windows, socket files are stored in `%TEMP%\kicad\api.sock`, and there is a question about whether connections fail due to improper path formatting for Windows named pipes.

## Constraints

- Research must use official NNG documentation and source code
- Must verify findings against pynng and kicad-python implementations
- Must provide actionable recommendations for KiAssist

## Success Criteria

1. Clear documentation of NNG IPC path handling on Windows
2. Validated path format examples
3. Comparison with kipy/kicad-python implementation
