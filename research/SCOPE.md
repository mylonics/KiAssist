# Research Scope: KiCAD IPC Instance Detection and Selection

## Objective

Research KiCAD's IPC (Inter-Process Communication) API to enable instance detection and project selection in a Tauri + Vue application.

## Requirements

1. Determine IPC protocol and transport mechanism
2. Identify platform-specific socket/pipe locations
3. Document connection format and authentication
4. Specify methods to detect all open KiCAD instances
5. Define approach to retrieve project names from instances

## Constraints

- Target platforms: Windows, Linux, macOS
- Must support KiCAD 9.0+ (IPC API introduced in 9.0)
- Implementation language: Rust (Tauri backend) or TypeScript/JavaScript
- No direct Python dependency in production code

## Deliverables

- Technical specifications for IPC implementation
- Platform-specific path resolution logic
- Connection and authentication protocol
- Instance enumeration algorithm
- Project name retrieval method
