"""Main KiAssist application module using pywebview."""

import asyncio
import logging
import os
import sys
import threading
from pathlib import Path
from typing import Optional, List, Dict, Any
import webview

from .api_key import ApiKeyStore
from .ai.base import AIProvider, AIMessage
from .ai.gemini import GeminiProvider
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
from .kicad_schematic import inject_test_note, is_schematic_api_available
from .context.history import ConversationStore

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Provider metadata registry
# ---------------------------------------------------------------------------

_PROVIDER_REGISTRY: List[Dict[str, Any]] = [
    {
        "id": "gemini",
        "name": "Google Gemini",
        "models": [
            {"id": "3.1-pro", "name": "Gemini 3.1 Pro"},
            {"id": "3-flash", "name": "Gemini 3 Flash"},
            {"id": "3.1-flash-lite", "name": "Gemini 3.1 Flash Lite"},
        ],
        "default_model": "3-flash",
        "key_url": "https://aistudio.google.com/apikey",
        "key_prefix": "AIza",
        "key_min_length": 30,
    },
    {
        "id": "claude",
        "name": "Anthropic Claude",
        "models": [
            {"id": "sonnet", "name": "Claude Sonnet"},
            {"id": "haiku", "name": "Claude Haiku"},
            {"id": "opus", "name": "Claude Opus"},
        ],
        "default_model": "sonnet",
        "key_url": "https://console.anthropic.com/",
        "key_prefix": "sk-ant-",
        "key_min_length": 30,
    },
    {
        "id": "openai",
        "name": "OpenAI",
        "models": [
            {"id": "gpt-4o", "name": "GPT-4o"},
            {"id": "gpt-4o-mini", "name": "GPT-4o Mini"},
            {"id": "o3", "name": "o3"},
        ],
        "default_model": "gpt-4o",
        "key_url": "https://platform.openai.com/api-keys",
        "key_prefix": "sk-",
        "key_min_length": 20,
    },
]


