"""KiAssist Utilities Package

Python-based backend for KiAssist - KiCAD AI Assistant.
Provides API key management, Gemini integration, and KiCad IPC detection.
"""

__version__ = "0.1.0"

from .api_key import ApiKeyStore
from .gemini import GeminiAPI
from .kicad_ipc import detect_kicad_instances, KiCadInstance, get_open_project_paths, is_project_open
from .recent_projects import RecentProjectsStore, validate_kicad_project_path


def __getattr__(name: str):
    """Lazy-load main module symbols to avoid importing kiassist_utils.main
    at package init time.  When the package is invoked via
    ``python -m kiassist_utils.main``, an eager import causes a
    RuntimeWarning because the module appears in sys.modules before it is
    executed as ``__main__``."""
    if name in ("KiAssistAPI", "main"):
        from . import main as _main_mod  # noqa: F811
        return getattr(_main_mod, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = [
    "ApiKeyStore",
    "GeminiAPI",
    "detect_kicad_instances",
    "KiCadInstance",
    "get_open_project_paths",
    "is_project_open",
    "RecentProjectsStore",
    "validate_kicad_project_path",
    "KiAssistAPI",
    "main",
]


# Legacy functions for backward compatibility
def process_message(message: str) -> str:
    """
    Process a message string.
    
    Args:
        message: The input message to process
        
    Returns:
        A processed version of the message
    """
    return f"Processed: {message}"


def validate_kicad_project(project_path: str) -> bool:
    """
    Validate a KiCAD project (placeholder for future functionality).
    
    Args:
        project_path: Path to the KiCAD project
        
    Returns:
        True if valid, False otherwise
    """
    # Placeholder implementation
    return project_path.endswith(('.kicad_pro', '.pro'))
