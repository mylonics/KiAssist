"""Recent projects management module."""

import json
import os
from pathlib import Path
from typing import List, Dict, Any, Optional
from datetime import datetime


def get_config_dir() -> Path:
    """Get the configuration directory for KiAssist.
    
    Returns:
        Path to the configuration directory
    """
    if os.name == 'nt':  # Windows
        base = os.environ.get('APPDATA', os.path.expanduser('~'))
        config_dir = Path(base) / 'KiAssist'
    else:  # Linux/macOS
        xdg_config = os.environ.get('XDG_CONFIG_HOME', os.path.expanduser('~/.config'))
        config_dir = Path(xdg_config) / 'kiassist'
    
    config_dir.mkdir(parents=True, exist_ok=True)
    return config_dir


class RecentProjectsStore:
    """Manages recently opened KiCad projects."""
    
    MAX_RECENT_PROJECTS = 10
    
    def __init__(self):
        """Initialize the recent projects store."""
        self._config_file = get_config_dir() / 'recent_projects.json'
        self._recent_projects: List[Dict[str, Any]] = []
        self._load()
    
    def _load(self) -> None:
        """Load recent projects from disk."""
        try:
            if self._config_file.exists():
                with open(self._config_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self._recent_projects = data.get('recent_projects', [])
        except (json.JSONDecodeError, OSError) as e:
            print(f"Warning: Could not load recent projects: {e}")
            self._recent_projects = []
    
    def _save(self) -> None:
        """Save recent projects to disk."""
        try:
            with open(self._config_file, 'w', encoding='utf-8') as f:
                json.dump({'recent_projects': self._recent_projects}, f, indent=2)
        except OSError as e:
            print(f"Warning: Could not save recent projects: {e}")
    
    def add_project(self, project_path: str) -> None:
        """Add or update a project in the recent list.
        
        Args:
            project_path: Path to the KiCad project file
        """
        # Normalize the path
        project_path = os.path.normpath(os.path.abspath(project_path))
        
        # Remove if already exists (to move it to top)
        self._recent_projects = [
            p for p in self._recent_projects 
            if p.get('path') != project_path
        ]
        
        # Add to the beginning
        self._recent_projects.insert(0, {
            'path': project_path,
            'name': Path(project_path).stem,
            'last_opened': datetime.now().isoformat()
        })
        
        # Limit to max size
        self._recent_projects = self._recent_projects[:self.MAX_RECENT_PROJECTS]
        
        self._save()
    
    def remove_project(self, project_path: str) -> None:
        """Remove a project from the recent list.
        
        Args:
            project_path: Path to the KiCad project file
        """
        project_path = os.path.normpath(os.path.abspath(project_path))
        self._recent_projects = [
            p for p in self._recent_projects 
            if p.get('path') != project_path
        ]
        self._save()
    
    def get_recent_projects(self) -> List[Dict[str, Any]]:
        """Get list of recent projects.
        
        Returns:
            List of recent project dictionaries with path, name, and last_opened
        """
        # Filter out projects that no longer exist
        valid_projects = []
        for p in self._recent_projects:
            path = p.get('path', '')
            if path and Path(path).exists():
                valid_projects.append(p)
        
        # Update stored list if any were removed
        if len(valid_projects) != len(self._recent_projects):
            self._recent_projects = valid_projects
            self._save()
        
        return self._recent_projects.copy()
    
    def clear(self) -> None:
        """Clear all recent projects."""
        self._recent_projects = []
        self._save()


def validate_kicad_project_path(path: str) -> Dict[str, Any]:
    """Validate and get info about a KiCad project path.
    
    Args:
        path: Path to validate
        
    Returns:
        Dictionary with validation result and project info
    """
    try:
        path = os.path.normpath(os.path.abspath(path))
        p = Path(path)
        
        if not p.exists():
            return {'valid': False, 'error': 'Path does not exist'}
        
        # Check if it's a .kicad_pro file
        if p.suffix == '.kicad_pro':
            project_dir = p.parent
            project_name = p.stem
            return {
                'valid': True,
                'project_path': str(p),
                'project_dir': str(project_dir),
                'project_name': project_name,
                'pcb_path': find_file_in_dir(project_dir, '.kicad_pcb'),
                'schematic_path': find_file_in_dir(project_dir, '.kicad_sch')
            }
        
        # Check if it's a directory containing a .kicad_pro file
        if p.is_dir():
            kicad_pro_files = list(p.glob('*.kicad_pro'))
            if kicad_pro_files:
                pro_file = kicad_pro_files[0]
                project_name = pro_file.stem
                return {
                    'valid': True,
                    'project_path': str(pro_file),
                    'project_dir': str(p),
                    'project_name': project_name,
                    'pcb_path': find_file_in_dir(p, '.kicad_pcb'),
                    'schematic_path': find_file_in_dir(p, '.kicad_sch')
                }
            return {'valid': False, 'error': 'No KiCad project file found in directory'}
        
        return {'valid': False, 'error': 'Not a valid KiCad project file or directory'}
        
    except Exception as e:
        return {'valid': False, 'error': str(e)}


def find_file_in_dir(directory: Path, extension: str) -> Optional[str]:
    """Find a file with a specific extension in a directory.
    
    Args:
        directory: Directory to search in
        extension: File extension to look for (e.g., '.kicad_pcb')
        
    Returns:
        Path to the file if found, None otherwise
    """
    try:
        files = list(directory.glob(f'*{extension}'))
        if files:
            return str(files[0])
    except Exception:
        pass
    return None