class KiAssistAPI:
    """Backend API exposed to the frontend via pywebview."""

    def __init__(self):
        """Initialize the backend API."""
        self.api_key_store = ApiKeyStore()
        self.current_provider: Optional[AIProvider] = None
        self.current_provider_name: str = "gemini"
        self.current_model: str = "3-flash"
        self.recent_projects_store = RecentProjectsStore()
        self.current_session_id: Optional[str] = None
        self._current_project_path: Optional[str] = None
        # Streaming state
        self._stream_lock = threading.Lock()
        self._stream_buffer = ""
        self._stream_done = True
        self._stream_error = None
    
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

    # ------------------------------------------------------------------
    # Provider management
    # ------------------------------------------------------------------

    def get_providers(self) -> Dict[str, Any]:
        """Return available AI providers, their models, and configuration status.

        Returns:
            Dictionary with providers list, current provider and model.
        """
        providers = []
        for info in _PROVIDER_REGISTRY:
            entry = dict(info)
            entry["has_key"] = self.api_key_store.has_api_key(info["id"])
            providers.append(entry)
        return {
            "success": True,
            "providers": providers,
            "current_provider": self.current_provider_name,
            "current_model": self.current_model,
        }

    def set_provider(self, provider: str, model: str) -> dict:
        """Set the active AI provider and model.

        Args:
            provider: Provider ID (``gemini``, ``claude``, or ``openai``).
            model: Model shortcut string.

        Returns:
            Result dictionary with success status and optional warning.
        """
        valid_ids = {p["id"] for p in _PROVIDER_REGISTRY}
        if provider not in valid_ids:
            return {"success": False, "error": f"Unknown provider: {provider}"}

        self.current_provider_name = provider
        self.current_model = model
        self.current_provider = None  # force re-creation

        try:
            new_provider = self._create_provider(provider, model)
            if new_provider:
                self.current_provider = new_provider
                return {"success": True}
            else:
                return {
                    "success": True,
                    "warning": f"No API key configured for {provider}. Please add one via Settings.",
                }
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    def _create_provider(self, provider_name: str, model: str) -> Optional[AIProvider]:
        """Instantiate and return an :class:`AIProvider` for *provider_name*.

        Returns ``None`` when no API key is available for the provider.
        """
        api_key = self.api_key_store.get_api_key(provider_name)
        if not api_key:
            return None

        if provider_name == "gemini":
            return GeminiProvider(api_key, model)

        if provider_name == "claude":
            from .ai.claude import ClaudeProvider  # optional dep
            return ClaudeProvider(api_key, model)

        if provider_name == "openai":
            from .ai.openai import OpenAIProvider  # optional dep
            return OpenAIProvider(api_key, model)

        return None

    def _get_or_create_provider(
        self, model: Optional[str] = None
    ) -> Optional[AIProvider]:
        """Return the current provider, creating or updating it as needed.

        When *model* differs from ``current_model`` a temporary provider for
        that model is returned without persisting the change.
        """
        effective_model = model if model is not None else self.current_model
        # Reuse cached provider when nothing changed
        if self.current_provider and effective_model == self.current_model:
            return self.current_provider
        provider = self._create_provider(self.current_provider_name, effective_model)
        if provider and model is None:
            self.current_provider = provider
            self.current_model = effective_model
        return provider

    def _send_to_ai(self, prompt: str, model: Optional[str] = None) -> str:
        """Send a single-turn prompt to the current AI provider.

        Args:
            prompt: Text to send.
            model: Optional model override.

        Returns:
            The text response from the AI.

        Raises:
            RuntimeError: When no provider is configured.
        """
        provider = self._get_or_create_provider(model)
        if not provider:
            raise RuntimeError(
                "No AI provider configured. Please add an API key via Settings."
            )
        msgs = [AIMessage(role="user", content=prompt)]
        response = provider.chat(msgs)
        return response.content

    # ------------------------------------------------------------------
    # API key management
    # ------------------------------------------------------------------

    def check_api_key(self, provider: Optional[str] = None) -> bool:
        """Check if an API key is stored for *provider* (default: current provider).

        Args:
            provider: Provider ID to check, or ``None`` to use the active provider.

        Returns:
            ``True`` if an API key exists, ``False`` otherwise.
        """
        target = provider or self.current_provider_name
        has_key = self.api_key_store.has_api_key(target)
        logger.debug("check_api_key(%r) -> %s", target, has_key)
        return has_key

    def get_api_key(self, provider: Optional[str] = None) -> Optional[str]:
        """Get the stored API key for *provider* (default: current provider).

        Args:
            provider: Provider ID, or ``None`` to use the active provider.

        Returns:
            The API key or ``None``.
        """
        target = provider or self.current_provider_name
        return self.api_key_store.get_api_key(target)

    def set_api_key(self, api_key: str, provider: Optional[str] = None) -> dict:
        """Store *api_key* for *provider* (default: current provider).

        Also creates/refreshes the active provider instance when the stored
        key belongs to the currently selected provider.

        Args:
            api_key: The API key to store.
            provider: Provider ID, or ``None`` to use the active provider.

        Returns:
            Result dictionary with ``success`` status and optional ``warning``.
        """
        target = provider or self.current_provider_name
        try:
            logger.debug(
                "set_api_key(provider=%r, key_len=%d)",
                target,
                len(api_key) if api_key else 0,
            )
            success, warning = self.api_key_store.set_api_key(api_key, target)
            logger.debug("set_api_key result: success=%s, warning=%r", success, warning)

            # Refresh active provider if this key belongs to the current provider
            if target == self.current_provider_name:
                self.current_provider = None  # force re-creation on next use
                new_provider = self._create_provider(target, self.current_model)
                if new_provider:
                    self.current_provider = new_provider

            result: Dict[str, Any] = {"success": success}
            if warning:
                result["warning"] = warning
            return result
        except Exception as exc:
            logger.error("set_api_key failed: %s", exc, exc_info=True)
            return {"success": False, "error": str(exc)}
    
    # ------------------------------------------------------------------
    # Chat / messaging
    # ------------------------------------------------------------------

    def send_message(self, message: str, model: Optional[str] = None) -> dict:
        """Send a message to the active AI provider and return a response.

        The user prompt and assistant response are persisted to the active
        ConversationStore so they appear in the sessions list.

        Args:
            message: The message to send.
            model: Optional model override (uses current model when omitted).

        Returns:
            Dictionary with ``response`` text or ``error``.
        """
        try:
            store = self._get_session_store()
            if not self.current_session_id:
                self.current_session_id = store.new_session()

            user_msg = AIMessage(role="user", content=message)
            store.append(self.current_session_id, user_msg)

            response_text = self._send_to_ai(message, model)

            assistant_msg = AIMessage(role="assistant", content=response_text)
            store.append(self.current_session_id, assistant_msg)

            return {"success": True, "response": response_text}
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    def start_stream_message(self, message: str, model: Optional[str] = None) -> dict:
        """Start streaming a response from the active AI provider in a background thread.

        The user prompt is persisted immediately; the final assembled assistant
        response is persisted in the background thread once streaming completes.

        Args:
            message: The message to send.
            model: Optional model override.

        Returns:
            ``{"success": True}`` on successful start, or an error dict.
        """
        try:
            provider = self._get_or_create_provider(model)
            if not provider:
                return {
                    "success": False,
                    "error": "No AI provider configured. Please add an API key via Settings.",
                }

            # Persist the user message immediately
            store = self._get_session_store()
            if not self.current_session_id:
                self.current_session_id = store.new_session()
            session_id = self.current_session_id
            store.append(session_id, AIMessage(role="user", content=message))

            with self._stream_lock:
                self._stream_buffer = ""
                self._stream_done = False
                self._stream_error = None

            msgs = [AIMessage(role="user", content=message)]

            def _run_stream():
                async def _async_stream():
                    try:
                        async for chunk in provider.chat_stream(msgs):
                            if chunk.text:
                                with self._stream_lock:
                                    self._stream_buffer += chunk.text
                    except Exception as exc:
                        with self._stream_lock:
                            self._stream_error = str(exc)
                    finally:
                        with self._stream_lock:
                            self._stream_done = True
                            final_text = self._stream_buffer

                        # Persist the assembled assistant response
                        if final_text:
                            try:
                                store.append(
                                    session_id,
                                    AIMessage(role="assistant", content=final_text),
                                )
                            except Exception as persist_exc:
                                logger.warning(
                                    "Failed to persist assistant response: %s",
                                    persist_exc,
                                )

                asyncio.run(_async_stream())

            thread = threading.Thread(target=_run_stream, daemon=True)
            thread.start()
            return {"success": True}

        except Exception as exc:
            return {"success": False, "error": f"Stream error: {exc}"}

    def poll_stream(self) -> dict:
        """Poll for new streaming content."""
        with self._stream_lock:
            return {
                "success": True,
                "text": self._stream_buffer,
                "done": self._stream_done,
                "error": self._stream_error,
            }
    
    # ------------------------------------------------------------------
    # Project management
    # ------------------------------------------------------------------

    def set_project_path(self, path: str) -> dict:
        """Set the current active project path.

        Args:
            path: Path to the ``.kicad_pro`` project file or project directory.

        Returns:
            Result dictionary with success status.
        """
        try:
            p = Path(path)
            if not p.exists():
                return {"success": False, "error": f"Path does not exist: {path}"}
            self._current_project_path = str(p)
            return {"success": True}
        except Exception as exc:
            return {"success": False, "error": str(exc)}

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
            
            # Get open project paths for comparison, and auto-add them to recent projects
            open_paths = set()
            for instance in open_instances:
                project_path = instance.get('project_path', '')
                if project_path:
                    open_paths.add(os.path.normpath(os.path.abspath(project_path)))
                    # Automatically track every opened project in recent projects
                    self.recent_projects_store.add_project(project_path)
            
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
                               model: Optional[str] = None) -> Dict[str, Any]:
        """Send initial answers to LLM to refine remaining questions.

        Args:
            initial_answers: Dictionary of question_id to answer text.
            model: Optional model override.

        Returns:
            Dictionary with refined questions or error.
        """
        try:
            prompt = build_refine_prompt(initial_answers)
            response = self._send_to_ai(prompt, model)
            refined_questions = parse_refined_questions(response)
            return {"success": True, "questions": refined_questions}
        except Exception as exc:
            return {"success": False, "error": f"Error refining questions: {exc}"}

    def synthesize_requirements(self, questions: List[Dict[str, Any]],
                               answers: Dict[str, str],
                               project_name: str = "PCB Project",
                               model: Optional[str] = None) -> Dict[str, Any]:
        """Send all Q&A to LLM to synthesize requirements documents.

        Args:
            questions: List of question dictionaries.
            answers: Dictionary of question_id to answer text.
            project_name: Name of the project.
            model: Optional model override.

        Returns:
            Dictionary with requirements and todo content or error.
        """
        try:
            prompt = build_synthesize_prompt(questions, answers, project_name)
            response = self._send_to_ai(prompt, model)
            docs = parse_synthesized_docs(response)
            if docs['requirements'] or docs['todo']:
                return {
                    "success": True,
                    "requirements": docs['requirements'],
                    "todo": docs['todo'],
                }
            return {
                "success": False,
                "error": "Failed to parse LLM response into documents",
            }
        except Exception as exc:
            return {"success": False, "error": f"Error synthesizing requirements: {exc}"}
    
    # Schematic API methods

    def inject_schematic_test_note(self, project_path: str) -> Dict[str, Any]:
        """Inject a test note into the schematic for a KiCad project.
        
        If a schematic exists, it will be loaded, modified, and saved.
        If no schematic exists, a new one will be created with the project name.
        
        Args:
            project_path: Path to the .kicad_pro project file
            
        Returns:
            Dictionary with success status and details
        """
        if not project_path:
            return {
                "success": False,
                "error": "No project is open. Please open a KiCad project first."
            }
        
        return inject_test_note(project_path)
    
    def is_schematic_api_available(self) -> bool:
        """Check if the KiCad schematic API is available.

        Returns:
            True if the schematic API is available, False otherwise.
        """
        return is_schematic_api_available()

    # ------------------------------------------------------------------
    # Session management
    # ------------------------------------------------------------------

    def _get_session_store(self, project_path: Optional[str] = None) -> ConversationStore:
        """Return a :class:`ConversationStore` for *project_path*.

        Falls back to ``self._current_project_path`` and then the user's home
        directory when no explicit path is provided.
        """
        path = project_path or self._current_project_path or str(Path.home())
        return ConversationStore(path)

    def get_sessions(self, project_path: Optional[str] = None) -> Dict[str, Any]:
        """Return a list of conversation sessions.

        Args:
            project_path: Path to the project directory or ``.kicad_pro`` file.
                          Defaults to the current project or home directory.

        Returns:
            Dictionary with ``sessions`` list (each session has ``session_id``,
            ``started_at``, ``last_at``, ``message_count``).
        """
        try:
            store = self._get_session_store(project_path)
            sessions = store.list_sessions()
            return {"success": True, "sessions": sessions}
        except Exception as exc:
            return {"success": False, "error": str(exc), "sessions": []}

    def resume_session(
        self, session_id: str, project_path: Optional[str] = None
    ) -> Dict[str, Any]:
        """Load the messages for an existing session.

        Args:
            session_id: ID of the session to resume.
            project_path: Optional project path override.

        Returns:
            Dictionary with ``messages`` list and ``session_id``.
        """
        try:
            store = self._get_session_store(project_path)
            messages = store.load_session(session_id)
            serialized = [
                {"role": m.role, "content": m.content}
                for m in messages
            ]
            self.current_session_id = session_id
            return {"success": True, "session_id": session_id, "messages": serialized}
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    def export_session(
        self, session_id: str, project_path: Optional[str] = None
    ) -> Dict[str, Any]:
        """Export a conversation session as plain text.

        Args:
            session_id: ID of the session to export.
            project_path: Optional project path override.

        Returns:
            Dictionary with ``content`` string.
        """
        try:
            store = self._get_session_store(project_path)
            messages = store.load_session(session_id)
            lines = []
            for m in messages:
                if m.role == "user":
                    lines.append(f"User: {m.content}\n")
                elif m.role == "assistant":
                    lines.append(f"Assistant: {m.content}\n")
            return {"success": True, "content": "\n".join(lines)}
        except Exception as exc:
            return {"success": False, "error": str(exc)}


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


