"""KiCad schematic manipulation module using kicad_parser."""

import uuid
from pathlib import Path
from typing import Dict, Any, Optional

from .kicad_parser.schematic import Schematic
from .kicad_parser.sexpr import QStr


# Default position for test note (top right area of A4 schematic in mm)
# KiCAD uses a coordinate system where the origin (0,0) is at the top-left corner
# X increases to the right, Y increases downward
# A4 paper is 297mm x 210mm, so we place it near the top-right
DEFAULT_NOTE_POSITION = (250.0, 20.0)

# Default text size in mm (standard KiCAD text size)
DEFAULT_TEXT_SIZE = 2.54


def is_schematic_api_available() -> bool:
    """Check if the KiCad schematic API is available.

    Returns:
        True — the built-in kicad_parser module is always available.
    """
    return True


def get_schematic_path_for_project(project_path: str) -> Optional[str]:
    """Get the schematic file path for a given project.
    
    If a schematic file exists with the same name as the project, returns that path.
    Otherwise returns the path where a new schematic would be created.
    
    Args:
        project_path: Path to the .kicad_pro project file
        
    Returns:
        Path to the schematic file (existing or to be created), or None if project path is invalid
    """
    if not project_path:
        return None
        
    project_path = Path(project_path)
    
    # Handle both project file and directory
    if project_path.is_file():
        project_dir = project_path.parent
        project_name = project_path.stem
    else:
        project_dir = project_path
        project_name = project_path.name
    
    if not project_dir.exists():
        return None
        
    # Look for existing schematic file with project name
    schematic_path = project_dir / f"{project_name}.kicad_sch"
    
    return str(schematic_path)


def find_existing_schematic(project_path: str) -> Optional[str]:
    """Find an existing schematic file in the project directory.
    
    Args:
        project_path: Path to the .kicad_pro project file
        
    Returns:
        Path to existing schematic file, or None if not found
    """
    if not project_path:
        return None
        
    project_path = Path(project_path)
    
    # Handle both project file and directory
    if project_path.is_file():
        project_dir = project_path.parent
        project_name = project_path.stem
    else:
        project_dir = project_path
        project_name = project_path.name
    
    if not project_dir.exists():
        return None
    
    # First, try the schematic with the same name as the project
    main_schematic = project_dir / f"{project_name}.kicad_sch"
    if main_schematic.exists():
        return str(main_schematic)
    
    # Otherwise, look for any .kicad_sch file
    sch_files = list(project_dir.glob("*.kicad_sch"))
    if sch_files:
        return str(sch_files[0])
    
    return None


def _make_text_node(text: str, x: float, y: float, size: float) -> list:
    """Build a KiCad S-expression list for a schematic text item.

    Args:
        text: The annotation text.
        x: X coordinate in mm.
        y: Y coordinate in mm.
        size: Font size in mm.

    Returns:
        A nested list suitable for appending to ``Schematic._extra``.
    """
    return [
        "text",
        QStr(text),
        ["at", x, y, 0],
        ["effects", ["font", ["size", size, size]]],
        ["uuid", QStr(str(uuid.uuid4()))],
    ]


def inject_test_note(project_path: str, note_text: str = "KiAssist Test Note") -> Dict[str, Any]:
    """Inject a test note into the schematic for a KiCad project.
    
    If a schematic exists, it will be loaded, modified, and saved.
    If no schematic exists, a new one will be created with the project name.
    
    Args:
        project_path: Path to the .kicad_pro project file
        note_text: Text to add as a note (default: "KiAssist Test Note")
        
    Returns:
        Dictionary with:
            - success: bool - True if operation succeeded
            - message: str - Description of what was done
            - schematic_path: str - Path to the modified/created schematic
            - error: str - Error message if operation failed
    """
    if not project_path:
        return {
            "success": False,
            "error": "No project path provided. Please open a KiCad project first."
        }
    
    project_path = Path(project_path)
    
    # Handle both project file and directory
    if project_path.is_file():
        project_dir = project_path.parent
        project_name = project_path.stem
    else:
        project_dir = project_path
        project_name = project_path.name
    
    if not project_dir.exists():
        return {
            "success": False,
            "error": f"Project directory does not exist: {project_dir}"
        }
    
    schematic_path = project_dir / f"{project_name}.kicad_sch"
    created_new = False
    
    try:
        existing_schematic = find_existing_schematic(str(project_path))

        if existing_schematic:
            schematic = Schematic.load(existing_schematic)
            schematic_path = Path(existing_schematic)
        else:
            schematic = Schematic()
            schematic.generator = "KiAssist"
            schematic.version = 20231120
            created_new = True

        # Add the text note as a raw S-expression entry
        x, y = DEFAULT_NOTE_POSITION
        schematic._extra.append(_make_text_node(note_text, x, y, DEFAULT_TEXT_SIZE))

        schematic.save(str(schematic_path))
        
        action = "Created new schematic and added" if created_new else "Added"
        return {
            "success": True,
            "message": f"{action} test note '{note_text}' to schematic",
            "schematic_path": str(schematic_path),
            "created_new": created_new
        }
        
    except Exception as e:
        return {
            "success": False,
            "error": f"Failed to modify schematic: {str(e)}"
        }
