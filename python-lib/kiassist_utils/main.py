"""Main KiAssist application module using pywebview."""

import asyncio
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
)
from .kicad_schematic import inject_test_note, is_schematic_api_available
from .context.history import ConversationStore
from .context.prompts import SystemPromptBuilder
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
        # Streaming state
        self._stream_lock = threading.Lock()
        self._stream_buffer = ""
        self._stream_thinking_buffer = ""
        self._stream_in_thinking = False
        self._stream_done = True
        self._stream_error = None
        self._stream_cancel = threading.Event()
        # Persistent event loop for async streaming (avoids closing the loop
        # between calls which would destroy the genai client's async session)
        self._async_loop = asyncio.new_event_loop()
        self._async_thread = threading.Thread(
            target=self._async_loop.run_forever, daemon=True
        )
        self._async_thread.start()
    
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
            return self._local_model_manager.start_server(
                model_id, n_ctx=n_ctx, n_gpu_layers=n_gpu_layers
            )
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
        target = provider or self.current_provider_name
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
        target = provider or self.current_provider_name
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

    def start_stream_message(self, message: str, model: Optional[str] = None) -> dict:
        """Start streaming a response from the active AI provider in a background thread.

        The user prompt is persisted immediately; the final assembled assistant
        response is persisted in the background thread once streaming completes.
        The full conversation history from the current session is sent to the
        AI provider so it has context from prior turns, along with a system
        prompt containing project/PCB environment information.

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

            self._stream_cancel.clear()
            with self._stream_lock:
                self._stream_buffer = ""
                self._stream_thinking_buffer = ""
                self._stream_in_thinking = False
                self._stream_done = False
                self._stream_error = None

            # Build full conversation history (the new user message is
            # already persisted above and will be included)
            msgs = self._build_conversation_messages(store, session_id)
            system_prompt = self._build_system_prompt()
            cancel_event = self._stream_cancel

            log_id = llm_logger.start(
                provider=self.current_provider_name,
                model=model or self.current_model,
                messages=msgs,
                system_prompt=system_prompt,
                is_stream=True,
            )

            def _run_stream():
                async def _async_stream():
                    try:
                        last_usage = {}
                        async for chunk in provider.chat_stream(
                            msgs, system_prompt=system_prompt
                        ):
                            if cancel_event.is_set():
                                break
                            if chunk.text:
                                with self._stream_lock:
                                    self._process_stream_chunk(chunk.text)
                            if chunk.usage:
                                last_usage = chunk.usage
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
            return {
                "success": True,
                "text": self._stream_buffer,
                "thinking": self._stream_thinking_buffer,
                "done": self._stream_done,
                "error": self._stream_error,
            }

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
            self._current_project_path = str(p)
            # Clear prompt cache so project context refreshes
            self._prompt_builder.clear_cache()
            # Clear cached project context
            self._raw_context_cache = None
            self._synthesized_context_cache = None
            # Start a new session for the new project
            self.current_session_id = None
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
    # Symbol / Footprint Importer
    # ------------------------------------------------------------------

    def importer_lcsc_available(self) -> Dict[str, Any]:
        """Return whether the easyeda2kicad package is installed."""
        from .importer import lcsc_available
        return {"available": lcsc_available()}

    def importer_get_sym_libraries(self) -> Dict[str, Any]:
        """List all available symbol library nicknames."""
        try:
            from .kicad_parser.library import LibraryDiscovery
            project_dir = None
            if self._current_project_path:
                project_dir = str(Path(self._current_project_path).parent)
            disc = LibraryDiscovery(project_dir=project_dir)
            entries = disc.list_symbol_libraries()
            libs = [{"nickname": e.nickname, "uri": e.uri} for e in entries]
            return {"success": True, "libraries": libs}
        except Exception as exc:
            return {"success": False, "error": str(exc), "libraries": []}

    def importer_get_fp_libraries(self) -> Dict[str, Any]:
        """List all available footprint library nicknames."""
        try:
            from .kicad_parser.library import LibraryDiscovery
            project_dir = None
            if self._current_project_path:
                project_dir = str(Path(self._current_project_path).parent)
            disc = LibraryDiscovery(project_dir=project_dir)
            entries = disc.list_footprint_libraries()
            libs = [{"nickname": e.nickname, "uri": e.uri} for e in entries]
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
        except Exception as exc:
            return {"success": False, "error": str(exc), "warnings": []}

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

        Args:
            query: Case-insensitive substring to match.
            library_name: Restrict search to this library nickname, or ``""``
                for all libraries.

        Returns:
            Dict with ``success`` and ``results`` list.
        """
        try:
            from .importer import search_symbols
            project_dir = None
            if self._current_project_path:
                project_dir = str(Path(self._current_project_path).parent)
            results = search_symbols(
                query,
                library_name=library_name or None,
                project_dir=project_dir,
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

        Args:
            query: Case-insensitive substring.
            library_name: Library nickname, or ``""`` for all.

        Returns:
            Dict with ``success`` and ``results`` list.
        """
        try:
            from .importer import search_footprints
            project_dir = None
            if self._current_project_path:
                project_dir = str(Path(self._current_project_path).parent)
            results = search_footprints(
                query,
                library_name=library_name or None,
                project_dir=project_dir,
            )
            return {"success": True, "results": results}
        except Exception as exc:
            return {"success": False, "error": str(exc), "results": []}

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

    def importer_browse_zip(self) -> Dict[str, Any]:
        """Open a file-chooser dialog for ZIP file selection.

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
                file_types=("ZIP files (*.zip)", "All files (*.*)"),
            )
            if result:
                return {"success": True, "path": result[0]}
            return {"success": True, "path": ""}
        except Exception as exc:
            return {"success": False, "error": str(exc), "path": ""}

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
            from .kicad_parser.library import LibraryDiscovery

            # Gather available library names for the prompt
            project_dir = None
            if self._current_project_path:
                project_dir = str(Path(self._current_project_path).parent)
            disc = LibraryDiscovery(project_dir=project_dir)
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
            from .kicad_parser.library import LibraryDiscovery
            from .kicad_parser.symbol_lib import SymbolLibrary
            from .kicad_parser.sexpr import serialize

            # Load base symbol
            project_dir = None
            if self._current_project_path:
                project_dir = str(Path(self._current_project_path).parent)
            disc = LibraryDiscovery(project_dir=project_dir)
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

    @staticmethod
    def _import_result_to_dict(result) -> Dict[str, Any]:
        """Serialise an :class:`ImportResult` for JSON transport."""
        if not result.success:
            return {"success": False, "error": result.error, "warnings": result.warnings}
        comp = result.component
        fields = comp.fields
        return {
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
    webview.start(debug=False)


if __name__ == "__main__":
    main()
