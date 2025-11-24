"""KiCad IPC instance detection module."""

import os
import platform
from pathlib import Path
from typing import List, Dict, Any, Optional


class KiCadInstance:
    """Represents a detected KiCad instance."""
    
    def __init__(self, socket_path: str, project_name: str = "Unknown Project", 
                 display_name: str = "", version: str = ""):
        """Initialize KiCad instance.
        
        Args:
            socket_path: Path to the IPC socket
            project_name: Name of the project
            display_name: Display name for UI
            version: KiCad version string
        """
        self.socket_path = socket_path
        self.project_name = project_name
        self.display_name = display_name or f"KiCad {version}"
        self.version = version
    
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
        }


def get_ipc_socket_dir() -> Path:
    """Get the base directory where KiCad IPC sockets are stored.
    
    Returns:
        Path to the socket directory
    """
    system = platform.system()
    
    if system == "Windows":
        temp = os.environ.get("TEMP", "C:\\Temp")
        return Path(temp) / "kicad"
    else:
        return Path("/tmp/kicad")


def discover_socket_files() -> List[Path]:
    """Discover all KiCad IPC socket files.
    
    Returns:
        List of paths to socket files
    """
    socket_dir = get_ipc_socket_dir()
    sockets = []
    
    if not socket_dir.exists():
        return sockets
    
    # Look for files matching api*.sock pattern
    try:
        for entry in socket_dir.iterdir():
            if entry.is_file():
                filename = entry.name
                if filename.startswith("api") and filename.endswith(".sock"):
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
        # Try to import kicad API - this is optional
        try:
            from kicad import KiCad, KiCadConnectionConfig, DocumentType
        except ImportError:
            print("Warning: kicad Python package not available. KiCad detection disabled.")
            return None
        
        config = KiCadConnectionConfig(
            socket_path=socket_path,
            client_name="kiassist-probe"
        )
        
        kicad = KiCad(config)
        
        # Get version
        try:
            version = kicad.get_version()
            version_str = str(version)
        except Exception:
            version_str = "Unknown"
        
        # Try to get open documents to determine project name
        project_name = "No Project Open"
        try:
            docs = kicad.get_open_documents(DocumentType.DOCTYPE_PCB)
            if docs and len(docs) > 0:
                doc = docs[0]
                project_path = doc.project.path
                project_name = Path(project_path).stem
        except Exception:
            pass
        
        display_name = f"{project_name} (KiCad {version_str})" if project_name != "No Project Open" else f"KiCad {version_str}"
        
        return KiCadInstance(
            socket_path=socket_path,
            project_name=project_name,
            display_name=display_name,
            version=version_str
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
