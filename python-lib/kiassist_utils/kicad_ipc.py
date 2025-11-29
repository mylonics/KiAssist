"""KiCad IPC instance detection module."""

import os
import platform
from pathlib import Path
from tempfile import gettempdir
from typing import List, Dict, Any, Optional


# Cache the current OS platform
_CURRENT_PLATFORM = platform.system()


class KiCadInstance:
    """Represents a detected KiCad instance."""
    
    def __init__(self, socket_path: str, project_name: str = "Unknown Project", 
                 display_name: str = "", version: str = "", project_path: str = "",
                 pcb_path: str = "", schematic_path: str = ""):
        """Initialize KiCad instance.
        
        Args:
            socket_path: Path to the IPC socket
            project_name: Name of the project
            display_name: Display name for UI
            version: KiCad version string
            project_path: Path to the project file
            pcb_path: Path to the PCB file
            schematic_path: Path to the schematic file
        """
        self.socket_path = socket_path
        self.project_name = project_name
        self.display_name = display_name or project_name
        self.version = version
        self.project_path = project_path
        self.pcb_path = pcb_path
        self.schematic_path = schematic_path
    
    def to_dict(self) -> Dict[str, str]:
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
        # On Windows, we need to enumerate named pipes
        # The pipe names are the full paths: C:\Users\...\Temp\kicad\api.sock
        try:
            import subprocess
            # Use PowerShell to enumerate pipes matching our pattern
            temp_dir = str(socket_dir).replace('/', '\\')
            
            # PowerShell command to find pipes matching the kicad socket pattern
            cmd = f'Get-ChildItem \\\\.\\pipe\\ | Where-Object {{ $_.Name -like "{temp_dir}\\api*.sock" }} | Select-Object -ExpandProperty Name'
            
            # Use CREATE_NO_WINDOW to prevent command prompt from appearing
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = subprocess.SW_HIDE
            
            result = subprocess.run(
                ['powershell', '-Command', cmd],
                capture_output=True,
                text=True,
                timeout=5,
                startupinfo=startupinfo,
                creationflags=subprocess.CREATE_NO_WINDOW
            )
            
            if result.returncode == 0 and result.stdout.strip():
                # Parse the output to get pipe names
                pipe_names = result.stdout.strip().split('\n')
                for pipe_name in pipe_names:
                    pipe_name = pipe_name.strip()
                    if pipe_name:
                        # Extract just the filename part after the last backslash
                        filename = pipe_name.split('\\')[-1]
                        # Validate the pattern: api.sock or api-<PID>.sock
                        if filename == "api.sock" or (
                            filename.startswith("api-") and 
                            filename.endswith(".sock") and
                            filename[4:-5].isdigit()
                        ):
                            # Return the full pipe path (this is what gets passed to ipc://)
                            sockets.append(Path(pipe_name))
        except Exception as e:
            print(f"Warning: Could not enumerate Windows named pipes: {e}")
        
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
        print(f"Warning: Could not scan socket directory: {e}")
    
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
            print("Warning: kicad-python package not available. KiCad detection disabled.")
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
        
        try:
            # Get PCB documents
            pcb_docs = kicad.get_open_documents(base_types_pb2.DocumentType.DOCTYPE_PCB)
            if pcb_docs and len(pcb_docs) > 0:
                doc = pcb_docs[0]
                project_path = doc.project.path
                project_name = Path(project_path).stem
                # Try to get the PCB file path from the document
                try:
                    # Try different attributes that might contain the path
                    if hasattr(doc, 'path'):
                        pcb_path = doc.path
                    elif hasattr(doc, 'file_path'):
                        pcb_path = doc.file_path
                    elif hasattr(doc, 'filename'):
                        pcb_path = doc.filename
                except Exception as e:
                    print(f"Could not get PCB path from document: {e}")
        except Exception as e:
            print(f"Could not get PCB documents: {e}")
        
        try:
            # Get schematic documents
            sch_docs = kicad.get_open_documents(base_types_pb2.DocumentType.DOCTYPE_SCHEMATIC)
            if sch_docs and len(sch_docs) > 0:
                doc = sch_docs[0]
                # Use project path from schematic if not set yet
                if not project_path:
                    project_path = doc.project.path
                    project_name = Path(project_path).stem
                # Try to get the schematic file path from the document
                try:
                    if hasattr(doc, 'path'):
                        schematic_path = doc.path
                    elif hasattr(doc, 'file_path'):
                        schematic_path = doc.file_path
                    elif hasattr(doc, 'filename'):
                        schematic_path = doc.filename
                except Exception as e:
                    print(f"Could not get schematic path from document: {e}")
        except Exception as e:
            print(f"Could not get schematic documents: {e}")
        
        # If we have a project path but no file paths, try to find them in the project directory
        if project_path and (not pcb_path or not schematic_path):
            try:
                project_dir = Path(project_path)
                if project_dir.is_dir():
                    # Look for .kicad_pcb file
                    if not pcb_path:
                        pcb_files = list(project_dir.glob("*.kicad_pcb"))
                        if pcb_files:
                            pcb_path = str(pcb_files[0])
                    
                    # Look for .kicad_sch file (root schematic)
                    if not schematic_path:
                        sch_files = list(project_dir.glob("*.kicad_sch"))
                        # Try to find the root schematic (usually same name as project)
                        root_sch = project_dir / f"{project_name}.kicad_sch"
                        if root_sch.exists():
                            schematic_path = str(root_sch)
                        elif sch_files:
                            schematic_path = str(sch_files[0])
            except Exception as e:
                print(f"Could not search project directory: {e}")
        
        display_name = project_name if project_name != "No Project Open" else "KiCad (No Project)"
        
        return KiCadInstance(
            socket_path=socket_path,
            project_name=project_name,
            display_name=display_name,
            version=version_str,
            project_path=project_path,
            pcb_path=pcb_path,
            schematic_path=schematic_path
        )
    except Exception as e:
        print(f"Warning: Could not probe KiCad instance at {socket_path}: {e}")
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
