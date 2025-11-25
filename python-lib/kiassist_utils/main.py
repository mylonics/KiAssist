"""Main KiAssist application module using pywebview."""

import os
import sys
from pathlib import Path
from typing import Optional, List, Dict, Any
import webview

from .api_key import ApiKeyStore
from .gemini import GeminiAPI
from .kicad_ipc import detect_kicad_instances, get_open_project_paths
from .recent_projects import RecentProjectsStore, validate_kicad_project_path
from .requirements_wizard import (
    get_default_questions,
    check_requirements_file,
    get_requirements_content,
    save_requirements_file,
    build_refine_prompt,
    build_synthesize_prompt,
    parse_refined_questions,
    parse_synthesized_docs,
)


class KiAssistAPI:
    """Backend API exposed to the frontend via pywebview."""
    
    def __init__(self):
        """Initialize the backend API."""
        self.api_key_store = ApiKeyStore()
        self.gemini_api: Optional[GeminiAPI] = None
        self.recent_projects_store = RecentProjectsStore()
    
    def echo_message(self, message: str) -> str:
        """Echo a message (for testing).
        
        Args:
            message: The message to echo
            
        Returns:
            The echoed message
        """
        return f"Echo: {message}"
    
    def detect_kicad_instances(self):
        """Detect available KiCad instances.
        
        Returns:
            List of KiCad instances
        """
        return detect_kicad_instances()
    
    def check_api_key(self) -> bool:
        """Check if an API key is stored.
        
        Returns:
            True if API key exists, False otherwise
        """
        has_key = self.api_key_store.has_api_key()
        print(f"[DEBUG] check_api_key result: {has_key}")
        if has_key:
            key = self.api_key_store.get_api_key()
            print(f"[DEBUG] Key exists, length: {len(key) if key else 0}")
        return has_key
    
    def get_api_key(self) -> Optional[str]:
        """Get the stored API key.
        
        Returns:
            The API key or None
        """
        return self.api_key_store.get_api_key()
    
    def set_api_key(self, api_key: str) -> dict:
        """Set/save an API key.
        
        Args:
            api_key: The API key to store
            
        Returns:
            Result dictionary with success status and optional warning
        """
        try:
            print(f"[DEBUG] set_api_key called with key length: {len(api_key) if api_key else 0}")
            success, warning = self.api_key_store.set_api_key(api_key)
            print(f"[DEBUG] set_api_key result - success: {success}, warning: {warning}")
            
            # Verify the key was saved by trying to retrieve it
            retrieved = self.api_key_store.get_api_key()
            print(f"[DEBUG] Retrieved key matches: {retrieved == api_key}")
            
            # Update the Gemini API instance
            self.gemini_api = GeminiAPI(api_key)
            result = {"success": success}
            if warning:
                result["warning"] = warning
            print(f"[DEBUG] Returning result: {result}")
            return result
        except Exception as e:
            print(f"[DEBUG] Exception in set_api_key: {e}")
            import traceback
            traceback.print_exc()
            return {"success": False, "error": str(e)}
    
    def send_message(self, message: str, model: str = "2.5-flash") -> dict:
        """Send a message to Gemini API.
        
        Args:
            message: The message to send
            model: The model to use
            
        Returns:
            Dictionary with response or error
        """
        try:
            # Get API key
            api_key = self.api_key_store.get_api_key()
            if not api_key:
                return {"success": False, "error": "API key not configured"}
            
            # Create or update Gemini API instance
            if not self.gemini_api:
                self.gemini_api = GeminiAPI(api_key)
            
            # Send message
            response = self.gemini_api.send_message(message, model)
            return {"success": True, "response": response}
            
        except Exception as e:
            return {"success": False, "error": f"Gemini API error: {str(e)}"}
    
    def get_recent_projects(self) -> List[Dict[str, Any]]:
        """Get list of recently opened projects.
        
        Returns:
            List of recent project dictionaries
        """
        return self.recent_projects_store.get_recent_projects()
    
    def add_recent_project(self, project_path: str) -> dict:
        """Add a project to the recent projects list.
        
        Args:
            project_path: Path to the KiCad project file
            
        Returns:
            Result dictionary with success status
        """
        try:
            self.recent_projects_store.add_project(project_path)
            return {"success": True}
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def remove_recent_project(self, project_path: str) -> dict:
        """Remove a project from the recent projects list.
        
        Args:
            project_path: Path to the KiCad project file
            
        Returns:
            Result dictionary with success status
        """
        try:
            self.recent_projects_store.remove_project(project_path)
            return {"success": True}
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def validate_project_path(self, path: str) -> Dict[str, Any]:
        """Validate and get info about a KiCad project path.
        
        Args:
            path: Path to validate
            
        Returns:
            Dictionary with validation result and project info
        """
        return validate_kicad_project_path(path)
    
    def browse_for_project(self) -> Dict[str, Any]:
        """Open a file dialog to browse for a KiCad project.
        
        Returns:
            Dictionary with selected path and project info, or error
        """
        try:
            # Get all windows
            windows = webview.windows
            if not windows:
                return {"success": False, "error": "No window available"}
            
            window = windows[0]
            
            # Open file dialog for .kicad_pro files
            result = window.create_file_dialog(
                webview.OPEN_DIALOG,
                allow_multiple=False,
                file_types=('KiCad Project Files (*.kicad_pro)', 'All Files (*.*)')
            )
            
            if result and len(result) > 0:
                selected_path = result[0]
                validation = validate_kicad_project_path(selected_path)
                if validation.get('valid'):
                    return {
                        "success": True,
                        "path": selected_path,
                        **validation
                    }
                else:
                    return {
                        "success": False,
                        "error": validation.get('error', 'Invalid project')
                    }
            else:
                return {"success": False, "cancelled": True}
                
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def get_open_project_paths(self) -> List[str]:
        """Get list of project paths from currently open KiCad instances.
        
        Returns:
            List of project paths that are currently open in KiCad
        """
        return get_open_project_paths()
    
    def get_projects_list(self) -> Dict[str, Any]:
        """Get combined list of open and recent projects.
        
        Returns:
            Dictionary containing open_projects and recent_projects lists
        """
        try:
            # Get open KiCad instances
            open_instances = detect_kicad_instances()
            
            # Get open project paths for comparison
            open_paths = set()
            for instance in open_instances:
                project_path = instance.get('project_path', '')
                if project_path:
                    open_paths.add(os.path.normpath(os.path.abspath(project_path)))
            
            # Get recent projects (excluding currently open ones)
            all_recent = self.recent_projects_store.get_recent_projects()
            recent_projects = []
            for project in all_recent:
                project_path = project.get('path', '')
                if project_path:
                    normalized = os.path.normpath(os.path.abspath(project_path))
                    if normalized not in open_paths:
                        recent_projects.append(project)
            
            return {
                "success": True,
                "open_projects": open_instances,
                "recent_projects": recent_projects
            }
            
        except Exception as e:
            return {"success": False, "error": str(e), "open_projects": [], "recent_projects": []}
    
    # Requirements Wizard API methods
    
    def get_wizard_questions(self) -> Dict[str, Any]:
        """Get the default wizard questions.
        
        Returns:
            Dictionary with questions list
        """
        try:
            questions = get_default_questions()
            return {
                "success": True,
                "questions": questions
            }
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def check_requirements_file(self, project_dir: str) -> Dict[str, Any]:
        """Check if requirements.md exists in project directory.
        
        Args:
            project_dir: Path to the project directory
            
        Returns:
            Dictionary with exists status and paths
        """
        return check_requirements_file(project_dir)
    
    def get_requirements_content(self, project_dir: str) -> Dict[str, Any]:
        """Read requirements.md content from project directory.
        
        Args:
            project_dir: Path to the project directory
            
        Returns:
            Dictionary with file content or error
        """
        return get_requirements_content(project_dir)
    
    def save_requirements(self, project_dir: str, requirements_content: str, 
                         todo_content: Optional[str] = None) -> Dict[str, Any]:
        """Save requirements.md and todo.md to project directory.
        
        Args:
            project_dir: Path to the project directory
            requirements_content: Content for requirements.md
            todo_content: Optional content for todo.md
            
        Returns:
            Dictionary with success status and saved paths
        """
        return save_requirements_file(project_dir, requirements_content, todo_content)
    
    def refine_wizard_questions(self, initial_answers: Dict[str, str], 
                               model: str = "2.5-flash") -> Dict[str, Any]:
        """Send initial answers to LLM to refine remaining questions.
        
        Args:
            initial_answers: Dictionary of question_id to answer text
            model: The model to use
            
        Returns:
            Dictionary with refined questions or error
        """
        try:
            # Get API key
            api_key = self.api_key_store.get_api_key()
            if not api_key:
                return {"success": False, "error": "API key not configured"}
            
            # Create or update Gemini API instance
            if not self.gemini_api:
                self.gemini_api = GeminiAPI(api_key)
            
            # Build the prompt
            prompt = build_refine_prompt(initial_answers)
            
            # Send to LLM
            response = self.gemini_api.send_message(prompt, model)
            
            # Parse the response
            refined_questions = parse_refined_questions(response)
            
            return {
                "success": True,
                "questions": refined_questions
            }
            
        except Exception as e:
            return {"success": False, "error": f"Error refining questions: {str(e)}"}
    
    def synthesize_requirements(self, questions: List[Dict[str, Any]], 
                               answers: Dict[str, str],
                               project_name: str = "PCB Project",
                               model: str = "2.5-flash") -> Dict[str, Any]:
        """Send all Q&A to LLM to synthesize requirements documents.
        
        Args:
            questions: List of question dictionaries
            answers: Dictionary of question_id to answer text
            project_name: Name of the project
            model: The model to use
            
        Returns:
            Dictionary with requirements and todo content or error
        """
        try:
            # Get API key
            api_key = self.api_key_store.get_api_key()
            if not api_key:
                return {"success": False, "error": "API key not configured"}
            
            # Create or update Gemini API instance
            if not self.gemini_api:
                self.gemini_api = GeminiAPI(api_key)
            
            # Build the prompt
            prompt = build_synthesize_prompt(questions, answers, project_name)
            
            # Send to LLM
            response = self.gemini_api.send_message(prompt, model)
            
            # Parse the response
            docs = parse_synthesized_docs(response)
            
            if docs['requirements'] or docs['todo']:
                return {
                    "success": True,
                    "requirements": docs['requirements'],
                    "todo": docs['todo']
                }
            else:
                return {
                    "success": False,
                    "error": "Failed to parse LLM response into documents"
                }
            
        except Exception as e:
            return {"success": False, "error": f"Error synthesizing requirements: {str(e)}"}


