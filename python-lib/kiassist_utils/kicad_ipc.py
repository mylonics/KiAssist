"""KiCad IPC instance detection module."""

import ctypes
import ctypes.wintypes
import json
import logging
import os
import platform
import re
import subprocess
from pathlib import Path
from tempfile import gettempdir
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)

# Cache the current OS platform
_CURRENT_PLATFORM = platform.system()


class KiCadInstance:
    """Represents a detected KiCad instance."""
    
    def __init__(self, socket_path: str, project_name: str = "Unknown Project", 
                 display_name: str = "", version: str = "", project_path: str = "",
                 pcb_path: str = "", schematic_path: str = "",
                 pcb_open: bool = False, schematic_open: bool = False):
        """Initialize KiCad instance.
        
        Args:
            socket_path: Path to the IPC socket
            project_name: Name of the project
            display_name: Display name for UI
            version: KiCad version string
            project_path: Path to the project file
            pcb_path: Path to the PCB file
            schematic_path: Path to the schematic file
            pcb_open: Whether the PCB editor is confirmed open
            schematic_open: Whether the schematic editor is confirmed open
        """
        self.socket_path = socket_path
        self.project_name = project_name
        self.display_name = display_name or project_name
        self.version = version
        self.project_path = project_path
        self.pcb_path = pcb_path
        self.schematic_path = schematic_path
        self.pcb_open = pcb_open
        self.schematic_open = schematic_open
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert instance to dictionary.
        
        Returns:
            Dictionary representation
        """
        return {
            "socket_path": self.socket_path,
            "project_name": self.project_name,
            "display_name": self.display_name,
            "version": self.version,
            "project_path": self.project_path,
            "pcb_path": self.pcb_path,
            "schematic_path": self.schematic_path,
            "pcb_open": self.pcb_open,
            "schematic_open": self.schematic_open,
        }


def get_ipc_socket_dir() -> Path:
    """Get the base directory where KiCad IPC sockets are stored.
    
    Returns:
        Path to the socket directory
    """
    if _CURRENT_PLATFORM == "Windows":
        temp = gettempdir()
        return Path(temp) / "kicad"
    else:
        # Check for flatpak socket path first
        home = os.environ.get('HOME')
        if home is not None:
            flatpak_socket_path = Path(home) / '.var/app/org.kicad.KiCad/cache/tmp/kicad'
            if flatpak_socket_path.exists():
                return flatpak_socket_path
        return Path("/tmp/kicad")


def discover_socket_files() -> List[Path]:
    r"""Discover all KiCad IPC socket files.
    
    On Windows: Enumerates named pipes in \\.\pipe\ to find KiCad sockets.
    On Linux/macOS: Scans the socket directory for socket files matching the pattern.
    
    Pattern: api.sock (main instance) or api-<PID>.sock (additional instances)
    
    Returns:
        List of paths to socket files
    """
    socket_dir = get_ipc_socket_dir()
    sockets = []
    
    if _CURRENT_PLATFORM == "Windows":
        # On Windows, KiCad IPC uses named pipes via NNG.
        # Enumerate pipes using os.listdir which is orders of magnitude faster
        # than spawning a PowerShell subprocess.
        try:
            # Normalize socket_dir for case-insensitive comparison with forward slashes
            # (NNG on Windows uses forward slashes in pipe names)
            temp_dir_normalized = str(socket_dir).replace('\\', '/').lower().rstrip('/')

            for pipe_name in os.listdir(r'\\.\pipe'):
                pipe_lower = pipe_name.replace('\\', '/').lower()

                # Check if this pipe belongs to our kicad socket dir
                prefix = temp_dir_normalized + '/'
                if not pipe_lower.startswith(prefix):
                    continue

                # Extract the filename part after the directory prefix using
                # the original-case pipe name so the path matches what NNG
                # registered (named pipe matching is case-insensitive on
                # Windows but using original case avoids any edge cases).
                filename = pipe_name.replace('\\', '/')[len(prefix):]
                filename_lower = pipe_lower[len(prefix):]

                # Validate the pattern: api.sock or api-<PID>.sock
                if filename_lower == "api.sock" or (
                    filename_lower.startswith("api-") and
                    filename_lower.endswith(".sock") and
                    filename_lower[4:-5].isdigit()
                ):
                    # Reconstruct the path using socket_dir for consistent format
                    sockets.append(socket_dir / filename)
        except Exception as e:
            logger.warning("Could not enumerate Windows named pipes: %s", e)

        return sockets
    
    # On Linux/macOS, scan the directory for actual socket files
    if not socket_dir.exists():
        return sockets
    
    # Look for files matching api*.sock pattern
    try:
        for entry in socket_dir.iterdir():
            if entry.is_file() or entry.is_socket():
                filename = entry.name
                # Match api.sock or api-<PID>.sock pattern
                if filename.startswith("api") and filename.endswith(".sock"):
                    # Additional validation: check for api.sock or api-<digits>.sock
                    if filename == "api.sock" or (
                        filename.startswith("api-") and 
                        filename[4:-5].isdigit()  # Check that middle part is a number (PID)
                    ):
                        sockets.append(entry)
    except Exception as e:
        logger.warning("Could not scan socket directory: %s", e)
    
    # Sort sockets by name to ensure consistent ordering
    sockets.sort()
    return sockets


def socket_path_to_uri(socket_file: Path) -> str:
    """Convert a socket file path to the IPC URI format.
    
    Args:
        socket_file: Path to the socket file
        
    Returns:
        IPC URI string
    """
    return f"ipc://{socket_file}"


def _get_kicad_file_history() -> List[str]:
    """Read KiCad's recent project file history from its config.
    
    Returns:
        List of .kicad_pro file paths from KiCad's file_history, newest first.
    """
    try:
        if _CURRENT_PLATFORM == "Windows":
            appdata = os.environ.get('APPDATA', '')
            if not appdata:
                return []
            config_base = Path(appdata) / "kicad"
        else:
            config_base = Path.home() / ".config" / "kicad"
        
        if not config_base.exists():
            return []
        
        # Find the newest kicad.json (highest version number)
        candidates = []
        for d in config_base.iterdir():
            kicad_json = d / "kicad.json"
            if d.is_dir() and kicad_json.exists():
                candidates.append(kicad_json)
        
        # Sort by version number descending (e.g. 10.0 > 9.0)
        def version_key(p: Path) -> float:
            try:
                return float(p.parent.name)
            except ValueError:
                return 0.0
        candidates.sort(key=version_key, reverse=True)
        
        # Merge histories from all versions, newest first, deduplicating
        seen: set = set()
        merged: list = []
        for kicad_json in candidates:
            try:
                with open(kicad_json, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                history = data.get('system', {}).get('file_history', [])
                for h in history:
                    if isinstance(h, str) and h not in seen:
                        seen.add(h)
                        merged.append(h)
            except Exception:
                continue
        
        return merged
    except Exception:
        return []


def _get_kicad_process_info() -> List[Dict[str, str]]:
    """Get info about running KiCad processes.
    
    Returns:
        List of dicts with 'pid' and 'title' keys.
    """
    results = []
    try:
        if _CURRENT_PLATFORM == "Windows":
            # Use Win32 API directly (much faster than spawning PowerShell)
            results = _get_kicad_process_info_win32()
        else:
            # On Linux, try wmctrl or xdotool
            result = subprocess.run(
                ['wmctrl', '-l', '-p'],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                for line in result.stdout.strip().split('\n'):
                    if 'kicad' in line.lower():
                        parts = line.split(None, 4)
                        if len(parts) >= 5:
                            results.append({'pid': parts[2], 'title': parts[4]})
    except Exception:
        pass
    return results


def _get_kicad_process_info_win32() -> List[Dict[str, str]]:
    """Get KiCad process info using Win32 API (EnumWindows).
    
    Much faster than spawning PowerShell — completes in milliseconds.
    
    Returns:
        List of dicts with 'pid' and 'title' keys.
    """
    results = []
    try:
        user32 = ctypes.windll.user32  # type: ignore[attr-defined]
        
        EnumWindows = user32.EnumWindows
        GetWindowTextW = user32.GetWindowTextW
        GetWindowTextLengthW = user32.GetWindowTextLengthW
        GetWindowThreadProcessId = user32.GetWindowThreadProcessId
        IsWindowVisible = user32.IsWindowVisible
        
        WNDENUMPROC = ctypes.WINFUNCTYPE(
            ctypes.wintypes.BOOL,
            ctypes.wintypes.HWND,
            ctypes.wintypes.LPARAM,
        )
        
        def _enum_callback(hwnd: int, _lparam: int) -> bool:
            if not IsWindowVisible(hwnd):
                return True
            
            length = GetWindowTextLengthW(hwnd)
            if length == 0:
                return True
            
            buf = ctypes.create_unicode_buffer(length + 1)
            GetWindowTextW(hwnd, buf, length + 1)
            title = buf.value
            
            if not title or 'kicad' not in title.lower():
                return True
            
            pid = ctypes.wintypes.DWORD()
            GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            
            results.append({'pid': str(pid.value), 'title': title})
            return True
        
        EnumWindows(WNDENUMPROC(_enum_callback), 0)
    except Exception:
        pass
    return results


def _extract_project_name_from_title(title: str) -> Optional[str]:
    """Extract the project name from a KiCad window title.
    
    Window titles follow patterns like:
        "project_name \u2014 KiCad"
        "project_name \u2014 PCB Editor"
        "project_name \u2014 Schematic Editor"
        "project_name - PCB Editor"
    
    Returns:
        Project name or None
    """
    if not title:
        return None
    
    # Split on em-dash (\u2014) or regular dash with spaces
    for sep in ['\u2014', ' \u2014 ', ' - ']:
        if sep in title:
            name = title.split(sep)[0].strip()
            if name:
                return name
    
    return None


def _find_project_path_for_name(project_name: str, file_history: List[str]) -> Optional[str]:
    """Find the project directory path matching a project name in the file history.
    
    Args:
        project_name: Project name (e.g. 'myl-dbg')
        file_history: List of .kicad_pro file paths
    
    Returns:
        Project directory path or None
    """
    for pro_file in file_history:
        pro_path = Path(pro_file)
        if pro_path.stem == project_name:
            # Return the parent directory (project directory)
            project_dir = pro_path.parent
            if project_dir.exists():
                return str(project_dir)
    return None


def _fallback_detect_project(socket_path: str) -> Optional[Dict[str, str]]:
    """Fallback: detect project from KiCad process window title + config file history.
    
    Used when the IPC API cannot provide project info (e.g. no editor frames open).
    
    Args:
        socket_path: The IPC socket path (used to match PID for multi-instance)
    
    Returns:
        Dict with project_name, project_path, or None
    """
    try:
        processes = _get_kicad_process_info()
        if not processes:
            return None
        
        # Try to match socket to process via PID embedded in socket filename
        # Socket format: ipc://...\api-<PID>.sock or ipc://...\api.sock
        target_pid = None
        sock_basename = Path(socket_path.replace('ipc://', '')).name
        pid_match = re.match(r'api-(\d+)\.sock', sock_basename)
        if pid_match:
            target_pid = pid_match.group(1)
        
        # Find the matching process
        proc_info = None
        if target_pid:
            for p in processes:
                if p['pid'] == target_pid:
                    proc_info = p
                    break
        
        # For api.sock (no PID), find the process that does NOT have its own
        # api-<PID>.sock socket — those PIDs are claimed by other sockets.
        if not proc_info and not target_pid:
            claimed_pids = set()
            try:
                all_sockets = discover_socket_files()
                for s in all_sockets:
                    m = re.match(r'api-(\d+)\.sock', s.name)
                    if m:
                        claimed_pids.add(m.group(1))
            except Exception:
                pass
            
            # Pick the first process whose PID is not claimed by another socket
            for p in processes:
                if p['pid'] not in claimed_pids:
                    proc_info = p
                    break
            
            # Last resort: first process
            if not proc_info:
                proc_info = processes[0]
        
        if not proc_info:
            return None
        
        project_name = _extract_project_name_from_title(proc_info['title'])
        if not project_name:
            return None
        
        # Look up full path from file history
        file_history = _get_kicad_file_history()
        project_path = _find_project_path_for_name(project_name, file_history)
        
        return {
            'project_name': project_name,
            'project_path': project_path or '',
        }
    except Exception:
        return None


def probe_kicad_instance(socket_path: str) -> Optional[KiCadInstance]:
    """Try to connect to a KiCad instance and retrieve its information.
    
    Args:
        socket_path: Path to the IPC socket
        
    Returns:
        KiCadInstance if successful, None otherwise
    """
    try:
        # Try to import kicad-python API - this is optional
        try:
            from kipy import KiCad
            from kipy.proto.common.types import base_types_pb2
        except ImportError:
            logger.warning("kicad-python package not available. KiCad detection disabled.")
            return None
        
        # Create KiCad connection with required parameters
        # socket_path, client_name, timeout_ms
        kicad = KiCad(
            socket_path=socket_path,
            client_name="kiassist-probe",
            timeout_ms=5000
        )
        
        # Get version
        try:
            version = kicad.get_version()
            version_str = str(version)
        except Exception:
            version_str = "Unknown"
        
        # Try to get open documents to determine project name and file paths
        project_name = "No Project Open"
        project_path = ""
        pcb_path = ""
        schematic_path = ""
        pcb_open = False
        schematic_open = False
        
        # Try to get project info via DOCTYPE_PROJECT first
        try:
            project_docs = kicad.get_open_documents(base_types_pb2.DocumentType.DOCTYPE_PROJECT)
            if project_docs and len(project_docs) > 0:
                doc = project_docs[0]
                if doc.project and doc.project.path:
                    project_path = doc.project.path
                    project_name = Path(project_path).stem
        except Exception:
            pass  # Expected if not supported
        
        try:
            # Get PCB documents
            pcb_docs = kicad.get_open_documents(base_types_pb2.DocumentType.DOCTYPE_PCB)
            if pcb_docs and len(pcb_docs) > 0:
                pcb_open = True
                doc = pcb_docs[0]
                if not project_path and doc.project and doc.project.path:
                    project_path = doc.project.path
                    project_name = Path(project_path).stem
                # Try to get the PCB file path from the document
                try:
                    if hasattr(doc, 'board_filename') and doc.board_filename:
                        pcb_path = doc.board_filename
                    elif hasattr(doc, 'path'):
                        pcb_path = doc.path
                    elif hasattr(doc, 'file_path'):
                        pcb_path = doc.file_path
                except Exception as e:
                    logger.debug("Could not get PCB path from document: %s", e)
        except Exception as e:
            # "no handler available" is expected when KiCad PCB editor isn't open yet
            if "no handler" not in str(e).lower():
                logger.warning("Could not get PCB documents: %s", e)
        
        try:
            # Get schematic documents
            sch_docs = kicad.get_open_documents(base_types_pb2.DocumentType.DOCTYPE_SCHEMATIC)
            if sch_docs and len(sch_docs) > 0:
                schematic_open = True
                doc = sch_docs[0]
                # Use project path from schematic if not set yet
                if not project_path and doc.project and doc.project.path:
                    project_path = doc.project.path
                    project_name = Path(project_path).stem
                # Try to get the schematic file path from the document
                try:
                    if hasattr(doc, 'path'):
                        schematic_path = doc.path
                    elif hasattr(doc, 'file_path'):
                        schematic_path = doc.file_path
                except Exception as e:
                    logger.debug("Could not get schematic path from document: %s", e)
        except Exception as e:
            # "no handler available" is expected when KiCad schematic editor isn't open yet
            if "no handler" not in str(e).lower():
                logger.warning("Could not get schematic documents: %s", e)
        
        # Fallback: if IPC didn't give us a project, try process window title
        if not project_path:
            fallback = _fallback_detect_project(socket_path)
            if fallback:
                project_name = fallback['project_name']
                project_path = fallback.get('project_path', '')
        
        # Always try to guess missing file paths from the project directory
        if project_path:
            try:
                project_dir = Path(project_path)
                if project_dir.is_dir():
                    if not pcb_path:
                        # Prefer file matching project name
                        expected_pcb = project_dir / f"{project_name}.kicad_pcb"
                        if expected_pcb.exists():
                            pcb_path = str(expected_pcb)
                        else:
                            pcb_files = list(project_dir.glob("*.kicad_pcb"))
                            if pcb_files:
                                pcb_path = str(pcb_files[0])
                    
                    if not schematic_path:
                        expected_sch = project_dir / f"{project_name}.kicad_sch"
                        if expected_sch.exists():
                            schematic_path = str(expected_sch)
                        else:
                            sch_files = list(project_dir.glob("*.kicad_sch"))
                            if sch_files:
                                schematic_path = str(sch_files[0])
            except Exception as e:
                logger.debug("Could not search project directory: %s", e)
        
        display_name = project_name if project_name != "No Project Open" else "KiCad (No Project)"
        
        return KiCadInstance(
            socket_path=socket_path,
            project_name=project_name,
            display_name=display_name,
            version=version_str,
            project_path=project_path,
            pcb_path=pcb_path,
            schematic_path=schematic_path,
            pcb_open=pcb_open,
            schematic_open=schematic_open
        )
    except Exception as e:
        logger.warning("Could not probe KiCad instance at %s: %s", socket_path, e)
        return None


def detect_kicad_instances() -> List[Dict[str, str]]:
    """Detect all available KiCad instances.
    
    Returns:
        List of dictionaries representing KiCad instances
    """
    socket_files = discover_socket_files()
    instances = []
    
    for socket_file in socket_files:
        socket_uri = socket_path_to_uri(socket_file)
        
        # Try to connect to this instance
        instance = probe_kicad_instance(socket_uri)
        if instance:
            instances.append(instance.to_dict())
    
    return instances


def _get_doc_path(doc: Any) -> str:
    """Extract the file path from a kipy document specifier.

    Different kipy versions expose the path via different attribute names;
    this helper tries them all in the same order the probe code uses.

    Args:
        doc: A document specifier returned by ``KiCad.get_open_documents()``.

    Returns:
        The file path string, or an empty string if none could be found.
    """
    for attr in ("path", "file_path", "board_filename"):
        try:
            val = getattr(doc, attr, None)
            if val:
                return str(val)
        except Exception:
            pass
    return ""


def ipc_save_document(file_path: str) -> Dict[str, Any]:
    """Save an open KiCad document via the IPC API (``SaveDocument``).

    Iterates over all discovered KiCad IPC sockets, locates the document
    whose path matches *file_path*, and calls ``kicad.save_document()`` on
    it.  This is the programmatic equivalent of the user pressing Ctrl+S in
    the KiCad editor.

    Args:
        file_path: Path to the ``.kicad_sch`` or ``.kicad_pcb`` file to save.

    Returns:
        Dict with:

        * ``success`` — ``True`` if the document was saved via IPC.
        * ``method``  — ``"ipc"`` on success.
        * ``socket``  — the socket path used on success.
        * ``error``   — description of the failure when ``success`` is
          ``False``.
    """
    try:
        from kipy import KiCad
        from kipy.proto.common.types import base_types_pb2
    except ImportError:
        return {"success": False, "error": "kicad-python package not available"}

    norm = os.path.normpath(os.path.abspath(file_path))
    socket_files = discover_socket_files()

    if not socket_files:
        return {"success": False, "error": "No KiCad IPC sockets found"}

    _DOC_TYPES = [
        base_types_pb2.DocumentType.DOCTYPE_PCB,
        base_types_pb2.DocumentType.DOCTYPE_SCHEMATIC,
    ]

    for socket_file in socket_files:
        socket_uri = socket_path_to_uri(socket_file)
        try:
            kicad = KiCad(socket_path=socket_uri, client_name="kiassist-save", timeout_ms=5000)
            for doc_type in _DOC_TYPES:
                try:
                    docs = kicad.get_open_documents(doc_type)
                except Exception:
                    continue
                for doc in docs or []:
                    doc_path = _get_doc_path(doc)
                    if doc_path and os.path.normpath(os.path.abspath(doc_path)) == norm:
                        kicad.save_document(doc)
                        return {"success": True, "socket": str(socket_file), "method": "ipc"}
        except Exception:
            continue

    return {
        "success": False,
        "error": f"No open KiCad document found matching {file_path}",
    }


def ipc_revert_document(file_path: str) -> Dict[str, Any]:
    """Reload an open KiCad document from disk via the IPC API (``RevertDocument``).

    Locates the document whose path matches *file_path* in all running KiCad
    instances and calls ``kicad.revert_document()`` (discard in-memory
    changes, re-read from disk) followed by ``kicad.refresh_editor()``
    (force the editor UI to redraw).

    Args:
        file_path: Path to the ``.kicad_sch`` or ``.kicad_pcb`` file to
                   reload.

    Returns:
        Dict with:

        * ``success`` — ``True`` if the document was reverted via IPC.
        * ``method``  — ``"ipc"`` on success.
        * ``socket``  — the socket path used on success.
        * ``error``   — description of the failure when ``success`` is
          ``False``.
    """
    try:
        from kipy import KiCad
        from kipy.proto.common.types import base_types_pb2
    except ImportError:
        return {"success": False, "error": "kicad-python package not available"}

    norm = os.path.normpath(os.path.abspath(file_path))
    socket_files = discover_socket_files()

    if not socket_files:
        return {"success": False, "error": "No KiCad IPC sockets found"}

    _DOC_TYPES = [
        base_types_pb2.DocumentType.DOCTYPE_PCB,
        base_types_pb2.DocumentType.DOCTYPE_SCHEMATIC,
    ]

    for socket_file in socket_files:
        socket_uri = socket_path_to_uri(socket_file)
        try:
            kicad = KiCad(
                socket_path=socket_uri, client_name="kiassist-reload", timeout_ms=5000
            )
            for doc_type in _DOC_TYPES:
                try:
                    docs = kicad.get_open_documents(doc_type)
                except Exception:
                    continue
                for doc in docs or []:
                    doc_path = _get_doc_path(doc)
                    if doc_path and os.path.normpath(os.path.abspath(doc_path)) == norm:
                        kicad.revert_document(doc)
                        try:
                            kicad.refresh_editor(doc)
                        except Exception:
                            pass  # refresh_editor is best-effort
                        return {"success": True, "socket": str(socket_file), "method": "ipc"}
        except Exception:
            continue

    return {
        "success": False,
        "error": f"No open KiCad document found matching {file_path}",
    }


def get_open_project_paths() -> List[str]:
    """Get list of project paths from currently open KiCad instances.
    
    Returns:
        List of project paths that are currently open in KiCad
    """
    instances = detect_kicad_instances()
    paths = []
    for instance in instances:
        project_path = instance.get('project_path', '')
        if project_path:
            paths.append(project_path)
    return paths


def is_project_open(project_path: str) -> bool:
    """Check if a specific project is currently open in KiCad.
    
    Args:
        project_path: Path to the KiCad project to check
        
    Returns:
        True if the project is currently open, False otherwise
    """
    normalized_path = os.path.normpath(os.path.abspath(project_path))
    open_paths = get_open_project_paths()
    
    for open_path in open_paths:
        if os.path.normpath(os.path.abspath(open_path)) == normalized_path:
            return True
    return False
