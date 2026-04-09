"""Main KiAssist application module using pywebview."""

import asyncio
import base64
import json
import logging
import os
import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Optional, List, Dict, Any
import webview

from .api_key import ApiKeyStore
from .ai.base import AIProvider, AIMessage
from .local_llm import LocalModelManager
from .kicad_ipc import detect_kicad_instances, get_open_project_paths
from .recent_projects import RecentProjectsStore, validate_kicad_project_path, find_file_in_dir
from .requirements_wizard import (
    get_default_questions,
    check_requirements_file,
    get_requirements_content,
    save_requirements_file,
    build_refine_prompt,
    build_synthesize_prompt,
    parse_refined_questions,
    parse_synthesized_docs,
    INITIAL_QUESTIONS_COUNT,
)
from .kicad_schematic import inject_test_note, is_schematic_api_available
from .context.history import ConversationStore
from .context.prompts import SystemPromptBuilder
from .context.requirements import RequirementsManager, ContextState
from .ai.llm_logger import llm_logger

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
    {
        "id": "local",
        "name": "Local Model",
        "models": [
            {"id": "llama3.2", "name": "Llama 3.2"},
            {"id": "llama3.1", "name": "Llama 3.1"},
            {"id": "mistral", "name": "Mistral"},
            {"id": "codellama", "name": "Code Llama"},
            {"id": "deepseek-r1", "name": "DeepSeek R1"},
            {"id": "qwen2.5-coder", "name": "Qwen 2.5 Coder"},
            {"id": "phi4", "name": "Phi-4"},
        ],
        "default_model": "llama3.2",
        # Local models require no remote key URL – the server runs locally.
        "key_url": "",
        "key_prefix": "",
        "key_min_length": 0,
    },
    {
        "id": "gemma4",
        "name": "Gemma 4 (Local)",
        "models": [
            {"id": "gemma4-e2b-q4_k_m", "name": "Gemma 4 E2B (Q4_K_M)"},
            {"id": "gemma4-e4b-q4_k_m", "name": "Gemma 4 E4B (Q4_K_M)"},
            {"id": "gemma4-26b-a4b-q4_k_m", "name": "Gemma 4 26B-A4B (Q4_K_M)"},
            {"id": "gemma4-31b-q4_k_m", "name": "Gemma 4 31B (Q4_K_M)"},
        ],
        "default_model": "gemma4-e2b-q4_k_m",
        # Gemma 4 runs entirely locally — no API key needed.
        "key_url": "",
        "key_prefix": "",
        "key_min_length": 0,
    },
]