def get_frontend_path() -> Path:
    """Get the path to the frontend dist directory.
    
    Returns:
        Path to the dist directory
    """
    # If running as a PyInstaller frozen executable
    if getattr(sys, 'frozen', False):
        # PyInstaller extracts files to sys._MEIPASS
        base_path = Path(sys._MEIPASS)
        dist_path = base_path / "dist"
        print(f"[DEBUG] Running as frozen executable")
        print(f"[DEBUG] Base path (sys._MEIPASS): {base_path}")
        print(f"[DEBUG] Looking for dist at: {dist_path}")
        print(f"[DEBUG] Dist exists: {dist_path.exists()}")
        if dist_path.exists():
            print(f"[DEBUG] Contents: {list(dist_path.iterdir())}")
            return dist_path
    
    # When running from source, try to find the dist directory
    # Get the directory of this file
    current_file = Path(__file__)
    python_lib = current_file.parent.parent  # Up to python-lib
    repo_root = python_lib.parent  # Up to repository root
    
    # Check for dist in repository root
    dist_path = repo_root / "dist"
    print(f"[DEBUG] Running from source")
    print(f"[DEBUG] Looking for dist at: {dist_path}")
    print(f"[DEBUG] Dist exists: {dist_path.exists()}")
    if dist_path.exists():
        return dist_path
    
    # Fallback: create a minimal index.html if dist not found
    print(f"[DEBUG] No dist directory found!")
    return None


