# Scope

## Objective

Research how KiCad's IPC API works on Windows, specifically:

1. Where does KiCad create its IPC endpoint on Windows?
2. Does KiCad create a `.sock` file in `%TEMP%\kicad\` on Windows, or only a named pipe at `\\.\pipe\`?
3. If a `.sock` file is created on Windows, does it contain the actual named pipe path or is it just a marker?
4. What is the actual named pipe path format that KiCad uses on Windows?

## Context

KiAssist needs to connect to KiCad's IPC API. Understanding the Windows-specific behavior is essential for socket discovery and connection.

## Constraints

- Research must use official KiCad source code
- Must verify findings against NNG library behavior
- Must provide actionable recommendations for KiAssist implementation

## Success Criteria

1. Clear documentation of KiCad's Windows IPC endpoint creation
2. Determination of whether `.sock` files exist on Windows
3. Verified named pipe path format
4. Recommendations for Windows socket discovery