class KiAssistAPI:
    """Backend API exposed to the frontend via pywebview."""

    # Config keys for persisted model selections
    _CFG_LAST_GEMMA_MODEL = "last_gemma_server_model"
    _CFG_PROVIDER = "last_provider"
    _CFG_MODEL = "last_model"
    _CFG_SECONDARY_PROVIDER = "last_secondary_provider"
    _CFG_SECONDARY_MODEL = "last_secondary_model"
    # Config keys for persisted library paths
    _CFG_LAST_SYM_LIB = "last_sym_lib"
    _CFG_LAST_FP_LIB = "last_fp_lib"
    _CFG_LAST_MODELS_DIR = "last_models_dir"

    def __init__(self):
        """Initialize the backend API."""
        self.api_key_store = ApiKeyStore()
        self.current_provider: Optional[AIProvider] = None
        self.current_provider_name: str = "gemma4"
        self.current_model: str = "gemma4-e2b-q4_k_m"
        # Secondary (lightweight/cheap) model used for simple tasks
        self.secondary_provider: Optional[AIProvider] = None
        self.secondary_provider_name: str = "gemma4"
        self.secondary_model: str = "gemma4-e2b-q4_k_m"
        self.recent_projects_store = RecentProjectsStore()
        self._local_model_manager = LocalModelManager()
        self.current_session_id: Optional[str] = None
        self._current_project_path: Optional[str] = None
        # System prompt builder for injecting project/PCB context
        self._prompt_builder = SystemPromptBuilder()
        # Project context caches (cleared on project switch / new session)
        self._raw_context_cache: Optional[str] = None
        self._synthesized_context_cache: Optional[str] = None
        # Context lifecycle state (RequirementsManager-backed)
        self._requirements_manager: Optional[RequirementsManager] = None
        self._context_lifecycle: Dict[str, Any] = self._default_lifecycle_state()
        # Streaming state
        self._stream_lock = threading.Lock()
        self._stream_buffer = ""
        self._stream_thinking_buffer = ""
        self._stream_in_thinking = False
        self._stream_done = True
        self._stream_error = None
        self._stream_tool_activity: Optional[str] = None  # e.g. "Searching the web…"
        self._stream_cancel = threading.Event()
        # Part import progress (polled by frontend)
        self._import_progress = ""
        # Persistent event loop for async streaming (avoids closing the loop
        # between calls which would destroy the genai client's async session)
        self._async_loop = asyncio.new_event_loop()
        self._async_thread = threading.Thread(
            target=self._async_loop.run_forever, daemon=True
        )
        self._async_thread.start()

        # Pre-built library index for fast symbol/footprint search
        from .importer.library_index import LibraryIndex
        self._library_index = LibraryIndex()
        # Start building the index in the background immediately
        self._library_index.build_async()

        # Restore persisted model selections from config
        self._restore_model_selections()

        # Auto-start last Gemma server in background so it's ready by the
        # time the user sends a message.
        self._auto_start_last_server()

    # ------------------------------------------------------------------
    # Context lifecycle helpers
    # ------------------------------------------------------------------

    # Maximum number of question rounds before forcing finalization.
    _MAX_QUESTION_ROUNDS = 2

    @staticmethod
    def _default_lifecycle_state() -> Dict[str, Any]:
        """Return a fresh context-lifecycle state dict."""
        return {
            "state": "idle",
            "raw_context": "",
            "questions": [],
            "current_question_index": 0,
            "answers": [],
            "requirements": "",
            "synthesized_context": "",
            "error": "",
            "wizard_phase": False,
            "question_round": 0,
        }

    def _ensure_requirements_manager(self) -> Optional[RequirementsManager]:
        """Lazily create a :class:`RequirementsManager` for the active project."""
        if not self._current_project_path:
            return None
        if self._requirements_manager is None or str(
            self._requirements_manager.project_dir
        ) != str(Path(self._current_project_path).parent if Path(self._current_project_path).is_file() else Path(self._current_project_path)):
            self._requirements_manager = RequirementsManager(self._current_project_path)
        return self._requirements_manager

    # ------------------------------------------------------------------
    # Config persistence helpers
    # ------------------------------------------------------------------

    def _get_config(self) -> Dict[str, Any]:
        """Load the full ``~/.kiassist/config.json`` dictionary."""
        try:
            config_path = self.api_key_store._get_config_path()
            if config_path.exists():
                with open(config_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                return data if isinstance(data, dict) else {}
        except Exception:
            pass
        return {}

    def _save_config_field(self, key: str, value: Any) -> None:
        """Persist a single key in ``~/.kiassist/config.json``."""
        try:
            config_path = self.api_key_store._ensure_config_dir()
            config = self._get_config()
            config[key] = value
            with open(config_path, "w", encoding="utf-8") as f:
                json.dump(config, f, indent=2)
        except Exception:
            logger.warning("Failed to save config key %s", key)

    def _restore_model_selections(self) -> None:
        """Restore provider/model selections from ``~/.kiassist/config.json``."""
        config = self._get_config()
        valid_ids = {p["id"] for p in _PROVIDER_REGISTRY}

        saved_provider = config.get(self._CFG_PROVIDER)
        saved_model = config.get(self._CFG_MODEL)
        if saved_provider and saved_provider in valid_ids:
            self.current_provider_name = saved_provider
            if saved_model:
                self.current_model = saved_model

        saved_sec_provider = config.get(self._CFG_SECONDARY_PROVIDER)
        saved_sec_model = config.get(self._CFG_SECONDARY_MODEL)
        if saved_sec_provider and saved_sec_provider in valid_ids:
            self.secondary_provider_name = saved_sec_provider
            if saved_sec_model:
                self.secondary_model = saved_sec_model

        logger.info(
            "Restored model selections: primary=%s/%s, secondary=%s/%s",
            self.current_provider_name, self.current_model,
            self.secondary_provider_name, self.secondary_model,
        )

    def _persist_model_selections(self) -> None:
        """Save current provider/model selections to config file."""
        try:
            config_path = self.api_key_store._ensure_config_dir()
            config = self._get_config()
            config[self._CFG_PROVIDER] = self.current_provider_name
            config[self._CFG_MODEL] = self.current_model
            config[self._CFG_SECONDARY_PROVIDER] = self.secondary_provider_name
            config[self._CFG_SECONDARY_MODEL] = self.secondary_model
            with open(config_path, "w", encoding="utf-8") as f:
                json.dump(config, f, indent=2)
        except Exception:
            logger.warning("Failed to persist model selections")

    # ------------------------------------------------------------------
    # Symbol field defaults
    # ------------------------------------------------------------------

    _CFG_SYMBOL_FIELD_DEFAULTS = "symbol_field_defaults"

    # The factory defaults if nothing is saved yet.
    _BUILTIN_FIELD_DEFAULTS: List[Dict[str, str]] = [
        {"key": "Reference", "enabled": "true"},
        {"key": "Value",     "enabled": "true"},
        {"key": "Footprint", "enabled": "true"},
        {"key": "Datasheet", "enabled": "true"},
        {"key": "Description", "enabled": "true"},
        {"key": "MF",        "enabled": "true"},
        {"key": "MPN",       "enabled": "true"},
        {"key": "DKPN",      "enabled": "true"},
        {"key": "LCSC",      "enabled": "true"},
    ]

    def get_symbol_field_defaults(self) -> Dict[str, Any]:
        """Return the user's configured default symbol fields.

        Each entry is ``{"key": "<field name>", "enabled": "true"|"false"}``.
        """
        config = self._get_config()
        fields = config.get(self._CFG_SYMBOL_FIELD_DEFAULTS)
        if not fields or not isinstance(fields, list):
            fields = self._BUILTIN_FIELD_DEFAULTS
        return {"success": True, "fields": fields}

    def set_symbol_field_defaults(self, fields: list) -> Dict[str, Any]:
        """Persist the user's configured default symbol fields.

        *fields* is a list of ``{"key": str, "enabled": str}`` dicts.
        """
        try:
            # Validate input
            cleaned: List[Dict[str, str]] = []
            for entry in fields:
                key = str(entry.get("key", "")).strip()
                enabled = str(entry.get("enabled", "true")).lower()
                if key:
                    cleaned.append({"key": key, "enabled": enabled})
            self._save_config_field(self._CFG_SYMBOL_FIELD_DEFAULTS, cleaned)
            return {"success": True}
        except Exception as exc:
            logger.warning("Failed to save symbol field defaults: %s", exc)
            return {"success": False, "error": str(exc)}

    def _auto_start_last_server(self) -> None:
        """Auto-start the last-used Gemma server model in a background thread.

        Only starts if a model was previously recorded and is still downloaded.
        Runs in a daemon thread so it doesn't block the UI from loading.
        """
        config = self._get_config()
        last_model = config.get(self._CFG_LAST_GEMMA_MODEL)
        if not last_model:
            return

        # Check the model is still downloaded before starting
        variant = self._local_model_manager._find_variant(last_model)
        if not variant:
            return
        model_path = self._local_model_manager._models_dir / variant["filename"]
        if not model_path.is_file():
            return

        def _start():
            try:
                logger.info("Auto-starting last Gemma server model: %s", last_model)
                result = self._local_model_manager.start_server(last_model)
                if result.get("success"):
                    logger.info("Auto-started Gemma server for %s", last_model)
                else:
                    logger.warning(
                        "Auto-start Gemma server failed: %s",
                        result.get("error", "unknown"),
                    )
            except Exception:
                logger.exception("Auto-start Gemma server error")

        thread = threading.Thread(target=_start, daemon=True, name="gemma-auto-start")
        thread.start()
    
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
            Dictionary with providers list, current provider and model, and
            secondary provider/model selection.
        """
        providers = []
        for info in _PROVIDER_REGISTRY:
            entry = dict(info)
            if info["id"] == "local":
                # Local models do not require an API key, so we report
                # has_key as True to indicate the provider is considered
                # configured for UI purposes. This does not imply the local
                # server is reachable. The base URL is returned so the
                # frontend can display/edit it.
                entry["has_key"] = True
                entry["base_url"] = self._get_local_base_url()
            elif info["id"] == "gemma4":
                # Gemma 4 runs locally — no API key needed.
                entry["has_key"] = True
                # Include server status so the frontend knows whether the
                # local inference server is running and which model is loaded.
                entry["server_status"] = self._local_model_manager.get_server_status()
            else:
                entry["has_key"] = self.api_key_store.has_api_key(info["id"])
            providers.append(entry)
        return {
            "success": True,
            "providers": providers,
            "current_provider": self.current_provider_name,
            "current_model": self.current_model,
            "secondary_provider": self.secondary_provider_name,
            "secondary_model": self.secondary_model,
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
        self._persist_model_selections()

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

        Returns ``None`` when no API key is available for cloud providers.
        Local providers always return an instance (they require no API key).
        """
        if provider_name == "local":
            base_url = self._get_local_base_url()
            try:
                from .ai.ollama import OllamaProvider  # optional dep
                return OllamaProvider(model=model, base_url=base_url)
            except ImportError as exc:
                raise ImportError(
                    "The 'openai' package is required to use local models. "
                    "Install it with: pip install openai"
                ) from exc

        if provider_name == "gemma4":
            # Gemma 4 uses the local llama-cpp-python server managed by
            # LocalModelManager.  If the server is running, connect to it;
            # otherwise auto-start it with the requested model.
            status = self._local_model_manager.get_server_status()
            if not status["running"]:
                start_result = self._local_model_manager.start_server(model)
                if not start_result.get("success"):
                    # If the port is already in use (orphan server from a
                    # previous session), try to connect to it anyway.
                    err = start_result.get("error", "")
                    if "already in use" in err:
                        logger.info(
                            "Port in use but manager unaware; adopting existing server."
                        )
                    else:
                        raise RuntimeError(
                            err or "Failed to start Gemma 4 server."
                        )
                status = self._local_model_manager.get_server_status()
            base_url = status["url"] or f"http://127.0.0.1:{status['port']}/v1"
            try:
                from .ai.ollama import OllamaProvider  # optional dep
                return OllamaProvider(model=model, base_url=base_url)
            except ImportError as exc:
                raise ImportError(
                    "The 'openai' package is required to use Gemma 4 local models. "
                    "Install it with: pip install openai"
                ) from exc

        api_key = self.api_key_store.get_api_key(provider_name)
        if not api_key:
            return None

        if provider_name == "gemini":
            from .ai.gemini import GeminiProvider  # optional dep
            return GeminiProvider(api_key, model)

        if provider_name == "claude":
            from .ai.claude import ClaudeProvider  # optional dep
            return ClaudeProvider(api_key, model)

        if provider_name == "openai":
            from .ai.openai import OpenAIProvider  # optional dep
            return OpenAIProvider(api_key, model)

        return None

    def _get_local_base_url(self) -> str:
        """Return the configured local model server base URL.

        Delegates to :meth:`~kiassist_utils.api_key.ApiKeyStore.get_api_key`
        with provider ``"local"``.  The ``local`` provider is file-only
        (keyring intentionally skipped), so the lookup order is:

        1. ``LOCAL_BASE_URL`` environment variable.
        2. In-memory cache (populated by :meth:`set_local_base_url`).
        3. ``~/.kiassist/config.json`` (``local_base_url`` field).

        Falls back to the Ollama default ``http://localhost:11434/v1`` when
        none of the above sources yield a value.
        """
        stored = self.api_key_store.get_api_key("local")
        if stored:
            return stored
        return "http://localhost:11434/v1"

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

        log_id = llm_logger.start(
            provider=self.current_provider_name,
            model=model or self.current_model,
            messages=msgs,
            is_stream=False,
        )
        try:
            response = provider.chat(msgs)
            llm_logger.finish(
                log_id,
                response_text=response.content,
                usage=response.usage,
            )
            return response.content
        except Exception as exc:
            llm_logger.finish(log_id, error=str(exc))
            raise

    def _build_system_prompt(self) -> Optional[str]:
        """Build a system prompt including project/PCB context.

        Returns:
            System prompt string, or ``None`` when no project context
            is available.
        """
        dynamic_parts: List[str] = []

        # Add KiCad instance info if available
        try:
            instances = detect_kicad_instances()
            if instances:
                editor_lines = []
                for inst in instances:
                    status_parts = []
                    if inst.get("pcb_open"):
                        status_parts.append("PCB editor open")
                    if inst.get("schematic_open"):
                        status_parts.append("Schematic editor open")
                    status = ", ".join(status_parts) if status_parts else "running"
                    editor_lines.append(
                        f"- {inst.get('display_name', 'KiCad')} ({status})"
                    )
                dynamic_parts.append(
                    "**Open KiCad editors:**\n" + "\n".join(editor_lines)
                )
        except Exception:
            pass

        if self._current_project_path:
            dynamic_parts.append(
                f"**Active project:** `{self._current_project_path}`"
            )

        # Include synthesized context if available (more compact than raw)
        if self._synthesized_context_cache:
            dynamic_parts.append(
                "## Synthesized Project Context\n\n" + self._synthesized_context_cache
            )

        dynamic_context = "\n\n".join(dynamic_parts) if dynamic_parts else None

        return self._prompt_builder.build(
            project_path=self._current_project_path,
            dynamic_context=dynamic_context,
        ) or None

    def _build_conversation_messages(
        self,
        store: "ConversationStore",
        session_id: str,
    ) -> List[AIMessage]:
        """Build the full message list from conversation history in the session store.

        The caller should already have persisted the latest user message before
        calling this method—it will be included in the loaded messages.
        Limits history to the last 40 turns to avoid exceeding context windows.

        Args:
            store: The conversation store instance.
            session_id: Current session ID.

        Returns:
            Ordered list of :class:`AIMessage` for the AI provider.
        """
        history: List[AIMessage] = []
        try:
            stored_messages = store.load_session(session_id)
            # Only include user and assistant messages (skip tool messages
            # that the simple chat flow doesn't need)
            for m in stored_messages:
                if m.role in ("user", "assistant") and m.content:
                    history.append(m)
        except Exception as exc:
            logger.debug("Failed to load session history: %s", exc)

        # Limit to last N turns to stay within context budget
        MAX_HISTORY_TURNS = 40
        if len(history) > MAX_HISTORY_TURNS:
            history = history[-MAX_HISTORY_TURNS:]

        return history

    # ------------------------------------------------------------------
    # Secondary (lightweight) model management
    # ------------------------------------------------------------------

    def get_model_config(self) -> Dict[str, Any]:
        """Return the current primary and secondary model configuration.

        The *primary* model is used for complex/high-quality tasks; the
        *secondary* model is used for simpler/cheaper tasks.

        Returns:
            Dictionary with ``primary`` and ``secondary`` model info.
        """
        return {
            "success": True,
            "primary": {
                "provider": self.current_provider_name,
                "model": self.current_model,
            },
            "secondary": {
                "provider": self.secondary_provider_name,
                "model": self.secondary_model,
            },
        }

    def set_secondary_model(self, provider: str, model: str) -> dict:
        """Set the secondary (lightweight/cheap) AI provider and model.

        Args:
            provider: Provider ID (``gemini``, ``claude``, ``openai``, or
                      ``local``).
            model:    Model shortcut string.

        Returns:
            Result dictionary with success status and optional warning.
        """
        valid_ids = {p["id"] for p in _PROVIDER_REGISTRY}
        if provider not in valid_ids:
            return {"success": False, "error": f"Unknown provider: {provider}"}

        self.secondary_provider_name = provider
        self.secondary_model = model
        self.secondary_provider = None  # force re-creation on next use
        self._persist_model_selections()

        try:
            new_provider = self._create_provider(provider, model)
            if new_provider:
                self.secondary_provider = new_provider
                return {"success": True}
            else:
                return {
                    "success": True,
                    "warning": (
                        f"No API key configured for {provider}. "
                        "Please add one via Settings."
                    ),
                }
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    # ------------------------------------------------------------------
    # Local model management
    # ------------------------------------------------------------------

    def set_local_base_url(self, base_url: str) -> dict:
        """Set the base URL of the local OpenAI-compatible model server.

        This URL is persisted to ``~/.kiassist/config.json`` so it survives
        restarts.  Typical values:

        * Ollama default – ``http://localhost:11434/v1``
        * LM Studio default – ``http://localhost:1234/v1``

        Args:
            base_url: Full base URL including the ``/v1`` path suffix.

        Returns:
            Result dictionary with success status and optional warning.
        """
        if not base_url or not base_url.strip():
            return {"success": False, "error": "Base URL cannot be empty."}
        base_url = base_url.strip()
        try:
            success, warning = self.api_key_store.set_api_key(base_url, "local")
            # Invalidate cached local provider so the new URL is used next time
            if self.current_provider_name == "local":
                self.current_provider = None
            if self.secondary_provider_name == "local":
                self.secondary_provider = None
            result: Dict[str, Any] = {"success": success}
            if warning:
                result["warning"] = warning
            return result
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    def get_local_models(self) -> Dict[str, Any]:
        """Detect models available on the local model server.

        Tries the OpenAI-compatible ``GET /v1/models`` endpoint first
        (works with both Ollama ≥0.1.24 and LM Studio).  If that fails,
        falls back to Ollama's native ``GET /api/tags`` endpoint.

        Returns an error dict (not an exception) if the server is unreachable
        so the caller can degrade gracefully.

        Returns:
            Dictionary with ``models`` list, ``base_url`` string, or an
            ``error`` string on failure.
        """
        import json as _json
        import urllib.request

        base_url = self._get_local_base_url()
        root_url = base_url.rstrip("/")
        # Ensure the /v1 suffix is present for the OpenAI-compat endpoint
        if not root_url.endswith("/v1"):
            v1_url = root_url + "/v1"
        else:
            v1_url = root_url
        openai_models_url = f"{v1_url}/models"

        # -- Attempt 1: OpenAI-compatible /v1/models (Ollama ≥0.1.24, LM Studio) --
        try:
            with urllib.request.urlopen(openai_models_url, timeout=5) as resp:
                data = _json.loads(resp.read().decode("utf-8"))
            raw_models = data.get("data", [])
            models = [
                {"id": m.get("id", ""), "name": m.get("id", "")}
                for m in raw_models
                if m.get("id")
            ]
            if models:
                return {"success": True, "models": models, "base_url": base_url}
        except Exception:
            pass  # fall through to Ollama-native endpoint

        # -- Attempt 2: Ollama-native /api/tags --
        # Strip /v1 suffix to reach the Ollama root
        if root_url.endswith("/v1"):
            ollama_root = root_url[:-3]
        else:
            ollama_root = root_url
        tags_url = f"{ollama_root}/api/tags"

        try:
            with urllib.request.urlopen(tags_url, timeout=5) as resp:
                data = _json.loads(resp.read().decode("utf-8"))
            raw_models = data.get("models", [])
            models = [
                {"id": m.get("name", ""), "name": m.get("name", "")}
                for m in raw_models
                if m.get("name")
            ]
            return {"success": True, "models": models, "base_url": base_url}
        except Exception as exc:
            return {
                "success": False,
                "error": (
                    f"Could not reach local model server at {base_url}: {exc}. "
                    "Make sure Ollama or LM Studio is running."
                ),
                "models": [],
                "base_url": base_url,
            }

    # ------------------------------------------------------------------
    # Gemma 4 local model management
    # ------------------------------------------------------------------

    def get_gemma_models(self) -> Dict[str, Any]:
        """Return available Gemma 4 model variants with download status.

        Each entry includes ``id``, ``name``, ``size_label``, ``description``,
        ``downloaded`` (bool), and ``path``.

        Returns:
            Dictionary with ``models`` list and server status.
        """
        try:
            models = self._local_model_manager.get_available_models()
            status = self._local_model_manager.get_server_status()
            return {
                "success": True,
                "models": models,
                "server_status": status,
            }
        except Exception as exc:
            return {"success": False, "error": str(exc), "models": []}

    def download_gemma_model(self, model_id: str) -> Dict[str, Any]:
        """Start downloading a Gemma 4 model variant.

        The download runs in the background.  Use :meth:`get_gemma_download_progress`
        to poll progress.

        Args:
            model_id: Variant ID (e.g. ``"gemma4-e4b-q4_k_m"``).

        Returns:
            Result dictionary.
        """
        try:
            return self._local_model_manager.download_model(model_id)
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    def cancel_gemma_download(self) -> Dict[str, Any]:
        """Cancel an in-progress Gemma 4 model download.

        Returns:
            Result dictionary.
        """
        return self._local_model_manager.cancel_download()

    def get_gemma_download_progress(self) -> Dict[str, Any]:
        """Poll the current download progress for Gemma 4 models.

        Returns:
            Dictionary with ``model_id``, ``percent``, ``downloaded_bytes``,
            ``total_bytes``, ``speed_bytes_per_sec``, ``eta_seconds``,
            ``status`` (``idle`` / ``downloading`` / ``completed`` / ``error``
            / ``cancelled``), and ``error`` string.
        """
        return self._local_model_manager.get_download_progress()

    def delete_gemma_model(self, model_id: str) -> Dict[str, Any]:
        """Delete a downloaded Gemma 4 model file.

        Args:
            model_id: The variant ID to delete.

        Returns:
            Result dictionary.
        """
        try:
            return self._local_model_manager.delete_model(model_id)
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    def start_gemma_server(
        self,
        model_id: str,
        n_ctx: int = 16384,
        n_gpu_layers: int = -1,
    ) -> Dict[str, Any]:
        """Start the local Gemma 4 inference server.

        Launches a ``llama-cpp-python`` server to serve the requested model
        on ``http://127.0.0.1:{port}/v1``.

        Args:
            model_id: Variant ID of the downloaded model to serve.
            n_ctx: Context window size in tokens (default 16384).
            n_gpu_layers: GPU layers to offload (-1 = all available).

        Returns:
            Result dictionary with ``url`` on success.
        """
        try:
            result = self._local_model_manager.start_server(
                model_id, n_ctx=n_ctx, n_gpu_layers=n_gpu_layers
            )
            if result.get("success"):
                self._save_config_field(self._CFG_LAST_GEMMA_MODEL, model_id)
            return result
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    def stop_gemma_server(self) -> Dict[str, Any]:
        """Stop the local Gemma 4 inference server.

        Returns:
            Result dictionary.
        """
        return self._local_model_manager.stop_server()

    def get_gemma_server_status(self) -> Dict[str, Any]:
        """Check whether the local Gemma 4 inference server is running.

        Returns:
            Dictionary with ``running`` (bool), ``url``, ``model_id``, ``port``.
        """
        return self._local_model_manager.get_server_status()

    # ------------------------------------------------------------------
    # API key management
    # ------------------------------------------------------------------

    # Providers that run fully locally and never require an API key.
    _LOCAL_PROVIDERS = frozenset({"local", "gemma4"})

    def check_api_key(self, provider: Optional[str] = None) -> bool:
        """Check if an API key is stored for *provider* (default: current provider).

        Local providers (``"local"``, ``"gemma4"``) are always considered
        configured because they do not require an API key.

        Args:
            provider: Provider ID to check, or ``None`` to use the active provider.

        Returns:
            ``True`` if an API key exists (or the provider needs no key).
        """
        target = (provider or self.current_provider_name).lower().strip()
        if target in self._LOCAL_PROVIDERS:
            logger.debug("check_api_key(%r) -> True (no key needed)", target)
            return True
        has_key = self.api_key_store.has_api_key(target)
        logger.debug("check_api_key(%r) -> %s", target, has_key)
        return has_key

    def get_api_key(self, provider: Optional[str] = None) -> Optional[str]:
        """Get the stored API key for *provider* (default: current provider).

        Local providers (``"local"``, ``"gemma4"``) do not use API keys; this
        returns ``None`` for them without consulting the key store.

        Args:
            provider: Provider ID, or ``None`` to use the active provider.

        Returns:
            The API key or ``None``.
        """
        target = (provider or self.current_provider_name).lower().strip()
        if target in self._LOCAL_PROVIDERS:
            return None
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
        ConversationStore so they appear in the sessions list.  The full
        conversation history from the current session is sent to the AI so it
        has context from prior turns.

        Args:
            message: The message to send.
            model: Optional model override (uses current model when omitted).

        Returns:
            Dictionary with ``response`` text or ``error``.
        """
        try:
            provider = self._get_or_create_provider(model)
            if not provider:
                return {
                    "success": False,
                    "error": "No AI provider configured. Please add an API key via Settings.",
                }

            store = self._get_session_store()
            if not self.current_session_id:
                self.current_session_id = store.new_session()

            # Persist the user message first
            user_msg = AIMessage(role="user", content=message)
            store.append(self.current_session_id, user_msg)

            # Build full conversation history + system prompt
            msgs = self._build_conversation_messages(store, self.current_session_id)
            system_prompt = self._build_system_prompt()

            log_id = llm_logger.start(
                provider=self.current_provider_name,
                model=model or self.current_model,
                messages=msgs,
                system_prompt=system_prompt,
                is_stream=False,
            )
            try:
                response = provider.chat(msgs, system_prompt=system_prompt)
                response_text = response.content
                llm_logger.finish(
                    log_id,
                    response_text=response_text,
                    usage=response.usage,
                )
            except Exception as exc:
                llm_logger.finish(log_id, error=str(exc))
                raise

            assistant_msg = AIMessage(role="assistant", content=response_text)
            store.append(self.current_session_id, assistant_msg)

            return {"success": True, "response": response_text}
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    # ------------------------------------------------------------------
    # Built-in tool schemas for the chat stream
    # ------------------------------------------------------------------

    _WEB_SEARCH_TOOL_SCHEMA = {
        "name": "web_search",
        "description": (
            "Search the web for information about electronic components, "
            "datasheets, PCB design techniques, or any other technical topic. "
            "Use this tool when the user asks about specific components, needs "
            "product recommendations, wants to compare parts, or asks questions "
            "that require up-to-date information from the internet."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "The search query. Be specific and include relevant "
                        "technical terms (e.g. 'TXS0108E 8-channel bidirectional "
                        "level shifter datasheet')."
                    ),
                },
            },
            "required": ["query"],
        },
    }

    _BUILTIN_TOOL_SCHEMAS = [_WEB_SEARCH_TOOL_SCHEMA]

    def _execute_builtin_tool(self, name: str, arguments: Dict[str, Any]) -> str:
        """Execute a built-in tool and return the result as a string.

        Args:
            name: Tool name (e.g. ``"web_search"``).
            arguments: Parsed arguments dict.

        Returns:
            String result to feed back to the model.
        """
        if name == "web_search":
            from .web_search import web_search
            query = arguments.get("query", "")
            if not query:
                return "Error: empty search query."
            results = web_search(query)
            if not results:
                return f"No web search results found for: {query}"
            # Format results for the model
            lines = [f"Web search results for: {query}\n"]
            for i, r in enumerate(results, 1):
                lines.append(
                    f"[{i}] {r.get('title', 'Untitled')}\n"
                    f"    URL: {r.get('url', '')}\n"
                    f"    {r.get('snippet', '').strip()}"
                )
            return "\n".join(lines)
        return f"Error: unknown tool '{name}'."

    def start_stream_message(self, message: str, model: Optional[str] = None, raw_mode: bool = False) -> dict:
        """Start streaming a response from the active AI provider in a background thread.

        The user prompt is persisted immediately; the final assembled assistant
        response is persisted in the background thread once streaming completes.
        The full conversation history from the current session is sent to the
        AI provider so it has context from prior turns, along with a system
        prompt containing project/PCB environment information.

        When the provider supports tool calling, a ``web_search`` tool is
        made available so the model can search the web for component data,
        datasheets, and other technical information without requiring a
        separate UI panel.

        When *raw_mode* is ``True``, only the user message is sent to the
        provider—no system prompt or conversation history is included.

        Args:
            message: The message to send.
            model: Optional model override.
            raw_mode: If ``True``, send only the bare user message with no
                context, system prompt, or conversation history.

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

            self._stream_cancel.clear()
            with self._stream_lock:
                self._stream_buffer = ""
                self._stream_thinking_buffer = ""
                self._stream_in_thinking = False
                self._stream_done = False
                self._stream_error = None
                self._stream_tool_activity = None

            if raw_mode:
                # Raw mode: send only the bare user message, no history or system prompt
                msgs = [AIMessage(role="user", content=message)]
                system_prompt = None
            else:
                # Build full conversation history (the new user message is
                # already persisted above and will be included)
                msgs = self._build_conversation_messages(store, session_id)
                system_prompt = self._build_system_prompt()
            cancel_event = self._stream_cancel

            # Determine whether to offer built-in tools to the model
            use_tools = (
                not raw_mode
                and provider.supports_tool_calling()
            )
            tool_schemas = self._BUILTIN_TOOL_SCHEMAS if use_tools else None

            log_id = llm_logger.start(
                provider=self.current_provider_name,
                model=model or self.current_model,
                messages=msgs,
                system_prompt=system_prompt,
                is_stream=True,
            )

            # Maximum number of tool-call round-trips before giving up
            max_tool_rounds = 5

            def _run_stream():
                async def _async_stream():
                    nonlocal msgs
                    try:
                        last_usage = {}
                        tool_round = 0

                        while True:
                            accumulated_tool_calls = []
                            async for chunk in provider.chat_stream(
                                msgs,
                                tools=tool_schemas,
                                system_prompt=system_prompt,
                            ):
                                if cancel_event.is_set():
                                    break
                                if chunk.text:
                                    with self._stream_lock:
                                        self._process_stream_chunk(chunk.text)
                                if chunk.tool_calls:
                                    accumulated_tool_calls = chunk.tool_calls
                                if chunk.usage:
                                    last_usage = chunk.usage

                            if cancel_event.is_set():
                                break

                            # If no tool calls, we're done
                            if not accumulated_tool_calls or not use_tools:
                                break

                            # Safety: limit tool round-trips
                            tool_round += 1
                            if tool_round > max_tool_rounds:
                                logger.warning(
                                    "Exceeded max tool rounds (%d); stopping.",
                                    max_tool_rounds,
                                )
                                break

                            # Execute tool calls and feed results back
                            from .ai.base import AIToolCall, AIToolResult

                            # Append assistant message with tool calls
                            with self._stream_lock:
                                assistant_text = self._stream_buffer

                            msgs.append(AIMessage(
                                role="assistant",
                                content=assistant_text,
                                tool_calls=accumulated_tool_calls,
                            ))

                            # Execute each tool call
                            tool_results = []
                            for tc in accumulated_tool_calls:
                                # Notify the frontend about the tool activity
                                activity_label = {
                                    "web_search": "Searching the web\u2026",
                                }.get(tc.name, f"Running {tc.name}\u2026")
                                with self._stream_lock:
                                    self._stream_tool_activity = activity_label

                                logger.info(
                                    "Executing built-in tool: %s(%s)",
                                    tc.name, tc.arguments,
                                )
                                result_text = self._execute_builtin_tool(
                                    tc.name, tc.arguments,
                                )
                                tool_results.append(AIToolResult(
                                    tool_call_id=tc.id,
                                    content=result_text,
                                    is_error=result_text.startswith("Error:"),
                                ))

                            # Clear tool activity before re-streaming
                            with self._stream_lock:
                                self._stream_tool_activity = None

                            # Append tool results to conversation
                            msgs.append(AIMessage(
                                role="tool",
                                tool_results=tool_results,
                            ))

                            # The model will now re-stream with the search
                            # results available.  The existing stream buffer
                            # already contains any text the model produced
                            # before deciding to call a tool — the next
                            # stream iteration will append to it.

                    except Exception as exc:
                        with self._stream_lock:
                            self._stream_error = str(exc)
                        llm_logger.finish(log_id, error=str(exc))
                        return
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

                    # Log the completed stream
                    llm_logger.finish(
                        log_id,
                        response_text=final_text,
                        usage=last_usage,
                    )

                # Schedule the coroutine on the persistent event loop
                future = asyncio.run_coroutine_threadsafe(
                    _async_stream(), self._async_loop
                )
                future.result()  # Block this thread until done

            thread = threading.Thread(target=_run_stream, daemon=True)
            thread.start()
            return {"success": True}

        except Exception as exc:
            return {"success": False, "error": f"Stream error: {exc}"}

    def _process_stream_chunk(self, text: str) -> None:
        """Route incoming stream text into thinking or response buffers.

        Parses ``<think>`` / ``</think>`` tags that Gemma and similar models
        emit for chain-of-thought reasoning.  Content inside the tags goes to
        ``_stream_thinking_buffer``; everything else goes to
        ``_stream_buffer``.

        Must be called while holding ``_stream_lock``.
        """
        remaining = text
        while remaining:
            if self._stream_in_thinking:
                # Look for the closing </think> tag
                end_idx = remaining.find("</think>")
                if end_idx == -1:
                    # Still inside thinking — buffer all remaining text
                    self._stream_thinking_buffer += remaining
                    remaining = ""
                else:
                    # Found closing tag — split at the boundary
                    self._stream_thinking_buffer += remaining[:end_idx]
                    self._stream_in_thinking = False
                    remaining = remaining[end_idx + len("</think>"):]
            else:
                # Look for an opening <think> tag
                start_idx = remaining.find("<think>")
                if start_idx == -1:
                    # No thinking tag — all goes to regular output
                    self._stream_buffer += remaining
                    remaining = ""
                else:
                    # Emit text before the tag as regular output
                    if start_idx > 0:
                        self._stream_buffer += remaining[:start_idx]
                    self._stream_in_thinking = True
                    remaining = remaining[start_idx + len("<think>"):]

    def poll_stream(self) -> dict:
        """Poll for new streaming content."""
        with self._stream_lock:
            result = {
                "success": True,
                "text": self._stream_buffer,
                "thinking": self._stream_thinking_buffer,
                "done": self._stream_done,
                "error": self._stream_error,
            }
            if self._stream_tool_activity:
                result["tool_activity"] = self._stream_tool_activity
            return result

    # ------------------------------------------------------------------
    # LLM interaction log
    # ------------------------------------------------------------------

    def get_llm_log(self, since_id: Optional[str] = None) -> Dict[str, Any]:
        """Return logged LLM interactions for debugging.

        Each entry captures the full context sent to and received from the AI
        provider: messages, system prompt, token usage, timing, etc.

        Args:
            since_id: If provided, only entries recorded *after* this ID are
                      returned (for incremental polling).

        Returns:
            Dictionary with ``entries`` list.
        """
        try:
            entries = llm_logger.get_entries(since_id=since_id)
            return {"success": True, "entries": entries}
        except Exception as exc:
            return {"success": False, "error": str(exc), "entries": []}

    def clear_llm_log(self) -> Dict[str, Any]:
        """Clear all logged LLM interactions.

        Returns:
            Dictionary with ``success`` status.
        """
        llm_logger.clear()
        return {"success": True}

    def steer_stream(self, message: str, model: Optional[str] = None) -> dict:
        """Interrupt the active stream and start a new one with an additional user message.

        The partial assistant response accumulated so far is persisted to the
        conversation history, followed by the new *steer* user message.  A
        fresh streaming call is then started so the model can incorporate the
        steering instruction.

        Args:
            message: The steering message to inject.
            model: Optional model override.

        Returns:
            ``{"success": True}`` on successful start, or an error dict.
        """
        try:
            # 1. Signal the running stream to stop
            self._stream_cancel.set()

            # 2. Wait briefly for the stream to acknowledge cancellation
            #    (the background thread will set _stream_done once it exits)
            deadline = 5.0  # seconds
            step = 0.05
            waited = 0.0
            while waited < deadline:
                with self._stream_lock:
                    if self._stream_done:
                        break
                time.sleep(step)
                waited += step

            # 3. Persist the partial assistant response (if any)
            with self._stream_lock:
                partial_text = self._stream_buffer.strip()

            store = self._get_session_store()
            if not self.current_session_id:
                self.current_session_id = store.new_session()
            session_id = self.current_session_id

            if partial_text:
                try:
                    store.append(
                        session_id,
                        AIMessage(role="assistant", content=partial_text),
                    )
                except Exception as exc:
                    logger.warning("Failed to persist partial response on steer: %s", exc)

            # 4. Now start a new stream with the steer message
            return self.start_stream_message(message, model)

        except Exception as exc:
            return {"success": False, "error": f"Steer error: {exc}"}

    def new_chat_session(self) -> dict:
        """Start a new chat session, discarding the current session ID.

        The next ``send_message`` / ``start_stream_message`` call will
        automatically create a fresh session in the conversation store.

        Returns:
            Dictionary with ``success`` status and the new session ID.
        """
        self.current_session_id = None
        # Also clear the prompt cache so the next message rebuilds context
        self._prompt_builder.clear_cache()
        # Clear project context caches so they are rebuilt for the new session
        self._raw_context_cache = None
        self._synthesized_context_cache = None
        return {"success": True}
    
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

            new_path = str(p)

            # If the path hasn't changed, skip the reset so an in-progress
            # context lifecycle (wizard Q&A, LLM generation, etc.) is not lost.
            if self._current_project_path == new_path:
                return {"success": True}

            self._current_project_path = new_path
            # Clear prompt cache so project context refreshes
            self._prompt_builder.clear_cache()
            # Clear cached project context
            self._raw_context_cache = None
            self._synthesized_context_cache = None
            # Reset context lifecycle state for the new project
            self._context_lifecycle = self._default_lifecycle_state()
            self._requirements_manager = None
            # Start a new session for the new project
            self.current_session_id = None
            # Rebuild library index with new project dir
            project_dir = str(Path(new_path).parent) if new_path else None
            self._library_index.set_project_dir(project_dir)
            self._library_index.rebuild_async()
            return {"success": True}
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    # ------------------------------------------------------------------
    # Project context (raw + LLM-synthesized)
    # ------------------------------------------------------------------

    def get_raw_project_context(self) -> Dict[str, Any]:
        """Build and return the raw project context for the active project.

        The raw context includes schematic/board file listing, hierarchical
        sheet references, a component BOM, and a netlist.

        Returns:
            Dictionary with ``context`` string or ``error``.
        """
        if not self._current_project_path:
            return {"success": False, "error": "No project selected."}

        try:
            from .context.project_context import get_raw_context
            ctx = get_raw_context(self._current_project_path)
            self._raw_context_cache = ctx
            return {"success": True, "context": ctx}
        except Exception as exc:
            logger.error("get_raw_project_context failed: %s", exc, exc_info=True)
            return {"success": False, "error": str(exc)}

    def get_synthesized_project_context(self) -> Dict[str, Any]:
        """Use the LLM to synthesize a compact summary of the project context.

        First builds the raw context (if not cached), then sends it to the
        active AI provider for synthesis into a clean, structured summary.

        Returns:
            Dictionary with ``context`` string or ``error``.
        """
        if not self._current_project_path:
            return {"success": False, "error": "No project selected."}

        try:
            # Get or build raw context
            if self._raw_context_cache is None:
                from .context.project_context import get_raw_context
                self._raw_context_cache = get_raw_context(self._current_project_path)

            # Get a provider for synthesis
            provider = self._get_or_create_provider()
            if not provider:
                return {
                    "success": False,
                    "error": "No AI provider configured. Please add an API key or start a local model.",
                }

            from .context.project_context import get_llm_synthesized_context
            from .ai.llm_logger import llm_logger
            from .ai.base import AIMessage

            log_id = llm_logger.start(
                provider=self.current_provider_name,
                model=self.current_model,
                messages=[AIMessage(role="user", content="[Context synthesis request]")],
                system_prompt="[Context synthesis]",
                is_stream=False,
            )

            try:
                synthesized = get_llm_synthesized_context(
                    self._raw_context_cache, provider
                )
                llm_logger.finish(log_id, response_text=synthesized)
            except Exception as exc:
                llm_logger.finish(log_id, error=str(exc))
                raise

            self._synthesized_context_cache = synthesized
            return {"success": True, "context": synthesized}
        except Exception as exc:
            logger.error("get_synthesized_project_context failed: %s", exc, exc_info=True)
            return {"success": False, "error": str(exc)}

    def get_cached_project_context(self) -> Dict[str, Any]:
        """Return any cached raw and synthesized context without re-computing.

        Returns:
            Dictionary with ``raw`` and ``synthesized`` strings (may be null).
        """
        return {
            "success": True,
            "raw": self._raw_context_cache,
            "synthesized": self._synthesized_context_cache,
        }

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
                        # Enrich with pcb_path/schematic_path from disk
                        if not project.get('pcb_path') or not project.get('schematic_path'):
                            project_dir = Path(project_path).parent if project_path.endswith('.kicad_pro') else Path(project_path)
                            proj_name = project.get('name', Path(project_path).stem)
                            if project_dir.is_dir():
                                if not project.get('pcb_path'):
                                    project['pcb_path'] = find_file_in_dir(project_dir, '.kicad_pcb', proj_name)
                                if not project.get('schematic_path'):
                                    project['schematic_path'] = find_file_in_dir(project_dir, '.kicad_sch', proj_name)
                        recent_projects.append(project)
            
            return {
                "success": True,
                "open_projects": open_instances,
                "recent_projects": recent_projects
            }
            
        except Exception as e:
            return {"success": False, "error": str(e), "open_projects": [], "recent_projects": []}
    
    # Requirements Wizard API methods

    # ------------------------------------------------------------------
    # Context Lifecycle API  (raw → questions → answers → requirements)
    # ------------------------------------------------------------------

    def start_context_lifecycle(self) -> Dict[str, Any]:
        """Start the context lifecycle.

        If ``requirements.md`` already exists the wizard questions are skipped
        and the lifecycle proceeds directly to LLM-based question generation
        with the raw context and existing requirements.

        Otherwise only the first two wizard questions (objectives & known parts)
        are presented so the LLM can refine the remaining questions quickly.

        Returns:
            Dictionary with lifecycle state or error.
        """
        if not self._current_project_path:
            return {"success": False, "error": "No project selected."}

        try:
            # Reset lifecycle state
            self._context_lifecycle = self._default_lifecycle_state()
            self._context_lifecycle["state"] = "extracting"

            # 1. Extract raw context
            from .context.project_context import get_raw_context
            raw_ctx = get_raw_context(self._current_project_path)
            self._raw_context_cache = raw_ctx
            self._context_lifecycle["raw_context"] = raw_ctx

            # 2. Check for existing requirements.md
            project_dir = (
                Path(self._current_project_path).parent
                if Path(self._current_project_path).is_file()
                else Path(self._current_project_path)
            )
            req_path = project_dir / "requirements.md"
            existing_req = ""
            if req_path.is_file():
                existing_req = req_path.read_text(encoding="utf-8")

            if existing_req.strip():
                # requirements.md exists — skip ALL user questions and go
                # straight to finalization with raw context + requirements.
                provider = self._get_or_create_provider()
                if not provider:
                    self._context_lifecycle["state"] = "idle"
                    return {
                        "success": False,
                        "error": "No AI provider configured. Add an API key or start a local model.",
                    }
                self._context_lifecycle["state"] = "generating"
                self._run_context_llm_stream(
                    "finalize", raw_ctx, existing_req, [], provider,
                )
                return {"success": True, **self._context_lifecycle}

            # 3. No requirements.md — present the first N wizard questions
            wizard_qs = get_default_questions()
            initial_qs = wizard_qs[:INITIAL_QUESTIONS_COUNT]
            self._context_lifecycle["questions"] = [
                {
                    "question": q["question"],
                    "suggestions": [],
                }
                for q in initial_qs
            ]
            self._context_lifecycle["current_question_index"] = 0
            self._context_lifecycle["state"] = "questioning"
            self._context_lifecycle["wizard_phase"] = True

            return {"success": True, **self._context_lifecycle}

        except Exception as exc:
            logger.error("start_context_lifecycle failed: %s", exc, exc_info=True)
            self._context_lifecycle["state"] = "idle"
            self._context_lifecycle["error"] = str(exc)
            return {"success": False, "error": str(exc)}

    def get_context_lifecycle_state(self) -> Dict[str, Any]:
        """Return the current context lifecycle state.

        Returns:
            Dictionary with full lifecycle state.
        """
        return {"success": True, **self._context_lifecycle}

    def submit_context_answer(self, answer: str) -> Dict[str, Any]:
        """Submit an answer to the current pending question.

        If the answer is the literal ``"__SKIP__"`` sentinel the question is
        dropped entirely — it will not appear in the answers list sent to the
        LLM, so the model won't attempt to infer or re-ask it.

        If more questions remain, the next question index is advanced.
        When all questions have been answered the finalization is launched
        in the background (streaming) so the frontend can poll for updates.

        Args:
            answer: The user's answer text, or ``"__SKIP__"`` to skip.

        Returns:
            Updated lifecycle state.
        """
        lc = self._context_lifecycle
        if lc["state"] != "questioning":
            return {"success": False, "error": "Not in questioning state."}

        idx = lc["current_question_index"]
        questions = lc["questions"]
        if idx >= len(questions):
            return {"success": False, "error": "No more questions."}

        # Record answer — but skip entirely if the user chose to skip.
        is_skip = answer.strip() == "__SKIP__"
        if not is_skip:
            q_obj = questions[idx]
            q_text = q_obj["question"] if isinstance(q_obj, dict) else str(q_obj)
            lc["answers"].append({
                "question": q_text,
                "answer": answer,
            })
        lc["current_question_index"] = idx + 1

        # If more questions remain, just return updated state
        if lc["current_question_index"] < len(questions):
            return {"success": True, **lc}

        # ── All current questions answered ──────────────────────────────
        try:
            provider = self._get_or_create_provider()
            if not provider:
                return {
                    "success": False,
                    "error": "No AI provider configured.",
                }

            project_dir = (
                Path(self._current_project_path).parent
                if Path(self._current_project_path).is_file()
                else Path(self._current_project_path)
            )
            existing_req = ""
            req_path = project_dir / "requirements.md"
            if req_path.is_file():
                existing_req = req_path.read_text(encoding="utf-8")

            # Bump the question round counter
            lc["question_round"] = lc.get("question_round", 0) + 1

            if lc.get("wizard_phase"):
                # Initial wizard questions done — send answers + remaining
                # wizard questions to LLM for quick refinement (no raw context).
                lc["wizard_phase"] = False
                lc["state"] = "refining"  # signals LLM is refining Qs
                self._run_context_llm_stream(
                    "refine", "", "",
                    lc["answers"], provider,
                )
            elif lc["question_round"] >= self._MAX_QUESTION_ROUNDS:
                # Hit the question-round limit — force finalization now.
                logger.info(
                    "[ContextLifecycle] Reached max question rounds (%d) — "
                    "forcing finalization",
                    self._MAX_QUESTION_ROUNDS,
                )
                lc["state"] = "generating"
                self._run_context_llm_stream(
                    "finalize", lc["raw_context"], existing_req,
                    lc["answers"], provider,
                )
            else:
                # All (refined/LLM) questions done — launch finalization
                lc["state"] = "generating"
                self._run_context_llm_stream(
                    "finalize", lc["raw_context"], existing_req,
                    lc["answers"], provider,
                )

            return {"success": True, **self._context_lifecycle}

        except Exception as exc:
            logger.error("submit_context_answer finalise failed: %s", exc, exc_info=True)
            lc["state"] = "questioning"
            lc["error"] = str(exc)
            return {"success": False, "error": str(exc)}

    def _run_context_llm_stream(
        self,
        phase: str,
        raw_context: str,
        existing_requirements: str,
        answers: List[Dict[str, str]],
        provider: Any,
    ) -> None:
        """Run a context-lifecycle LLM call via streaming in the background.

        Args:
            phase: ``"refine"``, ``"questions"``, or ``"finalize"``.
            raw_context: Raw context text.
            existing_requirements: Existing requirements text.
            answers: List of Q&A dicts.
            provider: AI provider instance.
        """
        from .context.project_context import (
            build_context_questions_prompt,
            build_refine_questions_prompt,
            build_requirements_and_context_prompt,
        )

        if phase == "refine":
            prompt_data = build_refine_questions_prompt(answers)
        elif phase == "questions":
            prompt_data = build_context_questions_prompt(
                raw_context, existing_requirements,
                wizard_answers=answers or None,
            )
        elif phase == "finalize_force":
            # Force finalization — tell the LLM it MUST produce a result now.
            prompt_data = build_requirements_and_context_prompt(
                raw_context, existing_requirements, answers,
                force_complete=True,
            )
        else:
            prompt_data = build_requirements_and_context_prompt(
                raw_context, existing_requirements, answers,
            )

        messages = prompt_data["messages"]
        system_prompt = prompt_data["system_prompt"]

        log_id = llm_logger.start(
            provider=self.current_provider_name,
            model=self.current_model,
            messages=messages,
            system_prompt=system_prompt,
            is_stream=True,
        )

        lc = self._context_lifecycle
        captured_phase = phase
        captured_raw = raw_context
        captured_req = existing_requirements
        captured_answers = list(answers)

        async def _stream():
            accumulated = ""
            try:
                async for chunk in provider.chat_stream(
                    messages, system_prompt=system_prompt
                ):
                    if chunk.text:
                        accumulated += chunk.text
                        llm_logger.update_stream_response(log_id, accumulated)
            except Exception as exc:
                llm_logger.finish(log_id, error=str(exc))
                lc["state"] = "idle"
                lc["error"] = str(exc)
                return

            response_text = accumulated.strip()

            # Strip <think>...</think> reasoning blocks that models like
            # Gemma emit — these would break JSON parsing downstream.
            import re as _re
            response_text = _re.sub(
                r"<think>.*?</think>", "", response_text, flags=_re.DOTALL,
            ).strip()

            logger.info(
                "[ContextLifecycle] Stream complete for phase=%s, "
                "response length=%d, first 200 chars: %.200s",
                captured_phase, len(response_text), response_text,
            )

            llm_logger.finish(log_id, response_text=response_text)

            # Process the result on the main context lifecycle state
            try:
                self._handle_context_llm_result(
                    captured_phase, response_text, captured_raw,
                    captured_req, captured_answers, provider,
                )
            except Exception as exc:
                logger.error(
                    "[ContextLifecycle] _handle_context_llm_result CRASHED: %s",
                    exc, exc_info=True,
                )
                lc["state"] = "idle"
                lc["error"] = f"Internal error: {exc}"

        def _run():
            try:
                future = asyncio.run_coroutine_threadsafe(_stream(), self._async_loop)
                future.result()  # block this thread until done
            except Exception as exc:
                logger.error(
                    "[ContextLifecycle] Background stream thread crashed: %s",
                    exc, exc_info=True,
                )
                lc["state"] = "idle"
                lc["error"] = f"Stream error: {exc}"

        thread = threading.Thread(target=_run, daemon=True)
        thread.start()

    def _handle_context_llm_result(
        self,
        phase: str,
        response_text: str,
        raw_context: str,
        existing_requirements: str,
        answers: List[Dict[str, str]],
        provider: Any,
    ) -> None:
        """Process completed LLM response for the context lifecycle."""
        from .context.project_context import (
            parse_context_questions_response,
            parse_requirements_and_context_response,
        )

        lc = self._context_lifecycle

        if phase == "refine":
            # LLM refined the remaining wizard questions based on initial
            # answers.  The response is the same JSON array format as the
            # questions phase.
            questions = parse_context_questions_response(response_text)
            logger.info(
                "[ContextLifecycle] Refined questions: %d returned",
                len(questions),
            )

            if not questions:
                # Refinement failed or returned nothing — fall through to
                # full LLM question generation with raw context.
                logger.info("[ContextLifecycle] No refined questions — falling back to full generation")
                project_dir = (
                    Path(self._current_project_path).parent
                    if Path(self._current_project_path).is_file()
                    else Path(self._current_project_path)
                )
                existing_req = ""
                req_path = project_dir / "requirements.md"
                if req_path.is_file():
                    existing_req = req_path.read_text(encoding="utf-8")

                lc["state"] = "extracting"
                self._run_context_llm_stream(
                    "questions", lc["raw_context"], existing_req,
                    lc["answers"], provider,
                )
                return

            # Present refined questions to the user
            lc["state"] = "questioning"
            lc["questions"] = lc["questions"] + questions
            lc["current_question_index"] = len(lc["answers"])
            logger.info(
                "[ContextLifecycle] State set to 'questioning' with %d refined questions",
                len(questions),
            )
            return

        if phase == "questions":
            questions = parse_context_questions_response(response_text)
            logger.info(
                "[ContextLifecycle] Parsed %d questions from LLM response",
                len(questions),
            )
            for i, q in enumerate(questions):
                logger.info("  Q%d: %s", i + 1, q)

            if not questions:
                # No questions — go straight to finalization
                logger.info("[ContextLifecycle] No questions parsed — skipping to finalization")
                lc["state"] = "generating"
                self._run_context_llm_stream(
                    "finalize", raw_context, existing_requirements,
                    lc["answers"], provider,
                )
                return

            # Append LLM questions after any existing wizard questions and
            # set the index to point at the first new question.
            lc["state"] = "questioning"
            lc["questions"] = lc["questions"] + questions
            lc["current_question_index"] = len(lc["answers"])
            logger.info(
                "[ContextLifecycle] State set to 'questioning' with %d questions",
                len(questions),
            )

            # Wire into RequirementsManager if available
            mgr = self._ensure_requirements_manager()
            if mgr:
                try:
                    req = mgr.start_raw_context_generation()
                    mgr.set_raw_context(req, raw_context)
                    mgr.set_auto_context(req, "", pending_questions=questions)
                except Exception:
                    pass  # non-critical persistence
            return

        # phase == "finalize" or "finalize_force"
        result = parse_requirements_and_context_response(response_text)

        if result.get("status") == "needs_more_info" and phase != "finalize_force":
            # Only allow more questions if we haven't hit the round limit.
            current_round = lc.get("question_round", 0)
            if current_round >= self._MAX_QUESTION_ROUNDS:
                logger.info(
                    "[ContextLifecycle] Finalize returned needs_more_info "
                    "but we're at round %d (max %d) — forcing done with "
                    "available info",
                    current_round,
                    self._MAX_QUESTION_ROUNDS,
                )
                # Re-run finalization with force_complete flag
                lc["state"] = "generating"
                self._run_context_llm_stream(
                    "finalize_force", lc["raw_context"],
                    existing_requirements, lc["answers"], provider,
                )
                return

            raw_qs = result.get("questions", [])
            # Wrap plain strings as question dicts for consistency
            new_questions = [
                q if isinstance(q, dict) else {"question": str(q), "suggestions": []}
                for q in raw_qs
            ]
            lc["state"] = "questioning"
            lc["questions"] = lc["questions"] + new_questions
            return

        if result.get("status") == "error":
            lc["state"] = "idle"
            lc["error"] = result.get("error", "Unknown error")
            return

        # Done — store results
        lc["state"] = "done"
        lc["requirements"] = result.get("requirements", "")
        lc["synthesized_context"] = result.get("synthesized_context", "")
        lc["error"] = ""

        # Cache synthesized context for chat system-prompt injection
        self._synthesized_context_cache = lc["synthesized_context"]

        # Persist requirements.md if generated
        if lc["requirements"]:
            project_dir = (
                Path(self._current_project_path).parent
                if Path(self._current_project_path).is_file()
                else Path(self._current_project_path)
            )
            try:
                save_requirements_file(str(project_dir), lc["requirements"])
            except Exception as exc:
                logger.warning("Failed to save requirements.md: %s", exc)

        # Update RequirementsManager
        mgr = self._ensure_requirements_manager()
        if mgr:
            try:
                req = mgr.load_or_create()
                if req.state == ContextState.GENERATING_REQUIREMENTS:
                    mgr.set_auto_context(req, lc["synthesized_context"])
                elif req.state == ContextState.QUERYING_USER:
                    mgr.submit_user_answers(req, lc["requirements"])
                    mgr.mark_up_to_date(req)
            except Exception:
                pass  # non-critical persistence

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
    
    # ------------------------------------------------------------------
    # Web component search
    # ------------------------------------------------------------------

    def web_search_components(
        self,
        query: str,
        model: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Search the web for electronic components and return AI-synthesized recommendations.

        When the active provider is Gemini, uses native **Google Search
        grounding** (``types.Tool(google_search=types.GoogleSearch())``) for
        authoritative results with automatic citations – no HTML scraping
        required.  For all other providers (Claude, OpenAI, local models), a
        DuckDuckGo HTML search is performed first and the results are passed to
        the AI for synthesis.

        Args:
            query: Natural-language description of the needed component (e.g.
                   ``"logic level converter 3.3V to 1.8-5V bidirectional"``).
            model: Optional model override.

        Returns:
            Dictionary with ``response`` (AI-synthesized Markdown text),
            ``search_results`` (web result list), and ``success`` flag.
        """
        if not query or not query.strip():
            return {"success": False, "error": "Query cannot be empty."}

        try:
            provider = self._get_or_create_provider(model)
            if not provider:
                return {
                    "success": False,
                    "error": (
                        "No AI provider configured. "
                        "Please add an API key or start a local model."
                    ),
                }

            from .ai.llm_logger import llm_logger
            from .ai.base import AIMessage as _AIMessage

            # ------------------------------------------------------------------
            # Path A: Gemini provider – use native Google Search grounding
            # ------------------------------------------------------------------
            try:
                from .ai.gemini import GeminiProvider
                is_gemini = isinstance(provider, GeminiProvider)
            except ImportError:
                is_gemini = False

            if is_gemini:
                grounding_prompt = (
                    "You are a knowledgeable electronics engineer assistant helping "
                    "with PCB design component selection.\n\n"
                    f"User request: {query.strip()}\n\n"
                    "Search the web and recommend 2–4 specific components that best "
                    "match this request. For each component, provide key specifications, "
                    "notable features, and trade-offs relevant to PCB design. "
                    "Format your response in clear Markdown with component names as headings."
                )

                log_id = llm_logger.start(
                    provider=self.current_provider_name,
                    model=model or self.current_model,
                    messages=[_AIMessage(role="user", content=grounding_prompt)],
                    is_stream=False,
                )
                try:
                    result = provider.search_grounded_query(grounding_prompt)
                    response_text = result["response_text"]
                    search_results = result["search_results"]
                    llm_logger.finish(log_id, response_text=response_text, usage=result["usage"])
                except Exception as exc:
                    llm_logger.finish(log_id, error=str(exc))
                    raise

                return {
                    "success": True,
                    "response": response_text,
                    "search_results": search_results,
                    "query": query.strip(),
                    "grounding": "google",
                }

            # ------------------------------------------------------------------
            # Path B: Other providers – DuckDuckGo scrape + AI synthesis
            # ------------------------------------------------------------------
            from .web_search import web_search, build_component_search_prompt

            search_query = f"{query.strip()} electronic component specifications"
            search_results = web_search(search_query)
            prompt = build_component_search_prompt(query.strip(), search_results)

            log_id = llm_logger.start(
                provider=self.current_provider_name,
                model=model or self.current_model,
                messages=[_AIMessage(role="user", content=prompt)],
                is_stream=False,
            )
            try:
                ai_response = provider.chat([_AIMessage(role="user", content=prompt)])
                response_text = ai_response.content
                llm_logger.finish(log_id, response_text=response_text, usage=ai_response.usage)
            except Exception as exc:
                llm_logger.finish(log_id, error=str(exc))
                raise

            return {
                "success": True,
                "response": response_text,
                "search_results": search_results,
                "query": query.strip(),
                "grounding": "duckduckgo",
            }
        except Exception as exc:
            logger.error("web_search_components failed: %s", exc, exc_info=True)
            return {"success": False, "error": str(exc)}

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
    # Symbol / Footprint Importer
    # ------------------------------------------------------------------

    def importer_lcsc_available(self) -> Dict[str, Any]:
        """Return whether the easyeda2kicad package is installed."""
        from .importer import lcsc_available
        return {"available": lcsc_available()}

    def importer_get_sym_libraries(self) -> Dict[str, Any]:
        """List all available symbol library nicknames."""
        try:
            libs = self._library_index.list_symbol_libraries()
            return {"success": True, "libraries": libs}
        except Exception as exc:
            return {"success": False, "error": str(exc), "libraries": []}

    def importer_get_fp_libraries(self) -> Dict[str, Any]:
        """List all available footprint library nicknames."""
        try:
            libs = self._library_index.list_footprint_libraries()
            return {"success": True, "libraries": libs}
        except Exception as exc:
            return {"success": False, "error": str(exc), "libraries": []}

    def importer_import_lcsc(
        self,
        lcsc_id: str,
        target_sym_lib: str = "",
        target_fp_lib_dir: str = "",
        models_dir: str = "",
        overwrite: bool = False,
    ) -> Dict[str, Any]:
        """Import a component from LCSC via EasyEDA.

        Args:
            lcsc_id: LCSC part number (e.g. ``"C14663"``).
            target_sym_lib: Path to the destination ``.kicad_sym`` file.
            target_fp_lib_dir: Path to the destination ``.pretty`` directory.
            models_dir: Directory for 3-D model files.
            overwrite: Replace existing entries if True.

        Returns:
            Dict with ``success``, ``component`` summary, ``warnings``, and
            ``error`` keys.
        """
        try:
            from .importer import import_lcsc, commit_import
            with tempfile.TemporaryDirectory(prefix="kiassist_lcsc_") as tmp_dir:
                result = import_lcsc(lcsc_id, output_dir=tmp_dir)
                if not result.success:
                    return {"success": False, "error": result.error, "warnings": result.warnings}

                if target_sym_lib or target_fp_lib_dir:
                    result = commit_import(
                        result.component,
                        target_sym_lib=target_sym_lib or None,
                        target_fp_lib_dir=target_fp_lib_dir or None,
                        models_dir=models_dir or None,
                        overwrite=overwrite,
                    )
                    return self._import_result_to_dict(result)
                else:
                    # Preview-only: the temp dir will be deleted when the context
                    # manager exits, so path fields would become stale.  Clear them
                    # and return only the S-expression data and field metadata.
                    result_dict = self._import_result_to_dict(result)
                    if result_dict.get("success") and "component" in result_dict:
                        result_dict["component"]["symbol_path"] = ""
                        result_dict["component"]["footprint_path"] = ""
                        result_dict["component"]["model_paths"] = []
                    return result_dict
        except Exception as exc:
            return {"success": False, "error": str(exc), "warnings": []}

    def importer_import_by_part(
        self,
        mpn: str = "",
        spn: str = "",
        lcsc: str = "",
    ) -> Dict[str, Any]:
        """Import a component by MPN, Supplier PN, or LCSC number.

        Queries Octopart for cross-reference data (DigiKey PN, LCSC,
        Mouser, datasheet, manufacturer, description) then imports
        symbol/footprint/3D via EasyEDA if an LCSC number is available.

        Args:
            mpn: Manufacturer Part Number.
            spn: Supplier Part Number (e.g. Digi-Key, Mouser).
            lcsc: LCSC / EasyEDA part number.

        Returns:
            Dict with ``success``, ``component`` summary, ``warnings``,
            and ``error`` keys.
        """
        try:
            from .importer.part_lookup import import_by_part

            def _push_progress(msg: str) -> None:
                self._import_progress = msg

            with tempfile.TemporaryDirectory(prefix="kiassist_part_") as tmp_dir:
                result = import_by_part(
                    mpn=mpn, spn=spn, lcsc=lcsc, output_dir=tmp_dir,
                    on_progress=_push_progress,
                )
                if not result.success:
                    return {
                        "success": False,
                        "error": result.error,
                        "warnings": result.warnings,
                        "cad_sources": [
                            {
                                "partner": s.partner,
                                "has_symbol": s.has_symbol,
                                "has_footprint": s.has_footprint,
                                "has_3d_model": s.has_3d_model,
                                "preview_symbol": s.preview_symbol,
                                "preview_footprint": s.preview_footprint,
                                "preview_3d": s.preview_3d,
                                "download_url": s.download_url,
                            }
                            for s in (result.cad_sources or [])
                        ],
                        "octopart_url": result.octopart_url or "",
                    }

                # Preview-only — temp dir will be deleted, clear paths.
                result_dict = self._import_result_to_dict(result)
                if result_dict.get("success") and "component" in result_dict:
                    result_dict["component"]["symbol_path"] = ""
                    result_dict["component"]["footprint_path"] = ""
                    result_dict["component"]["model_paths"] = []
                logger.info(
                    "[DEBUG] importer_import_by_part result keys=%s cad_sources=%s octopart_url=%s",
                    list(result_dict.keys()),
                    result_dict.get("cad_sources", "NOT_PRESENT"),
                    result_dict.get("octopart_url", "NOT_PRESENT"),
                )
                return result_dict
        except Exception as exc:
            return {"success": False, "error": str(exc), "warnings": []}
        finally:
            self._import_progress = ""

    def importer_import_progress(self) -> Dict[str, Any]:
        """Return the current part-import progress message (polled by frontend)."""
        return {"status": self._import_progress}

    def importer_import_zip(
        self,
        zip_path: str,
        target_sym_lib: str = "",
        target_fp_lib_dir: str = "",
        models_dir: str = "",
        overwrite: bool = False,
    ) -> Dict[str, Any]:
        """Import a component from a SnapEDA / Ultra Librarian ZIP file.

        Args:
            zip_path: Absolute path to the ``.zip`` file.
            target_sym_lib: Destination ``.kicad_sym`` file.
            target_fp_lib_dir: Destination ``.pretty`` directory.
            models_dir: Directory for 3-D model files.
            overwrite: Replace existing entries if True.

        Returns:
            Dict with ``success``, ``component`` summary, ``warnings``, ``error``.
        """
        try:
            from .importer import import_zip, commit_import
            with tempfile.TemporaryDirectory(prefix="kiassist_zip_") as tmp_dir:
                result = import_zip(zip_path, output_dir=tmp_dir)
                if not result.success:
                    return {"success": False, "error": result.error, "warnings": result.warnings}

                if target_sym_lib or target_fp_lib_dir:
                    result = commit_import(
                        result.component,
                        target_sym_lib=target_sym_lib or None,
                        target_fp_lib_dir=target_fp_lib_dir or None,
                        models_dir=models_dir or None,
                        overwrite=overwrite,
                    )

            return self._import_result_to_dict(result)
        except Exception as exc:
            return {"success": False, "error": str(exc), "warnings": []}

    def importer_search_symbols(
        self,
        query: str,
        library_name: str = "",
    ) -> Dict[str, Any]:
        """Search existing KiCad symbol libraries.

        Uses the pre-built library index for near-instant results.

        Args:
            query: Case-insensitive substring to match.
            library_name: Restrict search to this library nickname, or ``""``
                for all libraries.

        Returns:
            Dict with ``success`` and ``results`` list.
        """
        try:
            results = self._library_index.search_symbols(
                query,
                library_name=library_name or None,
            )
            return {"success": True, "results": results}
        except Exception as exc:
            return {"success": False, "error": str(exc), "results": []}

    def importer_search_footprints(
        self,
        query: str,
        library_name: str = "",
    ) -> Dict[str, Any]:
        """Search existing KiCad footprint libraries.

        Uses the pre-built library index for near-instant results.

        Args:
            query: Case-insensitive substring.
            library_name: Library nickname, or ``""`` for all.

        Returns:
            Dict with ``success`` and ``results`` list.
        """
        try:
            results = self._library_index.search_footprints(
                query,
                library_name=library_name or None,
            )
            return {"success": True, "results": results}
        except Exception as exc:
            return {"success": False, "error": str(exc), "results": []}

    def importer_reload_libraries(self) -> Dict[str, Any]:
        """Trigger a background rescan of KiCad libraries.

        The existing index (from cache or previous build) remains usable
        while the scan runs.  Poll ``importer_library_index_status()``
        to know when the new data is ready.

        Returns:
            Dict with ``success`` and index ``status``.
        """
        try:
            self._library_index.rebuild_async()
            return {"success": True, "status": self._library_index.status()}
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    def importer_library_index_status(self) -> Dict[str, Any]:
        """Return the current state of the library index.

        Returns:
            Dict with ``ready``, ``building``, ``symbol_count``,
            ``footprint_count``, and ``build_time``.
        """
        return self._library_index.status()

    def importer_get_footprint_sexpr(
        self,
        library_name: str,
        footprint_name: str,
    ) -> Dict[str, Any]:
        """Read a footprint's S-expression from an existing KiCad library.

        Also resolves the 3-D model referenced in the footprint (if any)
        and returns its content as base64-encoded data.

        Args:
            library_name: Library nickname (e.g. ``"Package_SO"``).
            footprint_name: Footprint name (e.g. ``"SOIC-8_3.9x4.9mm_P1.27mm"``).

        Returns:
            Dict with ``success``, ``sexpr`` (string), optional ``step_data``
            (base64 string), and optional ``error``.
        """
        try:
            import re, base64
            from .kicad_parser.library import _default_env
            disc = self._library_index.get_discovery()
            lib_path = disc.resolve_footprint_library(library_name)
            if not lib_path:
                return {"success": False, "error": f"Library '{library_name}' not found"}
            mod_file = Path(lib_path) / f"{footprint_name}.kicad_mod"
            if not mod_file.exists():
                return {"success": False, "error": f"Footprint '{footprint_name}' not found in '{library_name}'"}
            sexpr = mod_file.read_text(encoding="utf-8")

            # --- Resolve 3-D model (STEP file) if referenced ---
            step_data = None
            model_transform = None
            model_match = re.search(r'\(model\s+"([^"]+)"', sexpr)
            if model_match:
                model_path_raw = model_match.group(1)
                # Expand ${VAR} using KiCad env variables
                env = _default_env()
                if self._current_project_path:
                    env = {**env, "KIPRJMOD": str(Path(self._current_project_path).parent)}
                resolved = model_path_raw
                for var, val in env.items():
                    resolved = resolved.replace(f"${{{var}}}", val)
                model_file = Path(resolved)
                if model_file.exists():
                    raw_bytes = model_file.read_bytes()
                    step_data = base64.b64encode(raw_bytes).decode("ascii")

                # Extract offset / scale / rotate from the (model …) block
                def _parse_xyz(tag: str) -> Optional[Dict[str, float]]:
                    pat = rf'\({tag}\s*\(xyz\s+([\d.eE+\-]+)\s+([\d.eE+\-]+)\s+([\d.eE+\-]+)\s*\)'
                    m = re.search(pat, sexpr)
                    if m:
                        return {"x": float(m.group(1)), "y": float(m.group(2)), "z": float(m.group(3))}
                    return None

                offset = _parse_xyz("offset")
                scale = _parse_xyz("scale")
                rotate = _parse_xyz("rotate")
                if offset or scale or rotate:
                    model_transform = {
                        "offset": offset or {"x": 0, "y": 0, "z": 0},
                        "scale": scale or {"x": 1, "y": 1, "z": 1},
                        "rotate": rotate or {"x": 0, "y": 0, "z": 0},
                    }

            result: Dict[str, Any] = {"success": True, "sexpr": sexpr}
            if step_data:
                result["step_data"] = step_data
            if model_transform:
                result["model_transform"] = model_transform
            return result
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    def importer_get_symbol_sexpr(
        self,
        library_name: str,
        symbol_name: str,
    ) -> Dict[str, Any]:
        """Read a symbol's S-expression from an existing KiCad library.

        Args:
            library_name: Library nickname (e.g. ``"Device"``).
            symbol_name: Symbol name (e.g. ``"R"``).

        Returns:
            Dict with ``success`` and ``sexpr`` (string).
        """
        try:
            from .kicad_parser.symbol_lib import SymbolLibrary
            from .kicad_parser.sexpr import serialize

            disc = self._library_index.get_discovery()
            lib_path = disc.resolve_symbol_library(library_name)
            if not lib_path:
                return {"success": False, "error": f"Library '{library_name}' not found"}
            path = Path(lib_path)
            if not path.exists():
                return {"success": False, "error": f"Library file not found: {path}"}
            sym_lib = SymbolLibrary.load(path)
            sym = sym_lib.find_by_name(symbol_name)
            if sym is None:
                return {"success": False, "error": f"Symbol '{symbol_name}' not found in '{library_name}'"}
            sexpr = serialize(sym.to_tree())
            return {"success": True, "sexpr": sexpr}
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    def importer_replace_symbol_graphics(
        self,
        imported_symbol_sexpr: str,
        library_name: str,
        symbol_name: str,
    ) -> Dict[str, Any]:
        """Replace graphical elements (not fields) in the imported symbol with
        those from an existing library symbol.

        Keeps all properties/fields from the imported symbol but replaces
        sub-symbol graphics (pins, arcs, polylines, rectangles, circles, text)
        with those from the library symbol.

        Args:
            imported_symbol_sexpr: Raw S-expression of the imported symbol.
            library_name: Library nickname containing the base symbol.
            symbol_name: Symbol name within *library_name*.

        Returns:
            Dict with ``success``, ``merged_sexpr`` (the result), and
            ``base_sexpr`` (the raw base symbol for preview).
        """
        try:
            from .kicad_parser.symbol_lib import SymbolLibrary
            from .kicad_parser.sexpr import parse, serialize, QStr

            disc = self._library_index.get_discovery()
            lib_path = disc.resolve_symbol_library(library_name)
            if not lib_path:
                return {"success": False, "error": f"Library '{library_name}' not found"}
            path = Path(lib_path)
            if not path.exists():
                return {"success": False, "error": f"Library file not found: {path}"}
            sym_lib = SymbolLibrary.load(path)
            base_sym = sym_lib.find_by_name(symbol_name)
            if base_sym is None:
                return {"success": False, "error": f"Symbol '{symbol_name}' not found in '{library_name}'"}
            base_sexpr = serialize(base_sym.to_tree())

            # Parse both symbols
            imported_tree = parse(imported_symbol_sexpr.strip())
            base_tree = parse(base_sexpr.strip())

            # Handle case where imported_tree is a full library
            if imported_tree and imported_tree[0] == "kicad_symbol_lib":
                sub_syms = [t for t in imported_tree[1:]
                            if isinstance(t, list) and t and t[0] == "symbol"]
                if sub_syms:
                    imported_tree = sub_syms[0]

            if not imported_tree or imported_tree[0] != "symbol":
                return {"success": False, "error": "Invalid imported symbol S-expression"}
            if not base_tree or base_tree[0] != "symbol":
                return {"success": False, "error": "Invalid base symbol S-expression"}

            # Extract the imported symbol's name
            imported_name = str(imported_tree[1]) if len(imported_tree) > 1 else "Component"

            # Collect property nodes from the imported symbol (these we keep)
            imported_props = [
                node for node in imported_tree
                if isinstance(node, list) and node and node[0] == "property"
            ]

            # Collect top-level non-property, non-subsymbol metadata from imported
            # (e.g. pin_names, pin_numbers, in_bom, on_board, etc.)
            _METADATA_TAGS = {
                "pin_names", "pin_numbers", "in_bom", "on_board",
                "exclude_from_sim", "power",
            }
            imported_metadata = [
                node for node in imported_tree
                if isinstance(node, list) and node and node[0] in _METADATA_TAGS
            ]

            # Collect sub-symbols (graphical units) from the BASE symbol
            base_subsyms = [
                node for node in base_tree
                if isinstance(node, list) and node and node[0] == "symbol"
            ]

            # Collect top-level metadata from the BASE symbol as fallback
            base_metadata = [
                node for node in base_tree
                if isinstance(node, list) and node and node[0] in _METADATA_TAGS
            ]

            # Rename base sub-symbols to use the imported symbol's name
            base_name = str(base_tree[1]) if len(base_tree) > 1 else ""
            for subsym in base_subsyms:
                if isinstance(subsym[1], (str, QStr)):
                    old_sub_name = str(subsym[1])
                    # Replace the base name prefix with the imported name
                    if old_sub_name.startswith(base_name):
                        new_sub_name = imported_name + old_sub_name[len(base_name):]
                        subsym[1] = QStr(new_sub_name)

            # Build the merged symbol tree
            merged = ["symbol", QStr(imported_name)]

            # Add metadata (prefer imported, fallback to base)
            metadata = imported_metadata if imported_metadata else base_metadata
            merged.extend(metadata)

            # Add properties from imported
            merged.extend(imported_props)

            # Add graphical sub-symbols from base
            merged.extend(base_subsyms)

            merged_sexpr = serialize(merged) + "\n"
            return {
                "success": True,
                "merged_sexpr": merged_sexpr,
                "base_sexpr": base_sexpr,
            }
        except Exception as exc:
            import traceback
            traceback.print_exc()
            return {"success": False, "error": str(exc)}

    def importer_import_from_kicad(
        self,
        symbol_name: str,
        library_name: str,
        target_sym_lib: str = "",
        target_fp_lib_dir: str = "",
        models_dir: str = "",
        overwrite: bool = False,
    ) -> Dict[str, Any]:
        """Clone a symbol from an existing KiCad library into a target library.

        Args:
            symbol_name: KiCad symbol name (e.g. ``"R"``).
            library_name: Source library nickname.
            target_sym_lib: Destination ``.kicad_sym`` file.
            target_fp_lib_dir: Destination ``.pretty`` directory.
            models_dir: Directory for 3-D models.
            overwrite: Replace existing entries if True.

        Returns:
            Dict with ``success``, ``component`` summary, ``warnings``, ``error``.
        """
        try:
            from .importer import import_from_symbol_lib, commit_import
            project_dir = None
            if self._current_project_path:
                project_dir = str(Path(self._current_project_path).parent)
            result = import_from_symbol_lib(symbol_name, library_name, project_dir=project_dir)
            if not result.success:
                return {"success": False, "error": result.error, "warnings": []}

            if target_sym_lib or target_fp_lib_dir:
                result = commit_import(
                    result.component,
                    target_sym_lib=target_sym_lib or None,
                    target_fp_lib_dir=target_fp_lib_dir or None,
                    models_dir=models_dir or None,
                    overwrite=overwrite,
                )

            return self._import_result_to_dict(result)
        except Exception as exc:
            return {"success": False, "error": str(exc), "warnings": []}

    def importer_add_variant(
        self,
        template_library: str,
        template_symbol: str,
        new_symbol_name: str,
        fields: Dict[str, str] = None,
        target_library: str = "",
    ) -> Dict[str, Any]:
        """Clone a template symbol and create a new variant with updated fields.

        Designed for passive component libraries (e.g. pcb-club-res,
        pcb-club-cap) where all symbols share the same graphics/pins and
        only differ in their property values (Value, MPN, DKPN, LCSC, etc.).

        Args:
            template_library: Library nickname containing the template symbol.
            template_symbol: Symbol name to clone as a template.
            new_symbol_name: Name for the new symbol variant.
            fields: Dict of property key→value to set on the new symbol.
            target_library: Library to write to (defaults to template_library).

        Returns:
            Dict with ``success``, ``name``, ``library``, ``library_path``,
            ``error``.
        """
        try:
            from .importer.kicad_lib_importer import add_variant
            project_dir = None
            if self._current_project_path:
                project_dir = str(Path(self._current_project_path).parent)
            return add_variant(
                template_library=template_library,
                template_symbol=template_symbol,
                new_symbol_name=new_symbol_name,
                fields=fields or {},
                project_dir=project_dir,
                target_library=target_library or None,
            )
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    # ------------------------------------------------------------------
    # Quick variant import (side-panel passives workflow)
    # ------------------------------------------------------------------

    def importer_quick_variant(
        self,
        component_type: str,
        mpn: str,
        target_library: str,
    ) -> Dict[str, Any]:
        """Look up an MPN and create a library variant in one step.

        Args:
            component_type: ``"resistor"``, ``"capacitor"``, ``"inductor"``, or ``"diode"``.
            mpn: Manufacturer part number.
            target_library: Library nickname to add the new symbol to.

        Returns:
            Dict with ``success``, ``name``, ``library``, ``specs``,
            ``variant_name``, ``footprint``, ``description``,
            ``manufacturer``, ``mpn``, ``error``.
        """
        try:
            from .importer.variant_importer import quick_variant_import

            project_dir = None
            if self._current_project_path:
                project_dir = str(Path(self._current_project_path).parent)

            def _push(msg: str) -> None:
                self._import_progress = msg

            return quick_variant_import(
                component_type=component_type,
                mpn=mpn,
                target_library=target_library,
                project_dir=project_dir,
                on_progress=_push,
            )
        except Exception as exc:
            return {"success": False, "error": str(exc)}
        finally:
            self._import_progress = ""

    def importer_variant_preview(
        self,
        component_type: str,
        mpn: str,
    ) -> Dict[str, Any]:
        """Preview what a quick variant import would produce without creating it.

        Args:
            component_type: ``"resistor"``, ``"capacitor"``, ``"inductor"``, or ``"diode"``.
            mpn: Manufacturer part number.

        Returns:
            Dict with ``success``, ``mpn``, ``manufacturer``, ``description``,
            ``specs``, ``footprint``, ``variant_name``, supplier PNs, ``error``.
        """
        try:
            from .importer.variant_importer import lookup_variant_preview
            return lookup_variant_preview(component_type, mpn)
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    def importer_variant_import(
        self,
        component_type: str,
        mpn: str,
        default_symbols: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Import a passive variant using the full scraping pipeline.

        Returns the same dict structure as :meth:`importer_import_by_part`
        so the frontend can render it in ImporterDetails for preview,
        editing, and save-to-library.

        Args:
            component_type: ``"resistor"``, ``"capacitor"``, ``"inductor"``, or ``"diode"``.
            mpn: Manufacturer part number.
            default_symbols: Optional per-type symbol overrides, e.g.
                ``{"resistor": {"library": "Device", "symbol": "R_Small_US"}}``.

        Returns:
            Dict with ``success``, ``component``, ``warnings``,
            ``cad_sources``, ``octopart_url``, ``error``.
        """
        try:
            from .importer.variant_importer import variant_import_by_part

            project_dir = None
            if self._current_project_path:
                project_dir = str(Path(self._current_project_path).parent)

            def _push(msg: str) -> None:
                self._import_progress = msg

            result = variant_import_by_part(
                component_type=component_type,
                mpn=mpn,
                default_symbol_overrides=default_symbols,
                project_dir=project_dir,
                on_progress=_push,
            )

            result_dict = self._import_result_to_dict(result)
            # Clear temp paths since files are in a temp dir
            if result_dict.get("success") and "component" in result_dict:
                result_dict["component"]["symbol_path"] = ""
                result_dict["component"]["footprint_path"] = ""
                result_dict["component"]["model_paths"] = []
            return result_dict
        except Exception as exc:
            return {"success": False, "error": str(exc), "warnings": []}
        finally:
            self._import_progress = ""

    def importer_open_in_kicad(self, footprint_path: str) -> Dict[str, Any]:
        """Open a footprint file in the KiCad footprint editor.

        Args:
            footprint_path: Absolute path to the ``.kicad_mod`` file.

        Returns:
            Dict with ``success`` and ``error``.
        """
        import subprocess
        import platform
        try:
            path = Path(footprint_path)
            if not path.exists():
                return {"success": False, "error": f"File not found: {footprint_path}"}
            if path.suffix.lower() != ".kicad_mod":
                return {"success": False, "error": "Only .kicad_mod files can be opened"}

            system = platform.system()
            if system == "Windows":
                os.startfile(str(path))  # type: ignore[attr-defined]
            elif system == "Darwin":
                subprocess.Popen(["open", "-a", "KiCad", str(path)])
            else:
                # Linux — try pcbnew first, then xdg-open
                kicad_fp_editor = "pcbnew"
                if shutil.which(kicad_fp_editor):
                    subprocess.Popen([kicad_fp_editor, str(path)])
                else:
                    subprocess.Popen(["xdg-open", str(path)])

            return {"success": True}
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    _IMPORT_FILE_TYPES = (
        "Supported files (*.zip;*.kicad_sym;*.lib;*.kicad_mod;*.mod;*.step;*.stp;*.wrl)",
        "ZIP archives (*.zip)",
        "KiCad symbols (*.kicad_sym;*.lib)",
        "KiCad footprints (*.kicad_mod;*.mod)",
        "3D models (*.step;*.stp;*.wrl)",
        "All files (*.*)",
    )

    def importer_browse_zip(self) -> Dict[str, Any]:
        """Open a file-chooser dialog for file selection.

        Accepts ZIP archives as well as raw ``.kicad_sym``, ``.kicad_mod``,
        ``.step``, ``.stp``, ``.wrl`` files.

        Returns:
            Dict with ``success`` and ``path`` (empty string if cancelled).
        """
        try:
            import webview
            windows = webview.windows
            if not windows:
                return {"success": False, "error": "No webview window available"}
            result = windows[0].create_file_dialog(
                webview.OPEN_DIALOG,
                allow_multiple=False,
                file_types=self._IMPORT_FILE_TYPES,
            )
            if result:
                return {"success": True, "path": result[0]}
            return {"success": True, "path": ""}
        except Exception as exc:
            return {"success": False, "error": str(exc), "path": ""}

    def importer_browse_zips(self) -> Dict[str, Any]:
        """Open a file-chooser dialog for selecting multiple files.

        Accepts ZIP archives as well as raw ``.kicad_sym``, ``.kicad_mod``,
        ``.step``, ``.stp``, ``.wrl`` files.

        Returns:
            Dict with ``success`` and ``paths`` (list of selected file paths,
            empty list if cancelled).
        """
        try:
            import webview
            windows = webview.windows
            if not windows:
                return {"success": False, "error": "No webview window available"}
            result = windows[0].create_file_dialog(
                webview.OPEN_DIALOG,
                allow_multiple=True,
                file_types=self._IMPORT_FILE_TYPES,
            )
            if result:
                return {"success": True, "paths": list(result)}
            return {"success": True, "paths": []}
        except Exception as exc:
            return {"success": False, "error": str(exc), "paths": []}

    def importer_import_zips(
        self,
        zip_paths: list,
        target_sym_lib: str = "",
        target_fp_lib_dir: str = "",
        models_dir: str = "",
        overwrite: bool = False,
        override_fields: dict = None,
    ) -> Dict[str, Any]:
        """Import and merge components from multiple files.

        Accepts ZIP archives as well as individual raw files:
        ``.kicad_sym``, ``.lib``, ``.kicad_mod``, ``.mod``,
        ``.step``, ``.stp``, ``.wrl``.

        Each file may contribute a symbol, footprint, 3-D model, or metadata.
        Results are merged into a single :class:`ImportedComponent`: later
        files override earlier ones (last-wins).

        When *override_fields* is provided (e.g. from a prior Octopart/part
        lookup), those field values are overlaid **on top** of the file-derived
        fields — so part-lookup metadata (MPN, manufacturer, DKPN, datasheet,
        etc.) takes priority over whatever the file metadata contained.

        Args:
            zip_paths: List of absolute paths to ``.zip`` or raw KiCad/STEP files.
            target_sym_lib: Destination ``.kicad_sym`` file.
            target_fp_lib_dir: Destination ``.pretty`` directory.
            models_dir: Directory for 3-D model files.
            overwrite: Replace existing entries if True.
            override_fields: Dict of field values from a prior part lookup
                to overlay onto the imported component.  Keys match
                :class:`FieldSet` attributes (``mpn``, ``manufacturer``,
                ``digikey_pn``, ``mouser_pn``, ``lcsc_pn``, ``value``,
                ``reference``, ``footprint``, ``datasheet``, ``description``,
                ``package``).  An ``extra`` sub-dict is also supported.

        Returns:
            Dict with ``success``, ``component`` summary, ``warnings``, ``error``.
        """
        if not zip_paths:
            return {"success": False, "error": "No files specified", "warnings": []}

        try:
            from .importer import import_zip, import_raw_file, RAW_FILE_EXTS, commit_import
            from .importer.models import ImportedComponent, FieldSet, ImportMethod

            all_warnings: list[str] = []
            merged_component: ImportedComponent | None = None

            with tempfile.TemporaryDirectory(prefix="kiassist_multzip_") as tmp_dir:
                for i, zp in enumerate(zip_paths):
                    sub_dir = os.path.join(tmp_dir, f"file_{i}")
                    os.makedirs(sub_dir, exist_ok=True)

                    ext = os.path.splitext(zp)[1].lower()
                    if ext == ".zip":
                        result = import_zip(zp, output_dir=sub_dir)
                    elif ext in RAW_FILE_EXTS:
                        result = import_raw_file(zp, output_dir=sub_dir)
                    else:
                        all_warnings.append(
                            f"Skipped unsupported file type '{ext}': {os.path.basename(zp)}"
                        )
                        continue
                    all_warnings.extend(result.warnings)

                    if not result.success:
                        all_warnings.append(f"{os.path.basename(zp)}: {result.error}")
                        continue

                    comp = result.component
                    if merged_component is None:
                        # First successful ZIP becomes the base
                        merged_component = comp
                        merged_component.source_info = ", ".join(
                            os.path.basename(p) for p in zip_paths
                        )
                    else:
                        # Merge: later ZIPs override earlier ones.
                        # If the new ZIP has a symbol/footprint/3D, it
                        # replaces what was loaded before.
                        if comp.symbol_sexpr:
                            merged_component.symbol_sexpr = comp.symbol_sexpr
                            merged_component.symbol_path = comp.symbol_path
                        if comp.footprint_sexpr:
                            merged_component.footprint_sexpr = comp.footprint_sexpr
                            merged_component.footprint_path = comp.footprint_path
                        # Later 3-D models replace earlier ones
                        if comp.model_paths:
                            merged_component.model_paths = list(comp.model_paths)
                        # Merge fields: later non-empty values override earlier
                        mf = merged_component.fields
                        cf = comp.fields
                        for attr in (
                            "mpn", "manufacturer", "digikey_pn", "mouser_pn",
                            "lcsc_pn", "value", "reference", "footprint",
                            "datasheet", "description", "package",
                        ):
                            if getattr(cf, attr):
                                setattr(mf, attr, getattr(cf, attr))
                        for k, v in cf.extra.items():
                            if v:
                                mf.extra[k] = v

                if merged_component is None:
                    return {
                        "success": False,
                        "error": "No valid components found in the provided files",
                        "warnings": all_warnings,
                    }

                # Overlay part-lookup fields onto the ZIP-imported component.
                # Non-empty override values win over ZIP metadata so that
                # Octopart-sourced MPN, manufacturer, DKPN, datasheet, etc.
                # are preserved while the ZIP supplies symbol/footprint/3D.
                if override_fields:
                    mf = merged_component.fields
                    _FIELD_ATTRS = (
                        "mpn", "manufacturer", "digikey_pn", "mouser_pn",
                        "lcsc_pn", "value", "reference", "footprint",
                        "datasheet", "description", "package",
                    )
                    for attr in _FIELD_ATTRS:
                        override_val = override_fields.get(attr, "")
                        if override_val:
                            setattr(mf, attr, override_val)
                    # Merge extra fields
                    extra_override = override_fields.get("extra")
                    if isinstance(extra_override, dict):
                        for k, v in extra_override.items():
                            if v:
                                mf.extra[k] = v
                    # Update component name to match the overridden MPN
                    if mf.mpn:
                        merged_component.name = mf.mpn

                # Build the ImportResult wrapper
                from .importer.models import ImportResult
                merged_result = ImportResult(
                    success=True,
                    component=merged_component,
                    warnings=all_warnings,
                )

                if target_sym_lib or target_fp_lib_dir:
                    merged_result = commit_import(
                        merged_component,
                        target_sym_lib=target_sym_lib or None,
                        target_fp_lib_dir=target_fp_lib_dir or None,
                        models_dir=models_dir or None,
                        overwrite=overwrite,
                    )
                    return self._import_result_to_dict(merged_result)
                else:
                    result_dict = self._import_result_to_dict(merged_result)
                    if result_dict.get("success") and "component" in result_dict:
                        result_dict["component"]["symbol_path"] = ""
                        result_dict["component"]["footprint_path"] = ""
                        result_dict["component"]["model_paths"] = []
                    return result_dict
        except Exception as exc:
            return {"success": False, "error": str(exc), "warnings": []}

    def importer_save_dropped_zips(self, files: list) -> Dict[str, Any]:
        """Save base64-encoded files dropped via drag-and-drop.

        Accepts ZIP archives as well as raw ``.kicad_sym``, ``.kicad_mod``,
        ``.step``, ``.stp``, ``.wrl`` files.

        Each entry in *files* should be a dict with ``name`` (filename)
        and ``data`` (base64-encoded bytes).

        Returns:
            Dict with ``success`` and ``paths`` — absolute temp file paths
            that the frontend can feed into ``importer_import_zips``.
        """
        import base64

        if not files:
            return {"success": False, "error": "No files provided"}

        saved_paths: list[str] = []
        try:
            drop_dir = tempfile.mkdtemp(prefix="kiassist_drop_")
            for entry in files:
                name = entry.get("name", "dropped_file")
                data_b64 = entry.get("data", "")
                if not data_b64:
                    continue
                raw = base64.b64decode(data_b64)
                out = os.path.join(drop_dir, name)
                with open(out, "wb") as f:
                    f.write(raw)
                saved_paths.append(out)
            return {"success": True, "paths": saved_paths}
        except Exception as exc:
            return {"success": False, "error": str(exc), "paths": []}

    # Alias so the frontend can call either name
    importer_save_dropped_files = importer_save_dropped_zips

    def importer_browse_output_dir(self) -> Dict[str, Any]:
        """Open a folder-chooser dialog for the output library directory.

        Returns:
            Dict with ``success`` and ``path``.
        """
        try:
            import webview
            windows = webview.windows
            if not windows:
                return {"success": False, "error": "No webview window available"}
            result = windows[0].create_file_dialog(webview.FOLDER_DIALOG)
            if result:
                return {"success": True, "path": result[0]}
            return {"success": True, "path": ""}
        except Exception as exc:
            return {"success": False, "error": str(exc), "path": ""}

    def importer_ai_suggest_symbol(
        self,
        mpn: str = "",
        manufacturer: str = "",
        description: str = "",
        package: str = "",
        symbol_sexpr: str = "",
    ) -> Dict[str, Any]:
        """Use the AI to suggest the best matching KiCad native symbol as a template.

        The AI analyses the component metadata and pin topology and recommends
        up to 5 KiCad built-in symbols that could serve as a visual base.

        Args:
            mpn: Manufacturer part number.
            manufacturer: Manufacturer name.
            description: Component description.
            package: Package / footprint identifier (e.g. ``"DIP-8"``).
            symbol_sexpr: Optional imported symbol S-expression (used to
                extract the pin count/summary for the prompt).

        Returns:
            Dict with ``success`` and ``suggestions`` list.  Each suggestion
            has ``library``, ``name``, ``reason``, ``confidence`` keys.
        """
        try:
            from .importer.ai_symbol import (
                suggest_symbol,
                extract_pins_from_symbol,
            )

            # Gather available library names for the prompt
            disc = self._library_index.get_discovery()
            try:
                lib_names = [e.nickname for e in disc.list_symbol_libraries()]
            except Exception:
                lib_names = []

            # Build a brief pin summary
            pin_summary = ""
            if symbol_sexpr:
                pins = extract_pins_from_symbol(symbol_sexpr)
                if pins:
                    _PIN_SUMMARY_LIMIT = 8
                    unique_types = sorted({p["type"] for p in pins})
                    pin_summary = (
                        f"{len(pins)} pins: "
                        + ", ".join(f'"{p["number"]}"/"{p["name"]}"' for p in pins[:_PIN_SUMMARY_LIMIT])
                        + (" ..." if len(pins) > _PIN_SUMMARY_LIMIT else "")
                        + f" (types: {', '.join(unique_types)})"
                    )

            def _call_ai(system: str, user: str) -> str:
                provider = self._get_or_create_provider()
                if not provider:
                    raise RuntimeError("No AI provider configured")
                from .ai.base import AIMessage
                msgs = [AIMessage(role="user", content=user)]
                resp = provider.chat(msgs, system_prompt=system)
                return resp.content

            suggestions, _ = suggest_symbol(
                mpn=mpn,
                manufacturer=manufacturer,
                description=description,
                package=package,
                pin_summary=pin_summary,
                available_libraries=lib_names,
                call_ai=_call_ai,
            )
            return {
                "success": True,
                "suggestions": [
                    {
                        "library": s.library,
                        "name": s.name,
                        "reason": s.reason,
                        "confidence": s.confidence,
                    }
                    for s in suggestions
                ],
            }
        except Exception as exc:
            return {"success": False, "error": str(exc), "suggestions": []}

    def importer_ai_map_pins(
        self,
        mpn: str = "",
        imported_symbol_sexpr: str = "",
        base_library: str = "",
        base_symbol_name: str = "",
    ) -> Dict[str, Any]:
        """Use the AI to map imported component pins onto a KiCad base symbol's pins.

        Analyses both pin lists and produces a mapping table.  The resulting
        mapped symbol S-expression has the base symbol's graphics with the
        imported pin numbers applied.

        Args:
            mpn: Imported component MPN (for AI context).
            imported_symbol_sexpr: Raw S-expression of the imported symbol.
            base_library: KiCad library nickname containing the base symbol.
            base_symbol_name: Symbol name within *base_library*.

        Returns:
            Dict with ``success``, ``mapping`` dict, ``merged_symbol_sexpr``,
            ``notes``, and ``warnings`` list.
        """
        try:
            from .importer.ai_symbol import (
                map_pins,
                extract_pins_from_symbol,
                apply_pin_mapping,
            )
            from .kicad_parser.symbol_lib import SymbolLibrary
            from .kicad_parser.sexpr import serialize

            # Load base symbol
            disc = self._library_index.get_discovery()
            base_lib_path = disc.resolve_symbol_library(base_library)
            if not base_lib_path:
                return {
                    "success": False,
                    "error": f"Library '{base_library}' not found",
                }
            sym_lib = SymbolLibrary.load(base_lib_path)
            base_sym = sym_lib.find_by_name(base_symbol_name)
            if base_sym is None:
                return {
                    "success": False,
                    "error": f"Symbol '{base_symbol_name}' not found in '{base_library}'",
                }
            base_sym_sexpr = serialize(base_sym.to_tree())

            # Extract pins
            if not imported_symbol_sexpr:
                return {
                    "success": False,
                    "error": (
                        "No imported symbol S-expression provided. "
                        "Pass the symbol_sexpr returned by the import call."
                    ),
                }
            imported_pins = extract_pins_from_symbol(imported_symbol_sexpr)
            if not imported_pins:
                return {
                    "success": False,
                    "error": "No pins found in imported symbol S-expression.",
                }
            base_pins = extract_pins_from_symbol(base_sym_sexpr)

            if not base_pins:
                return {
                    "success": False,
                    "error": "No pins found in base symbol",
                }

            def _call_ai(system: str, user: str) -> str:
                provider = self._get_or_create_provider()
                if not provider:
                    raise RuntimeError("No AI provider configured")
                from .ai.base import AIMessage
                msgs = [AIMessage(role="user", content=user)]
                resp = provider.chat(msgs, system_prompt=system)
                return resp.content

            pin_mapping, _ = map_pins(
                mpn=mpn,
                imported_pins=imported_pins,
                base_symbol_lib=base_library,
                base_symbol_name=base_symbol_name,
                base_pins=base_pins,
                call_ai=_call_ai,
            )

            # Apply the mapping to produce a merged symbol S-expression
            merged_sexpr = apply_pin_mapping(base_sym_sexpr, pin_mapping)

            return {
                "success": True,
                "mapping": pin_mapping.mapping,
                "merged_symbol_sexpr": merged_sexpr,
                "notes": pin_mapping.notes,
                "warnings": pin_mapping.warnings,
                "imported_pins": imported_pins,
                "base_pins": base_pins,
            }
        except Exception as exc:
            return {"success": False, "error": str(exc), "mapping": {}}

    def importer_ai_generate_symbol(
        self,
        mpn: str = "",
        manufacturer: str = "",
        description: str = "",
        package: str = "",
        reference: str = "U",
        datasheet: str = "",
        pins: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """Ask the AI to generate a KiCad 6 symbol S-expression from scratch.

        Useful when no base symbol is suitable and the imported auto-symbol
        is unsatisfactory.

        Args:
            mpn: Manufacturer part number.
            manufacturer: Manufacturer name.
            description: Part description.
            package: Package (e.g. ``"DIP-8"``).
            reference: Default reference designator prefix (e.g. ``"U"``).
            datasheet: Datasheet URL.
            pins: Optional list of ``{"number", "name", "direction"}`` dicts.
                If omitted the AI generates a best-guess pin list.

        Returns:
            Dict with ``success`` and ``symbol_sexpr``.
        """
        try:
            from .importer.ai_symbol import generate_symbol

            def _call_ai(system: str, user: str) -> str:
                provider = self._get_or_create_provider()
                if not provider:
                    raise RuntimeError("No AI provider configured")
                from .ai.base import AIMessage
                msgs = [AIMessage(role="user", content=user)]
                resp = provider.chat(msgs, system_prompt=system)
                return resp.content

            sym_sexpr, raw = generate_symbol(
                mpn=mpn,
                manufacturer=manufacturer,
                description=description,
                package=package,
                reference=reference,
                datasheet=datasheet,
                pins=pins or [],
                call_ai=_call_ai,
            )
            if not sym_sexpr:
                return {
                    "success": False,
                    "error": "AI did not produce a valid symbol S-expression",
                    "raw_response": raw[:500] if raw else "",
                }
            return {"success": True, "symbol_sexpr": sym_sexpr}
        except Exception as exc:
            return {"success": False, "error": str(exc), "symbol_sexpr": ""}

    def importer_write_symbol_sexpr(
        self,
        symbol_sexpr: str,
        component_name: str,
        target_sym_lib: str = "",
        overwrite: bool = False,
    ) -> Dict[str, Any]:
        """Write an arbitrary symbol S-expression (e.g. AI-merged result) to a library.

        Used by the frontend after the AI produces a merged or generated symbol
        so the user can commit it without re-running the full import pipeline.

        Args:
            symbol_sexpr: Raw KiCad 6 symbol S-expression.
            component_name: Desired symbol name in the target library.
            target_sym_lib: Destination ``.kicad_sym`` file path.
            overwrite: Replace an existing entry with the same name if True.

        Returns:
            Dict with ``success``, ``name`` (final symbol name in library).
        """
        if not symbol_sexpr.strip():
            return {"success": False, "error": "Empty symbol S-expression"}
        if not target_sym_lib.strip():
            return {"success": False, "error": "No target symbol library specified"}
        try:
            from .importer.models import ImportedComponent
            from .importer.library_writer import write_symbol_to_library

            comp = ImportedComponent(name=component_name, symbol_sexpr=symbol_sexpr)
            ok, final_name = write_symbol_to_library(comp, target_sym_lib, overwrite=overwrite)
            if ok:
                return {"success": True, "name": final_name, "path": target_sym_lib}
            return {"success": False, "error": f"Symbol write failed: {final_name}"}
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    def importer_save_generated_symbol(
        self,
        symbol_sexpr: str,
        component_name: str,
        target_sym_lib: str = "",
        overwrite: bool = False,
    ) -> Dict[str, Any]:
        """Save an AI-generated symbol S-expression to the target library.

        Delegates to :meth:`importer_write_symbol_sexpr`.  AI-generated symbols
        contain only schematic graphics; a separate footprint write is not
        performed here (the user can assign a footprint in KiCad after import).

        Args:
            symbol_sexpr: Raw KiCad 6 symbol S-expression produced by the AI.
            component_name: Desired symbol name.
            target_sym_lib: Destination ``.kicad_sym`` file path.
            overwrite: Replace existing entry if True.

        Returns:
            Dict with ``success``, ``name``, ``symbol_path``.
        """
        return self.importer_write_symbol_sexpr(
            symbol_sexpr=symbol_sexpr,
            component_name=component_name,
            target_sym_lib=target_sym_lib,
            overwrite=overwrite,
        )

    # ------------------------------------------------------------------
    # Library save defaults (persisted)
    # ------------------------------------------------------------------

    def importer_get_library_defaults(self) -> Dict[str, Any]:
        """Return the user's last-used library paths.

        Returns:
            Dict with ``success``, ``sym_lib``, ``fp_lib``, ``models_dir``.
        """
        config = self._get_config()
        return {
            "success": True,
            "sym_lib": config.get(self._CFG_LAST_SYM_LIB, ""),
            "fp_lib": config.get(self._CFG_LAST_FP_LIB, ""),
            "models_dir": config.get(self._CFG_LAST_MODELS_DIR, ""),
        }

    def importer_set_library_defaults(
        self,
        sym_lib: str = "",
        fp_lib: str = "",
        models_dir: str = "",
    ) -> Dict[str, Any]:
        """Persist the user's last-used library paths.

        Args:
            sym_lib: Symbol library nickname or path.
            fp_lib: Footprint library nickname or path.
            models_dir: 3D models directory path.

        Returns:
            Dict with ``success``.
        """
        try:
            if sym_lib:
                self._save_config_field(self._CFG_LAST_SYM_LIB, sym_lib)
            if fp_lib:
                self._save_config_field(self._CFG_LAST_FP_LIB, fp_lib)
            if models_dir:
                self._save_config_field(self._CFG_LAST_MODELS_DIR, models_dir)
            return {"success": True}
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    def importer_browse_sym_library(self) -> Dict[str, Any]:
        """Open a file-chooser dialog for selecting a symbol library file.

        Returns:
            Dict with ``success`` and ``path``.
        """
        try:
            import webview
            windows = webview.windows
            if not windows:
                return {"success": False, "error": "No webview window available"}
            result = windows[0].create_file_dialog(
                webview.OPEN_DIALOG,
                file_types=("KiCad Symbol Library (*.kicad_sym)",),
            )
            if result:
                return {"success": True, "path": result[0]}
            return {"success": True, "path": ""}
        except Exception as exc:
            return {"success": False, "error": str(exc), "path": ""}

    def importer_browse_fp_library(self) -> Dict[str, Any]:
        """Open a folder-chooser dialog for selecting a footprint library directory.

        Returns:
            Dict with ``success`` and ``path``.
        """
        try:
            import webview
            windows = webview.windows
            if not windows:
                return {"success": False, "error": "No webview window available"}
            result = windows[0].create_file_dialog(webview.FOLDER_DIALOG)
            if result:
                return {"success": True, "path": result[0]}
            return {"success": True, "path": ""}
        except Exception as exc:
            return {"success": False, "error": str(exc), "path": ""}

    def importer_browse_models_dir(self) -> Dict[str, Any]:
        """Open a folder-chooser dialog for selecting a 3D models directory.

        Returns:
            Dict with ``success`` and ``path``.
        """
        try:
            import webview
            windows = webview.windows
            if not windows:
                return {"success": False, "error": "No webview window available"}
            result = windows[0].create_file_dialog(webview.FOLDER_DIALOG)
            if result:
                return {"success": True, "path": result[0]}
            return {"success": True, "path": ""}
        except Exception as exc:
            return {"success": False, "error": str(exc), "path": ""}

    def importer_resolve_library_path(
        self, nickname: str, lib_type: str = "sym"
    ) -> Dict[str, Any]:
        """Resolve a library nickname to an absolute path.

        Args:
            nickname: Library nickname (e.g. ``"Device"``).
            lib_type: ``"sym"`` for symbol libraries, ``"fp"`` for footprint.

        Returns:
            Dict with ``success``, ``path`` (absolute), ``uri`` (original with variables).
        """
        try:
            disc = self._library_index.get_discovery()
            if lib_type == "fp":
                entries = disc.list_footprint_libraries()
            else:
                entries = disc.list_symbol_libraries()
            for entry in entries:
                if entry.nickname == nickname:
                    project_dir = str(Path(self._current_project_path).parent) if self._current_project_path else None
                    env = {"KIPRJMOD": project_dir} if project_dir else None
                    resolved = entry.resolved_path(env=env)
                    return {
                        "success": True,
                        "path": str(resolved) if resolved else "",
                        "uri": entry.uri,
                        "nickname": nickname,
                    }
            return {"success": False, "error": f"Library '{nickname}' not found"}
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    def importer_check_library_existing(
        self,
        component_name: str,
        sym_lib_nickname: str = "",
        sym_lib_path: str = "",
        fp_lib_nickname: str = "",
        fp_lib_path: str = "",
        models_dir: str = "",
        has_symbol: bool = False,
        has_footprint: bool = False,
        has_model: bool = False,
    ) -> Dict[str, Any]:
        """Check whether a symbol, footprint, or 3D model already exists in the
        target libraries.

        Called before save to determine if an overwrite confirmation dialog
        is needed.

        Args:
            component_name: The component name to check.
            sym_lib_nickname: Symbol library nickname.
            sym_lib_path: Direct path to .kicad_sym file.
            fp_lib_nickname: Footprint library nickname.
            fp_lib_path: Direct path to .pretty directory.
            models_dir: Directory for 3D models.
            has_symbol: Whether the component has a symbol to save.
            has_footprint: Whether the component has a footprint to save.
            has_model: Whether the component has a 3D model to save.

        Returns:
            Dict with ``success``, ``symbol_exists``, ``footprint_exists``,
            ``model_exists`` booleans, and ``symbol_name``, ``footprint_name``,
            ``model_name`` strings for display.
        """
        try:
            from .kicad_parser.symbol_lib import SymbolLibrary
            from .importer.library_writer import _safe_sym_name

            project_dir = None
            if self._current_project_path:
                project_dir = str(Path(self._current_project_path).parent)
            disc = self._library_index.get_discovery()
            env = {"KIPRJMOD": project_dir} if project_dir else None

            safe_name = _safe_sym_name(component_name)

            result: Dict[str, Any] = {
                "success": True,
                "symbol_exists": False,
                "footprint_exists": False,
                "model_exists": False,
                "symbol_name": safe_name,
                "footprint_name": safe_name,
                "model_name": safe_name.replace(" ", "_") + ".step",
            }

            # Resolve symbol library path
            resolved_sym_path = sym_lib_path
            if not resolved_sym_path and sym_lib_nickname:
                for entry in disc.list_symbol_libraries():
                    if entry.nickname == sym_lib_nickname:
                        p = entry.resolved_path(env=env)
                        if p:
                            resolved_sym_path = str(p)
                        break

            # Check symbol existence
            if has_symbol and resolved_sym_path:
                sym_path = Path(resolved_sym_path)
                if sym_path.exists():
                    try:
                        lib = SymbolLibrary.load(sym_path)
                        if lib.find_by_name(safe_name) is not None:
                            result["symbol_exists"] = True
                    except Exception:
                        pass

            # Resolve footprint library path
            resolved_fp_path = fp_lib_path
            if not resolved_fp_path and fp_lib_nickname:
                for entry in disc.list_footprint_libraries():
                    if entry.nickname == fp_lib_nickname:
                        p = entry.resolved_path(env=env)
                        if p:
                            resolved_fp_path = str(p)
                        break

            # Check footprint existence
            if has_footprint and resolved_fp_path:
                fp_dir = Path(resolved_fp_path)
                fp_file = fp_dir / f"{safe_name}.kicad_mod"
                if fp_file.exists():
                    result["footprint_exists"] = True

            # Check 3D model existence
            if has_model and resolved_fp_path:
                if models_dir:
                    m_dir = Path(models_dir)
                else:
                    m_dir = Path(resolved_fp_path) / "3dmodels"
                model_file = m_dir / (safe_name.replace(" ", "_") + ".step")
                if model_file.exists():
                    result["model_exists"] = True

            return result
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    def importer_commit_to_library(
        self,
        component_data: dict,
        sym_lib_nickname: str = "",
        sym_lib_path: str = "",
        fp_lib_nickname: str = "",
        fp_lib_path: str = "",
        models_dir: str = "",
        overwrite: bool = False,
        overwrite_symbol: bool = False,
        overwrite_footprint: bool = False,
        overwrite_model: bool = False,
        skip_symbol: bool = False,
        skip_footprint: bool = False,
        skip_model: bool = False,
        save_as_default: bool = True,
    ) -> Dict[str, Any]:
        """Save an imported component to symbol and footprint libraries.

        Resolves library nicknames to paths, writes the symbol/footprint/3D
        model files, links them together, and optionally persists the
        library selections as defaults.

        Args:
            component_data: Dict representation of the ImportedComponent
                (as returned by ``_import_result_to_dict``).
            sym_lib_nickname: Symbol library nickname (resolved via lib-table).
            sym_lib_path: Direct path to ``.kicad_sym`` file (overrides nickname).
            fp_lib_nickname: Footprint library nickname (resolved via lib-table).
            fp_lib_path: Direct path to ``.pretty`` directory (overrides nickname).
            models_dir: Directory for 3D models (defaults to fp_lib/3dmodels).
            overwrite: Replace all existing entries if True (legacy single flag).
            overwrite_symbol: Replace existing symbol if True.
            overwrite_footprint: Replace existing footprint if True.
            overwrite_model: Replace existing 3D model if True.
            skip_symbol: If True, do not write the symbol at all (ignore).
            skip_footprint: If True, do not write the footprint at all (ignore).
            skip_model: If True, do not copy the 3D model at all (ignore).
            save_as_default: Persist these library selections for next time.

        Returns:
            Dict with ``success``, ``warnings``, ``symbol_name``,
            ``symbol_path``, ``footprint_path``, ``model_paths``,
            ``footprint_ref`` (lib:name for linking).
        """
        try:
            from .importer.models import ImportedComponent, FieldSet, ImportMethod
            from .importer.library_writer import (
                write_symbol_to_library,
                write_footprint_to_library,
                _apply_fields_to_symbol,
            )

            warnings: list[str] = []

            # Resolve library paths from nicknames
            project_dir = None
            if self._current_project_path:
                project_dir = str(Path(self._current_project_path).parent)

            disc = self._library_index.get_discovery()
            env = {"KIPRJMOD": project_dir} if project_dir else None

            # Symbol library path resolution
            resolved_sym_path = sym_lib_path
            sym_nickname_used = sym_lib_nickname
            if not resolved_sym_path and sym_lib_nickname:
                for entry in disc.list_symbol_libraries():
                    if entry.nickname == sym_lib_nickname:
                        p = entry.resolved_path(env=env)
                        if p:
                            resolved_sym_path = str(p)
                        else:
                            # Use the URI as-is for unresolvable entries
                            resolved_sym_path = entry.uri
                        break
                if not resolved_sym_path:
                    return {"success": False, "error": f"Symbol library '{sym_lib_nickname}' not found"}

            # Footprint library path resolution
            resolved_fp_path = fp_lib_path
            fp_nickname_used = fp_lib_nickname
            if not resolved_fp_path and fp_lib_nickname:
                for entry in disc.list_footprint_libraries():
                    if entry.nickname == fp_lib_nickname:
                        p = entry.resolved_path(env=env)
                        if p:
                            resolved_fp_path = str(p)
                        else:
                            resolved_fp_path = entry.uri
                        break
                if not resolved_fp_path:
                    return {"success": False, "error": f"Footprint library '{fp_lib_nickname}' not found"}

            # Reconstruct ImportedComponent from the dict
            cd = component_data
            fields = cd.get("fields", {})
            fs = FieldSet(
                mpn=fields.get("mpn", ""),
                manufacturer=fields.get("manufacturer", ""),
                digikey_pn=fields.get("digikey_pn", ""),
                mouser_pn=fields.get("mouser_pn", ""),
                lcsc_pn=fields.get("lcsc_pn", ""),
                value=fields.get("value", ""),
                reference=fields.get("reference", ""),
                footprint=fields.get("footprint", ""),
                datasheet=fields.get("datasheet", ""),
                description=fields.get("description", ""),
                package=fields.get("package", ""),
                extra=fields.get("extra", {}),
            )

            import base64
            step_data_b64 = cd.get("step_data", "")
            step_data = base64.b64decode(step_data_b64) if step_data_b64 else None

            comp = ImportedComponent(
                name=cd.get("name", "Component"),
                fields=fs,
                symbol_sexpr=cd.get("symbol_sexpr", ""),
                footprint_sexpr=cd.get("footprint_sexpr", ""),
                step_data=step_data,
                import_method=ImportMethod(cd.get("import_method", "zip")),
                source_info=cd.get("source_info", ""),
            )

            # Handle 3D model files from step_data
            import tempfile
            model_tmp_dir = None
            if comp.step_data:
                model_tmp_dir = tempfile.mkdtemp(prefix="kiassist_models_")
                model_name = comp.name.replace(" ", "_") + ".step"
                model_path = Path(model_tmp_dir) / model_name
                model_path.write_bytes(comp.step_data)
                comp.model_paths = [model_path]

            result_data: Dict[str, Any] = {
                "success": True,
                "warnings": warnings,
                "symbol_name": "",
                "symbol_path": "",
                "footprint_path": "",
                "model_paths": [],
                "footprint_ref": "",
            }

            # Write footprint FIRST so we can build the correct footprint reference
            fp_ref = ""
            # If the component already has a footprint reference (e.g. user
            # selected a pre-existing library footprint), use it directly
            # without writing/copying anything.
            if comp.fields.footprint and not comp.footprint_sexpr:
                fp_ref = comp.fields.footprint
                result_data["footprint_ref"] = fp_ref
            eff_overwrite_fp = overwrite or overwrite_footprint
            eff_overwrite_model = overwrite or overwrite_model
            if resolved_fp_path and comp.footprint_sexpr and not skip_footprint:
                try:
                    ok, fp_file_path, copied_models = write_footprint_to_library(
                        comp,
                        resolved_fp_path,
                        models_dir=models_dir or None,
                        overwrite=eff_overwrite_fp,
                        overwrite_models=eff_overwrite_model,
                        skip_models=skip_model,
                    )
                    if ok:
                        result_data["footprint_path"] = fp_file_path
                        result_data["model_paths"] = [str(p) for p in copied_models]
                        # Build the KiCad footprint reference (libraryname:footprintname)
                        fp_stem = Path(fp_file_path).stem
                        if fp_nickname_used:
                            fp_ref = f"{fp_nickname_used}:{fp_stem}"
                        else:
                            fp_ref = f"{Path(resolved_fp_path).stem}:{fp_stem}"
                        result_data["footprint_ref"] = fp_ref
                        # Update the component fields to link footprint
                        comp.fields.footprint = fp_ref
                    else:
                        warnings.append(f"Footprint write failed: {fp_file_path}")
                except Exception as exc:
                    warnings.append(f"Footprint write error: {exc}")

            # Write symbol (with updated footprint link)
            eff_overwrite_sym = overwrite or overwrite_symbol
            if resolved_sym_path and comp.symbol_sexpr and not skip_symbol:
                try:
                    ok, sym_name = write_symbol_to_library(
                        comp, resolved_sym_path, overwrite=eff_overwrite_sym
                    )
                    if ok:
                        result_data["symbol_name"] = sym_name
                        result_data["symbol_path"] = resolved_sym_path
                    else:
                        warnings.append(f"Symbol write failed: {sym_name}")
                except Exception as exc:
                    warnings.append(f"Symbol write error: {exc}")

            result_data["warnings"] = warnings

            # Persist library selections as defaults
            if save_as_default:
                self._save_config_field(
                    self._CFG_LAST_SYM_LIB,
                    sym_lib_nickname or sym_lib_path,
                )
                self._save_config_field(
                    self._CFG_LAST_FP_LIB,
                    fp_lib_nickname or fp_lib_path,
                )
                if models_dir:
                    self._save_config_field(self._CFG_LAST_MODELS_DIR, models_dir)

            return result_data

        except Exception as exc:
            import traceback
            traceback.print_exc()
            return {"success": False, "error": str(exc), "warnings": []}

    @staticmethod
    def _import_result_to_dict(result) -> Dict[str, Any]:
        """Serialise an :class:`ImportResult` for JSON transport."""
        if not result.success:
            return {"success": False, "error": result.error, "warnings": result.warnings}
        comp = result.component
        fields = comp.fields
        data: Dict[str, Any] = {
            "success": True,
            "warnings": result.warnings,
            "component": {
                "name": comp.name,
                "import_method": comp.import_method.value if comp.import_method else "",
                "source_info": comp.source_info,
                "symbol_path": str(comp.symbol_path) if comp.symbol_path else "",
                "footprint_path": str(comp.footprint_path) if comp.footprint_path else "",
                "model_paths": [str(p) for p in comp.model_paths],
                # Include S-expressions so the frontend can pass them to AI tools
                "symbol_sexpr": comp.symbol_sexpr,
                "footprint_sexpr": comp.footprint_sexpr,
                "step_data": base64.b64encode(comp.step_data).decode("ascii") if comp.step_data else "",
                "fields": {
                    "mpn": fields.mpn,
                    "manufacturer": fields.manufacturer,
                    "digikey_pn": fields.digikey_pn,
                    "mouser_pn": fields.mouser_pn,
                    "lcsc_pn": fields.lcsc_pn,
                    "value": fields.value,
                    "reference": fields.reference,
                    "footprint": fields.footprint,
                    "datasheet": fields.datasheet,
                    "description": fields.description,
                    "package": fields.package,
                    "extra": fields.extra,
                },
            },
        }
        # Include alternative CAD sources (always present, may be empty)
        data["cad_sources"] = [
            {
                "partner": s.partner,
                "has_symbol": s.has_symbol,
                "has_footprint": s.has_footprint,
                "has_3d_model": s.has_3d_model,
                "preview_symbol": s.preview_symbol,
                "preview_footprint": s.preview_footprint,
                "preview_3d": s.preview_3d,
                "download_url": s.download_url,
            }
            for s in (result.cad_sources or [])
        ]
        data["octopart_url"] = result.octopart_url or ""
        return data

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

    def shutdown(self) -> None:
        """Stop the background asyncio event loop and join the thread.

        Call this when the :class:`KiAssistAPI` instance is no longer needed
        (e.g. in tests or when creating multiple instances) to release the
        background thread.  The method is idempotent: calling it more than once
        is safe.
        """
        if self._async_loop.is_running():
            self._async_loop.call_soon_threadsafe(self._async_loop.stop)
        if self._async_thread.is_alive():
            self._async_thread.join(timeout=5)
            if self._async_thread.is_alive():
                logger.warning(
                    "KiAssistAPI background thread did not stop within the timeout; "
                    "it may still be running."
                )
        if not self._async_loop.is_closed():
            self._async_loop.close()

    # ------------------------------------------------------------------
    # Library Analyzer / Scanner API
    # ------------------------------------------------------------------

    def analyzer_scan_libraries(
        self, lib_type: str = "both"
    ) -> Dict[str, Any]:
        """Discover and list custom (non-built-in) libraries available for scanning.

        Args:
            lib_type: ``"sym"`` for symbol libraries only, ``"fp"`` for
                      footprint only, or ``"both"`` (default).

        Returns:
            Dict with ``success``, ``symbol_libraries`` and
            ``footprint_libraries`` lists.  Each entry has ``nickname``,
            ``uri``, and ``resolved_path``.
        """
        try:
            import re as _re

            # URIs starting with a KiCad built-in directory variable are
            # default (official) libraries — not user/custom ones.
            _DEFAULT_URI_RE = _re.compile(
                r"^\$\{KICAD\d*_(SYMBOL|FOOTPRINT|3DMODEL)_DIR\}"
            )

            project_dir = None
            if self._current_project_path:
                project_dir = str(Path(self._current_project_path).parent)
            disc = self._library_index.get_discovery()
            env = {"KIPRJMOD": project_dir} if project_dir else None

            sym_libs: list = []
            fp_libs: list = []

            if lib_type in ("sym", "both"):
                for entry in disc.list_symbol_libraries():
                    resolved = entry.resolved_path(env=env)
                    sym_libs.append({
                        "nickname": entry.nickname,
                        "uri": entry.uri,
                        "resolved_path": str(resolved) if resolved else "",
                        "is_default": bool(_DEFAULT_URI_RE.match(entry.uri)),
                    })

            if lib_type in ("fp", "both"):
                for entry in disc.list_footprint_libraries():
                    resolved = entry.resolved_path(env=env)
                    fp_libs.append({
                        "nickname": entry.nickname,
                        "uri": entry.uri,
                        "resolved_path": str(resolved) if resolved else "",
                        "is_default": bool(_DEFAULT_URI_RE.match(entry.uri)),
                    })

            return {
                "success": True,
                "symbol_libraries": sym_libs,
                "footprint_libraries": fp_libs,
            }
        except Exception as exc:
            return {
                "success": False,
                "error": str(exc),
                "symbol_libraries": [],
                "footprint_libraries": [],
            }

    def analyzer_analyze_symbol_library(self, path: str) -> Dict[str, Any]:
        """Run the full analyzer on a single symbol library file.

        Args:
            path: Absolute path to a ``.kicad_sym`` file.

        Returns:
            Dict with the full analysis report (see ``AnalysisReport.to_dict``).
        """
        try:
            from .kicad_parser.analyzer import LibraryAnalyzer

            analyzer = LibraryAnalyzer()
            report = analyzer.analyze_symbol_library(path)
            return {"success": True, **report.to_dict()}
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    def analyzer_analyze_footprint_library(self, path: str) -> Dict[str, Any]:
        """Run the analyzer on a footprint directory (``.pretty`` folder).

        If *path* points to a single ``.kicad_mod`` file, only that file is
        analysed.  If it points to a directory, all ``.kicad_mod`` files
        inside are analysed and the results are aggregated.

        Args:
            path: Absolute path to a ``.kicad_mod`` file or ``.pretty``
                  directory.

        Returns:
            Dict with ``success``, ``reports`` (list of report dicts), and
            aggregate ``total_*`` counts.
        """
        try:
            from .kicad_parser.analyzer import LibraryAnalyzer

            analyzer = LibraryAnalyzer()
            p = Path(path)

            if p.is_dir():
                reports = analyzer.analyze_footprint_directory(p)
            elif p.is_file() and p.suffix == ".kicad_mod":
                reports = [analyzer.analyze_footprint(p)]
            else:
                return {"success": False, "error": f"Not a recognised footprint path: {path}"}

            report_dicts = [r.to_dict() for r in reports]
            total_issues = sum(r["total"] for r in report_dicts)
            total_errors = sum(r["errors"] for r in report_dicts)
            total_warnings = sum(r["warnings"] for r in report_dicts)
            total_fixable = sum(r["fixable"] for r in report_dicts)

            return {
                "success": True,
                "reports": report_dicts,
                "total_issues": total_issues,
                "total_errors": total_errors,
                "total_warnings": total_warnings,
                "total_fixable": total_fixable,
            }
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    def analyzer_fix_symbol_library(
        self, input_path: str, output_path: str = ""
    ) -> Dict[str, Any]:
        """Auto-fix a symbol library and save the result.

        Args:
            input_path:  Source ``.kicad_sym`` file.
            output_path: Destination path.  Empty string means overwrite
                         the original file.

        Returns:
            Dict with ``success``, ``fixes_applied``, and a post-fix
            analysis ``report``.
        """
        try:
            from .kicad_parser.analyzer import LibraryAnalyzer

            analyzer = LibraryAnalyzer()
            out = output_path if output_path else None
            fixes = analyzer.fix_symbol_library(input_path, out)

            # Re-analyse after fix to give updated status
            final_path = output_path if output_path else input_path
            report = analyzer.analyze_symbol_library(final_path)
            return {
                "success": True,
                "fixes_applied": fixes,
                "report": report.to_dict(),
            }
        except Exception as exc:
            return {"success": False, "error": str(exc), "fixes_applied": 0}

    def analyzer_fix_footprint_library(
        self, input_path: str, output_path: str = ""
    ) -> Dict[str, Any]:
        """Auto-fix a footprint library (directory or single file).

        Args:
            input_path:  Source ``.pretty`` directory or ``.kicad_mod`` file.
            output_path: Destination path.  Empty string means in-place.

        Returns:
            Dict with ``success``, ``fixes_applied`` (total), and
            ``details`` dict mapping filenames to fix counts.
        """
        try:
            from .kicad_parser.analyzer import LibraryAnalyzer

            analyzer = LibraryAnalyzer()
            p = Path(input_path)

            if p.is_dir():
                out = output_path if output_path else None
                details = analyzer.fix_footprint_directory(p, out)
                total = sum(details.values())
                return {
                    "success": True,
                    "fixes_applied": total,
                    "details": details,
                }
            elif p.is_file() and p.suffix == ".kicad_mod":
                out = output_path if output_path else None
                fixes = analyzer.fix_footprint(p, out)
                return {
                    "success": True,
                    "fixes_applied": fixes,
                    "details": {p.name: fixes},
                }
            else:
                return {"success": False, "error": f"Unrecognised path: {input_path}"}
        except Exception as exc:
            return {"success": False, "error": str(exc), "fixes_applied": 0}

    def analyzer_batch_scan(
        self, libraries: list
    ) -> Dict[str, Any]:
        """Scan multiple libraries in one call.

        Args:
            libraries: List of dicts, each with ``nickname``, ``path``, and
                       ``type`` (``"sym"`` or ``"fp"``).

        Returns:
            Dict with ``success`` and ``results`` — a list of per-library
            result dicts containing ``nickname``, ``type``, and the report
            data.
        """
        try:
            from .kicad_parser.analyzer import LibraryAnalyzer

            analyzer = LibraryAnalyzer()
            results = []
            for lib in libraries:
                nickname = lib.get("nickname", "")
                lib_path = lib.get("path", "")
                lib_type = lib.get("type", "sym")
                try:
                    if lib_type == "sym":
                        report = analyzer.analyze_symbol_library(lib_path)
                        results.append({
                            "nickname": nickname,
                            "type": "sym",
                            **report.to_dict(),
                        })
                    else:
                        p = Path(lib_path)
                        if p.is_dir():
                            reports = analyzer.analyze_footprint_directory(p)
                        elif p.is_file():
                            reports = [analyzer.analyze_footprint(p)]
                        else:
                            results.append({
                                "nickname": nickname,
                                "type": "fp",
                                "total": 0,
                                "errors": 0,
                                "warnings": 0,
                                "fixable": 0,
                                "issues": [],
                                "error": f"Path not found: {lib_path}",
                            })
                            continue
                        # Aggregate footprint reports for this library
                        all_issues = []
                        for r in reports:
                            rd = r.to_dict()
                            all_issues.extend(rd["issues"])
                        results.append({
                            "nickname": nickname,
                            "type": "fp",
                            "file_count": len(reports),
                            "total": len(all_issues),
                            "errors": sum(1 for i in all_issues if i["severity"] == "error"),
                            "warnings": sum(1 for i in all_issues if i["severity"] == "warning"),
                            "infos": sum(1 for i in all_issues if i["severity"] == "info"),
                            "fixable": sum(1 for i in all_issues if i["fixable"]),
                            "issues": all_issues,
                        })
                except Exception as inner_exc:
                    results.append({
                        "nickname": nickname,
                        "type": lib_type,
                        "total": 0,
                        "errors": 1,
                        "warnings": 0,
                        "fixable": 0,
                        "issues": [{
                            "severity": "error",
                            "category": "structure",
                            "entity": nickname,
                            "message": str(inner_exc),
                            "fixable": False,
                            "fix_action": "",
                            "details": {},
                        }],
                    })
            return {"success": True, "results": results}
        except Exception as exc:
            return {"success": False, "error": str(exc), "results": []}

    def analyzer_batch_fix(
        self, libraries: list
    ) -> Dict[str, Any]:
        """Fix multiple libraries in one call (in-place).

        Args:
            libraries: List of dicts, each with ``nickname``, ``path``, and
                       ``type`` (``"sym"`` or ``"fp"``).

        Returns:
            Dict with ``success`` and ``results`` — per-library fix counts.
        """
        try:
            from .kicad_parser.analyzer import LibraryAnalyzer

            analyzer = LibraryAnalyzer()
            results = []
            total_fixed = 0
            for lib in libraries:
                nickname = lib.get("nickname", "")
                lib_path = lib.get("path", "")
                lib_type = lib.get("type", "sym")
                try:
                    if lib_type == "sym":
                        fixes = analyzer.fix_symbol_library(lib_path)
                    else:
                        p = Path(lib_path)
                        if p.is_dir():
                            details = analyzer.fix_footprint_directory(p)
                            fixes = sum(details.values())
                        elif p.is_file():
                            fixes = analyzer.fix_footprint(p)
                        else:
                            results.append({
                                "nickname": nickname,
                                "type": lib_type,
                                "fixes_applied": 0,
                                "error": f"Path not found: {lib_path}",
                            })
                            continue
                    total_fixed += fixes
                    results.append({
                        "nickname": nickname,
                        "type": lib_type,
                        "fixes_applied": fixes,
                    })
                except Exception as inner_exc:
                    results.append({
                        "nickname": nickname,
                        "type": lib_type,
                        "fixes_applied": 0,
                        "error": str(inner_exc),
                    })
            return {
                "success": True,
                "total_fixed": total_fixed,
                "results": results,
            }
        except Exception as exc:
            return {"success": False, "error": str(exc), "total_fixed": 0}


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
    
    # Start the webview — private_mode=False so localStorage persists between sessions
    storage_dir = str(Path.home() / ".kiassist" / "webview_data")
    webview.start(debug=False, private_mode=False, storage_path=storage_dir)


if __name__ == "__main__":
    main()