def create_window(api: KiAssistAPI, dev_mode: bool = False):
    """Create and show the main application window.
    
    Args:
        api: The backend API instance
        dev_mode: If True, connect to the Vite dev server for live reloading
    """
    if dev_mode:
        # Connect to Vite dev server for hot module replacement
        url = "http://localhost:1420"
        print(f"[DEBUG] Dev mode: loading from Vite dev server at {url}")
        window = webview.create_window(
            "KiAssist (Dev)",
            url,
            js_api=api,
            width=1100,
            height=750,
            min_size=(800, 500),
        )
        return

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
            width=1100,
            height=750,
            min_size=(800, 500),
        )
        return
    
    # Create the main window
    window = webview.create_window(
        "KiAssist",
        url,
        js_api=api,
        width=1100,
        height=750,
        min_size=(800, 500),
    )


def main():
    """Main entry point for the application."""
    import argparse
    parser = argparse.ArgumentParser(description="KiAssist - KiCAD AI Assistant")
    parser.add_argument("--dev", action="store_true",
                        help="Connect to Vite dev server (http://localhost:1420) for live reloading")
    args = parser.parse_args()

    print("\n" + "="*60)
    print("KiAssist - KiCAD AI Assistant")
    if args.dev:
        print("  ** DEV MODE — connect to Vite dev server for HMR **")
    print("="*60)
    print("TIP: Open browser DevTools to see [UI] debug messages")
    print("     (Right-click in app > Inspect Element > Console tab)")
    print("="*60 + "\n")
    
    # Create the backend API
    api = KiAssistAPI()
    
    # Create the window
    create_window(api, dev_mode=args.dev)
    
    # Start the webview
    webview.start(debug=True)


if __name__ == "__main__":
    main()