def create_window(api: KiAssistAPI):
    """Create and show the main application window.
    
    Args:
        api: The backend API instance
    """
    frontend_path = get_frontend_path()
    
    if frontend_path and (frontend_path / "index.html").exists():
        # Load the built frontend
        url = str(frontend_path / "index.html")
    else:
        # Create a minimal error page
        html = """
        <!DOCTYPE html>
        <html>
        <head>
            <title>KiAssist - Error</title>
        </head>
        <body>
            <h1>Frontend Not Found</h1>
            <p>The frontend distribution was not found. Please build the frontend first:</p>
            <pre>npm run build</pre>
        </body>
        </html>
        """
        url = None  # Will use html parameter instead
        
        window = webview.create_window(
            "KiAssist",
            html=html,
            js_api=api,
            width=800,
            height=600,
        )
        return
    
    # Create the main window
    window = webview.create_window(
        "KiAssist",
        url,
        js_api=api,
        width=800,
        height=600,
    )


def main():
    """Main entry point for the application."""
    print("\n" + "="*60)
    print("KiAssist - KiCAD AI Assistant")
    print("="*60)
    print("TIP: Open browser DevTools to see [UI] debug messages")
    print("     (Right-click in app > Inspect Element > Console tab)")
    print("="*60 + "\n")
    
    # Create the backend API
    api = KiAssistAPI()
    
    # Create the window
    create_window(api)
    
    # Start the webview
    webview.start(debug=True)


if __name__ == "__main__":
    main()
