"""Main KiAssist application module using pywebview."""

import os
import sys
from pathlib import Path
from typing import Optional
import webview

from .api_key import ApiKeyStore
from .gemini import GeminiAPI
from .kicad_ipc import detect_kicad_instances


class KiAssistAPI:
    """Backend API exposed to the frontend via pywebview."""
    
    def __init__(self):
        """Initialize the backend API."""
        self.api_key_store = ApiKeyStore()
        self.gemini_api: Optional[GeminiAPI] = None
    
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
        return self.api_key_store.has_api_key()
    
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
            Result dictionary with success status
        """
        try:
            self.api_key_store.set_api_key(api_key)
            # Update the Gemini API instance
            self.gemini_api = GeminiAPI(api_key)
            return {"success": True}
        except Exception as e:
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
    # Create the backend API
    api = KiAssistAPI()
    
    # Create the window
    create_window(api)
    
    # Start the webview
    webview.start(debug=True)


if __name__ == "__main__":
    main()
