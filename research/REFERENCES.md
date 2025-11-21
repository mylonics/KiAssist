# References

## Primary Sources

1. **kicad-python (Official Library)**
   - Repository: https://github.com/timblakely/kicad-python
   - Files: `kipy/kicad.py`, `kipy/client.py`
   - Description: Official Python client for KiCAD IPC API
   - License: MIT

2. **atopile IPC Implementation**
   - Repository: https://github.com/atopile/atopile
   - Files: `src/faebryk/libs/kicad/ipc.py`, `src/faebryk/libs/kicad/paths.py`
   - Description: Production implementation with multi-instance detection
   - License: MIT

3. **KiCAD-MCP-Server**
   - Repository: https://github.com/mixelpixx/KiCAD-MCP-Server
   - File: `python/kicad_api/ipc_backend.py`
   - Description: Model Context Protocol implementation for KiCAD
   - License: Not specified

4. **KiCAD Official Documentation**
   - URL: https://dev-docs.kicad.org/en/apis-and-binding/ipc-api/
   - Description: Official IPC API developer documentation
   - Note: Referenced in source comments

## Secondary Sources

5. **pynng Library**
   - Package: pynng (NNG messaging library bindings)
   - Description: Nanomsg-next-generation transport used by KiCAD IPC
   - Protocol: Request-Reply pattern (Req0)

6. **Protocol Buffers**
   - Technology: Google Protocol Buffers
   - Description: Serialization format for IPC messages
   - Files: `proto/common/ApiRequest`, `proto/common/ApiResponse`
