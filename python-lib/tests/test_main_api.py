"""Tests for KiAssistAPI in main.py.

Covers Phase 7.1 additions:
* Provider-agnostic AI interface (get_providers, set_provider)
* Multi-provider API key management (check_api_key, get_api_key, set_api_key with provider param)
* Session management (get_sessions, resume_session, export_session)
* Refactored send_message and start_stream_message
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch

import pytest

from kiassist_utils.ai.base import AIChunk, AIMessage, AIResponse
from kiassist_utils.context.history import ConversationStore

# ---------------------------------------------------------------------------
# Access the main module via sys.modules (avoids the __init__.py function
# shadowing issue where `kiassist_utils.main` resolves to the main()
# function rather than the module object).
# ---------------------------------------------------------------------------
import kiassist_utils  # noqa: F401 — ensure package is loaded
_main_mod = sys.modules["kiassist_utils.main"]
KiAssistAPI = _main_mod.KiAssistAPI


# ---------------------------------------------------------------------------
# Minimal fake AIProvider for testing
# ---------------------------------------------------------------------------

class _FakeProvider:
    """Minimal fake that mimics AIProvider.chat() and chat_stream()."""

    def __init__(self, response_text: str = "Hello from AI"):
        self._response = response_text

    def chat(self, messages, tools=None, system_prompt=None) -> AIResponse:  # type: ignore[override]
        return AIResponse(content=self._response, tool_calls=[], usage={})

    async def chat_stream(self, messages, tools=None, system_prompt=None):  # type: ignore[override]
        words = self._response.split()
        for word in words:
            yield AIChunk(text=word + " ", is_final=False)
        yield AIChunk(text="", is_final=True, tool_calls=[], usage={})


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def api(tmp_path, monkeypatch):
    """Return a KiAssistAPI instance with mocked dependencies."""
    fake_keys: Dict[str, str] = {}

    def _has_key(provider=None):
        return (provider or "gemini") in fake_keys

    def _get_key(provider=None):
        return fake_keys.get(provider or "gemini")

    def _set_key(api_key, provider=None):
        fake_keys[provider or "gemini"] = api_key
        return (True, None)

    mock_store = MagicMock()
    mock_store.has_api_key.side_effect = _has_key
    mock_store.get_api_key.side_effect = _get_key
    mock_store.set_api_key.side_effect = _set_key

    mock_recent = MagicMock()
    mock_recent.get_recent_projects.return_value = []

    # Patch at the module level using sys.modules reference
    monkeypatch.setattr(_main_mod, "ApiKeyStore", lambda: mock_store)
    monkeypatch.setattr(_main_mod, "RecentProjectsStore", lambda: mock_recent)

    instance = KiAssistAPI()
    instance._current_project_path = str(tmp_path)
    return instance


# ===========================================================================
# Tests: get_providers
# ===========================================================================

class TestGetProviders:
    def test_returns_three_providers(self, api):
        result = api.get_providers()
        assert result["success"] is True
        ids = [p["id"] for p in result["providers"]]
        assert "gemini" in ids
        assert "claude" in ids
        assert "openai" in ids
        assert "local" in ids

    def test_default_provider_is_gemini(self, api):
        result = api.get_providers()
        assert result["current_provider"] == "gemini"

    def test_each_provider_has_models(self, api):
        result = api.get_providers()
        for p in result["providers"]:
            assert len(p["models"]) > 0
            for m in p["models"]:
                assert "id" in m
                assert "name" in m

    def test_has_key_reflects_store(self, api):
        # No cloud keys configured yet; local provider always has has_key=True
        result = api.get_providers()
        for p in result["providers"]:
            if p["id"] == "local":
                assert p["has_key"] is True  # local needs no cloud key
            else:
                assert p["has_key"] is False

    def test_has_key_true_after_set(self, api, monkeypatch):
        # Simulate key being set for gemini
        api.api_key_store.has_api_key.side_effect = lambda provider=None: (provider or "gemini") == "gemini"
        result = api.get_providers()
        gemini = next(p for p in result["providers"] if p["id"] == "gemini")
        assert gemini["has_key"] is True


# ===========================================================================
# Tests: set_provider
# ===========================================================================

class TestSetProvider:
    def test_set_valid_provider(self, api, monkeypatch):
        # No key for claude, so warning expected
        result = api.set_provider("claude", "sonnet")
        assert result["success"] is True
        assert api.current_provider_name == "claude"
        assert api.current_model == "sonnet"

    def test_set_invalid_provider(self, api):
        result = api.set_provider("foobar", "model-x")
        assert result["success"] is False
        assert "Unknown provider" in result["error"]

    def test_set_provider_with_key_creates_instance(self, api, monkeypatch):
        # Provide a fake key for gemini
        api.api_key_store.get_api_key.side_effect = lambda p=None: "AIzaFakeKey" if (p or "gemini") == "gemini" else None

        fake_provider = _FakeProvider()
        monkeypatch.setattr(_main_mod, "GeminiProvider", lambda api_key, model: fake_provider)

        result = api.set_provider("gemini", "3-flash")
        assert result["success"] is True
        assert api.current_provider is fake_provider

    def test_set_provider_no_key_returns_warning(self, api):
        result = api.set_provider("openai", "gpt-4o")
        assert result["success"] is True
        assert "warning" in result


# ===========================================================================
# Tests: check_api_key / get_api_key / set_api_key (multi-provider)
# ===========================================================================

class TestApiKeyManagement:
    def test_check_api_key_no_key(self, api):
        assert api.check_api_key() is False
        assert api.check_api_key("gemini") is False
        assert api.check_api_key("claude") is False

    def test_set_api_key_default_provider(self, api, monkeypatch):
        fake_provider = _FakeProvider()
        monkeypatch.setattr(_main_mod, "GeminiProvider", lambda k, m: fake_provider)
        api.api_key_store.get_api_key.side_effect = lambda p=None: "AIzaFake" if (p or "gemini") == "gemini" else None

        result = api.set_api_key("AIzaFakeKey")
        assert result["success"] is True
        # Called with current provider
        api.api_key_store.set_api_key.assert_called_once_with("AIzaFakeKey", "gemini")

    def test_set_api_key_explicit_provider(self, api, monkeypatch):
        result = api.set_api_key("sk-ant-fakekey", "claude")
        assert result["success"] is True
        api.api_key_store.set_api_key.assert_called_once_with("sk-ant-fakekey", "claude")

    def test_get_api_key_uses_current_provider(self, api):
        api.api_key_store.get_api_key.side_effect = lambda p=None: "AIzaTest" if (p or "gemini") == "gemini" else None
        key = api.get_api_key()
        assert key == "AIzaTest"

    def test_get_api_key_explicit_provider(self, api):
        api.api_key_store.get_api_key.side_effect = lambda p=None: {
            "gemini": "AIzaGemini",
            "claude": "sk-ant-Claude",
        }.get(p or "gemini")
        assert api.get_api_key("gemini") == "AIzaGemini"
        assert api.get_api_key("claude") == "sk-ant-Claude"

    def test_check_api_key_local_providers_always_true(self, api):
        """Local providers (local, gemma4) never need a key."""
        assert api.check_api_key("local") is True
        assert api.check_api_key("gemma4") is True

    def test_check_api_key_case_insensitive(self, api):
        """Provider names should be normalised before lookup."""
        assert api.check_api_key("Gemma4") is True
        assert api.check_api_key("LOCAL") is True
        assert api.check_api_key("GEMMA4") is True

    def test_get_api_key_local_providers_returns_none(self, api):
        """Local providers return None without touching the key store."""
        api.api_key_store.get_api_key.side_effect = Exception("should not be called")
        assert api.get_api_key("local") is None
        assert api.get_api_key("gemma4") is None

    def test_get_api_key_case_insensitive(self, api):
        """Mixed-case provider names should be normalised."""
        assert api.get_api_key("Gemma4") is None
        assert api.get_api_key("LOCAL") is None


# ===========================================================================
# Tests: send_message
# ===========================================================================

class TestSendMessage:
    def test_send_message_returns_response(self, api, monkeypatch):
        fake = _FakeProvider("Hello, PCB designer!")
        monkeypatch.setattr(_main_mod, "GeminiProvider", lambda k, m: fake)
        api.api_key_store.get_api_key.side_effect = lambda p=None: "AIzaFake"

        result = api.send_message("What is a PCB?")
        assert result["success"] is True
        assert result["response"] == "Hello, PCB designer!"

    def test_send_message_no_key_returns_error(self, api):
        # No key configured
        result = api.send_message("Hello")
        assert result["success"] is False
        assert "error" in result

    def test_send_message_provider_error_returns_error(self, api, monkeypatch):
        def _bad_chat(*a, **kw):
            raise RuntimeError("API timeout")

        fake = _FakeProvider()
        fake.chat = _bad_chat
        monkeypatch.setattr(_main_mod, "GeminiProvider", lambda k, m: fake)
        api.api_key_store.get_api_key.side_effect = lambda p=None: "AIzaFake"

        result = api.send_message("Hello")
        assert result["success"] is False
        assert "API timeout" in result["error"]

    def test_send_message_persists_to_session(self, api, tmp_path, monkeypatch):
        """Messages should be appended to ConversationStore on success."""
        fake = _FakeProvider("AI response text")
        monkeypatch.setattr(_main_mod, "GeminiProvider", lambda k, m: fake)
        api.api_key_store.get_api_key.side_effect = lambda p=None: "AIzaFake"
        api._current_project_path = str(tmp_path)

        api.send_message("User question")

        assert api.current_session_id is not None
        store = ConversationStore(tmp_path)
        msgs = store.load_session(api.current_session_id)
        roles = [m.role for m in msgs]
        contents = [m.content for m in msgs]
        assert roles == ["user", "assistant"]
        assert "User question" in contents
        assert "AI response text" in contents

    def test_send_message_reuses_existing_session(self, api, tmp_path, monkeypatch):
        """Subsequent calls should append to the same session."""
        fake = _FakeProvider("reply")
        monkeypatch.setattr(_main_mod, "GeminiProvider", lambda k, m: fake)
        api.api_key_store.get_api_key.side_effect = lambda p=None: "AIzaFake"
        api._current_project_path = str(tmp_path)

        api.send_message("first")
        session_id_1 = api.current_session_id
        api.send_message("second")
        session_id_2 = api.current_session_id

        assert session_id_1 == session_id_2
        store = ConversationStore(tmp_path)
        msgs = store.load_session(session_id_1)
        assert len(msgs) == 4  # user+assistant x2


# ===========================================================================
# Tests: start_stream_message / poll_stream
# ===========================================================================

class TestStreaming:
    def test_stream_lifecycle(self, api, monkeypatch):
        fake = _FakeProvider("word1 word2 word3")
        monkeypatch.setattr(_main_mod, "GeminiProvider", lambda k, m: fake)
        api.api_key_store.get_api_key.side_effect = lambda p=None: "AIzaFake"

        start_result = api.start_stream_message("Hello")
        assert start_result["success"] is True

        # Wait for streaming to complete
        deadline = time.time() + 5.0
        while time.time() < deadline:
            poll = api.poll_stream()
            if poll["done"]:
                break
            time.sleep(0.05)

        assert poll["done"] is True
        assert poll["error"] is None
        assert "word1" in poll["text"]

    def test_stream_no_key_returns_error(self, api):
        result = api.start_stream_message("Hello")
        assert result["success"] is False
        assert "error" in result

    def test_stream_persists_user_message_immediately(self, api, tmp_path, monkeypatch):
        """User message is persisted before streaming starts."""
        fake = _FakeProvider("streamed response")
        monkeypatch.setattr(_main_mod, "GeminiProvider", lambda k, m: fake)
        api.api_key_store.get_api_key.side_effect = lambda p=None: "AIzaFake"
        api._current_project_path = str(tmp_path)

        api.start_stream_message("Streaming question")
        assert api.current_session_id is not None

        # User message is written before streaming begins
        store = ConversationStore(tmp_path)
        msgs = store.load_session(api.current_session_id)
        assert any(m.role == "user" and "Streaming question" in m.content for m in msgs)

    def test_stream_persists_assistant_response_when_done(self, api, tmp_path, monkeypatch):
        """Assistant response is persisted after streaming completes."""
        fake = _FakeProvider("final answer here")
        monkeypatch.setattr(_main_mod, "GeminiProvider", lambda k, m: fake)
        api.api_key_store.get_api_key.side_effect = lambda p=None: "AIzaFake"
        api._current_project_path = str(tmp_path)

        api.start_stream_message("Stream me")

        # Wait for streaming to complete
        deadline = time.time() + 5.0
        while time.time() < deadline:
            if api.poll_stream()["done"]:
                break
            time.sleep(0.05)

        store = ConversationStore(tmp_path)
        msgs = store.load_session(api.current_session_id)
        roles = [m.role for m in msgs]
        assert "assistant" in roles
        assistant_content = next(m.content for m in msgs if m.role == "assistant")
        assert "final answer" in assistant_content


# ===========================================================================
# Tests: session management
# ===========================================================================

class TestSessionManagement:
    def test_get_sessions_empty(self, api, tmp_path):
        api._current_project_path = str(tmp_path)
        result = api.get_sessions()
        assert result["success"] is True
        assert result["sessions"] == []

    def test_get_sessions_after_append(self, api, tmp_path):
        api._current_project_path = str(tmp_path)
        store = ConversationStore(tmp_path)
        sid = store.new_session()
        store.append(sid, AIMessage(role="user", content="hello"))

        result = api.get_sessions()
        assert result["success"] is True
        assert len(result["sessions"]) == 1
        assert result["sessions"][0]["session_id"] == sid

    def test_resume_session(self, api, tmp_path):
        api._current_project_path = str(tmp_path)
        store = ConversationStore(tmp_path)
        sid = store.new_session()
        store.append(sid, AIMessage(role="user", content="user msg"))
        store.append(sid, AIMessage(role="assistant", content="assistant reply"))

        result = api.resume_session(sid)
        assert result["success"] is True
        assert result["session_id"] == sid
        assert len(result["messages"]) == 2
        assert result["messages"][0]["role"] == "user"
        assert result["messages"][0]["content"] == "user msg"

    def test_resume_nonexistent_session_returns_empty(self, api, tmp_path):
        api._current_project_path = str(tmp_path)
        result = api.resume_session("nonexistentsid")
        assert result["success"] is True
        assert result["messages"] == []

    def test_export_session(self, api, tmp_path):
        api._current_project_path = str(tmp_path)
        store = ConversationStore(tmp_path)
        sid = store.new_session()
        store.append(sid, AIMessage(role="user", content="What is a KiCad footprint?"))
        store.append(sid, AIMessage(role="assistant", content="A footprint is a PCB land pattern."))

        result = api.export_session(sid)
        assert result["success"] is True
        assert "What is a KiCad footprint?" in result["content"]
        assert "A footprint is a PCB land pattern." in result["content"]

    def test_export_nonexistent_session_returns_empty(self, api, tmp_path):
        api._current_project_path = str(tmp_path)
        result = api.export_session("nope")
        assert result["success"] is True
        assert result["content"] == ""

    def test_get_sessions_with_explicit_path(self, api, tmp_path):
        store = ConversationStore(tmp_path)
        sid = store.new_session()
        store.append(sid, AIMessage(role="user", content="hi"))

        result = api.get_sessions(project_path=str(tmp_path))
        assert result["success"] is True
        assert len(result["sessions"]) == 1


# ===========================================================================
# Tests: set_project_path
# ===========================================================================

class TestSetProjectPath:
    def test_set_existing_path(self, api, tmp_path):
        result = api.set_project_path(str(tmp_path))
        assert result["success"] is True
        assert api._current_project_path == str(tmp_path)

    def test_set_nonexistent_path(self, api):
        result = api.set_project_path("/nonexistent/path/that/does/not/exist")
        assert result["success"] is False
        assert "error" in result


# ===========================================================================
# Tests: local provider and dual-model (primary/secondary) support
# ===========================================================================

class TestLocalProvider:
    """Tests for the 'local' (Ollama/LM Studio) provider integration."""

    def test_get_providers_includes_local(self, api):
        result = api.get_providers()
        assert result["success"] is True
        ids = [p["id"] for p in result["providers"]]
        assert "local" in ids

    def test_local_provider_has_key_always_true(self, api):
        result = api.get_providers()
        local = next(p for p in result["providers"] if p["id"] == "local")
        assert local["has_key"] is True

    def test_local_provider_has_base_url(self, api):
        result = api.get_providers()
        local = next(p for p in result["providers"] if p["id"] == "local")
        assert "base_url" in local
        assert local["base_url"].startswith("http")

    def test_set_local_base_url_persists(self, api, monkeypatch):
        new_url = "http://localhost:1234/v1"
        api.api_key_store.get_api_key.side_effect = lambda p=None: (
            new_url if p == "local" else None
        )
        result = api.set_local_base_url(new_url)
        assert result["success"] is True
        # The stored URL should be used for subsequent local providers
        api.api_key_store.set_api_key.assert_called_with(new_url, "local")

    def test_set_local_base_url_empty_string_fails(self, api):
        result = api.set_local_base_url("")
        assert result["success"] is False
        assert "empty" in result["error"].lower()

    def test_set_provider_local_creates_ollama_provider(self, api, monkeypatch):
        local_base_url = "http://127.0.0.1:11434/v1"
        api.api_key_store.get_api_key.side_effect = lambda p=None: (
            local_base_url if p == "local" else None
        )

        fake_ollama = _FakeProvider("local AI response")
        with patch.object(_main_mod, "OllamaProvider", autospec=True) as mock_ollama_cls:
            mock_ollama_cls.return_value = fake_ollama
            result = api.set_provider("local", "llama3.2")

        assert result["success"] is True
        assert api.current_provider_name == "local"
        mock_ollama_cls.assert_called_once()
        call_kwargs = mock_ollama_cls.call_args[1]
        assert call_kwargs.get("model") == "llama3.2"
        assert call_kwargs.get("base_url") == local_base_url

    def test_get_local_models_server_unreachable(self, api):
        """When Ollama is not running, get_local_models returns an error dict."""
        # Use a port that is almost certainly not listening
        api.api_key_store.get_api_key.side_effect = lambda p=None: (
            "http://127.0.0.1:19999/v1" if p == "local" else None
        )
        result = api.get_local_models()
        assert result["success"] is False
        assert "models" in result
        assert result["models"] == []
        assert "error" in result


class TestDualModelSupport:
    """Tests for secondary (lightweight) model selection."""

    def test_get_providers_includes_secondary_model_info(self, api):
        result = api.get_providers()
        assert "secondary_provider" in result
        assert "secondary_model" in result

    def test_secondary_model_defaults(self, api):
        result = api.get_providers()
        # Default secondary provider should be a valid provider ID
        valid_ids = {p["id"] for p in result["providers"]}
        assert result["secondary_provider"] in valid_ids

    def test_get_model_config_returns_primary_and_secondary(self, api):
        result = api.get_model_config()
        assert result["success"] is True
        assert "primary" in result
        assert "secondary" in result
        assert "provider" in result["primary"]
        assert "model" in result["primary"]
        assert "provider" in result["secondary"]
        assert "model" in result["secondary"]

    def test_set_secondary_model_valid(self, api):
        result = api.set_secondary_model("gemini", "3.1-flash-lite")
        assert result["success"] is True
        assert api.secondary_provider_name == "gemini"
        assert api.secondary_model == "3.1-flash-lite"

    def test_set_secondary_model_invalid_provider(self, api):
        result = api.set_secondary_model("unknown", "model-x")
        assert result["success"] is False
        assert "Unknown provider" in result["error"]

    def test_set_secondary_model_with_key_creates_instance(self, api, monkeypatch):
        api.api_key_store.get_api_key.side_effect = lambda p=None: (
            "AIzaFakeKey" if (p or "gemini") == "gemini" else None
        )
        fake_provider = _FakeProvider()
        monkeypatch.setattr(_main_mod, "GeminiProvider", lambda k, m: fake_provider)

        result = api.set_secondary_model("gemini", "3.1-flash-lite")
        assert result["success"] is True
        assert api.secondary_provider is fake_provider

    def test_set_secondary_model_no_key_returns_warning(self, api):
        result = api.set_secondary_model("openai", "gpt-4o-mini")
        assert result["success"] is True
        assert "warning" in result

    def test_primary_and_secondary_can_be_different(self, api, monkeypatch):
        api.api_key_store.get_api_key.side_effect = lambda p=None: "AIzaFakeKey"
        fake1 = _FakeProvider("primary response")
        fake2 = _FakeProvider("secondary response")
        call_count = [0]

        def _make_gemini(key, model):
            call_count[0] += 1
            return fake1 if call_count[0] == 1 else fake2

        monkeypatch.setattr(_main_mod, "GeminiProvider", _make_gemini)
        api.set_provider("gemini", "3.1-pro")
        api.set_secondary_model("gemini", "3.1-flash-lite")

        assert api.current_model == "3.1-pro"
        assert api.secondary_model == "3.1-flash-lite"


class TestOllamaProvider:
    """Unit tests for OllamaProvider."""

    def _make_provider(self, model="llama3.2", base_url="http://localhost:11434/v1"):
        from kiassist_utils.ai.ollama import OllamaProvider
        from unittest.mock import patch
        with patch("kiassist_utils.ai.openai._openai.OpenAI"), \
             patch("kiassist_utils.ai.openai._openai.AsyncOpenAI"):
            return OllamaProvider(model=model, base_url=base_url)

    def test_provider_name(self):
        p = self._make_provider()
        assert p.provider_name == "OllamaProvider"

    def test_model_name(self):
        p = self._make_provider(model="mistral")
        assert p.model_name == "mistral"

    def test_base_url(self):
        p = self._make_provider(base_url="http://localhost:1234/v1")
        assert p.base_url == "http://localhost:1234/v1"

    def test_default_context_window(self):
        from kiassist_utils.ai.ollama import _DEFAULT_CONTEXT_WINDOW
        p = self._make_provider()
        assert p.get_context_window() == _DEFAULT_CONTEXT_WINDOW

    def test_default_max_output_tokens(self):
        from kiassist_utils.ai.ollama import _DEFAULT_MAX_OUTPUT_TOKENS
        p = self._make_provider()
        assert p.get_max_output_tokens() == _DEFAULT_MAX_OUTPUT_TOKENS

    def test_supports_tool_calling(self):
        p = self._make_provider()
        assert p.supports_tool_calling() is True

    def test_chat_delegates_to_openai_provider(self):
        from kiassist_utils.ai.ollama import OllamaProvider
        from kiassist_utils.ai.base import AIMessage, AIResponse
        from unittest.mock import MagicMock, patch
        with patch("kiassist_utils.ai.openai._openai.OpenAI"), \
             patch("kiassist_utils.ai.openai._openai.AsyncOpenAI"):
            p = OllamaProvider(model="llama3.2")

        fake_response = AIResponse(content="Hello from Ollama", tool_calls=[], usage={})
        p._delegate.chat = MagicMock(return_value=fake_response)

        result = p.chat([AIMessage(role="user", content="hi")])
        assert result.content == "Hello from Ollama"
        p._delegate.chat.assert_called_once()


# ===========================================================================
# Tests: shutdown
# ===========================================================================

class TestShutdown:
    def test_shutdown_stops_background_thread(self, api):
        """shutdown() should stop the background asyncio thread."""
        assert api._async_thread.is_alive(), "thread should be running before shutdown"
        api.shutdown()
        assert not api._async_thread.is_alive(), "thread should be stopped after shutdown"

    def test_shutdown_closes_event_loop(self, api):
        """shutdown() should close the event loop to release resources."""
        assert not api._async_loop.is_closed(), "loop should be open before shutdown"
        api.shutdown()
        assert api._async_loop.is_closed(), "loop should be closed after shutdown"

    def test_shutdown_idempotent(self, api):
        """Calling shutdown() more than once must not raise."""
        api.shutdown()
        api.shutdown()  # second call must be a no-op

    def test_api_fixture_does_not_leak_threads(self, api):
        """Ensure the fixture teardown stops threads (no accumulation)."""
        thread = api._async_thread
        api.shutdown()
        thread.join(timeout=2)
        assert not thread.is_alive()
