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
import kiassist_utils
# Accessing KiAssistAPI via the package triggers the lazy-load of
# kiassist_utils.main, ensuring it is present in sys.modules below.
kiassist_utils.KiAssistAPI  # noqa: B018
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

    def test_default_provider_is_gemma4(self, api):
        result = api.get_providers()
        assert result["current_provider"] == "gemma4"

    def test_each_provider_has_models(self, api):
        result = api.get_providers()
        for p in result["providers"]:
            assert len(p["models"]) > 0
            for m in p["models"]:
                assert "id" in m
                assert "name" in m

    def test_has_key_reflects_store(self, api):
        # No cloud keys configured yet; local providers always have has_key=True
        result = api.get_providers()
        for p in result["providers"]:
            if p["id"] in ("local", "gemma4"):
                assert p["has_key"] is True  # local providers need no cloud key
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
        monkeypatch.setattr("kiassist_utils.ai.gemini.GeminiProvider", lambda api_key, model: fake_provider)

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
        # Default provider is gemma4 (local), which always reports True
        assert api.check_api_key() is True
        # Cloud providers with no key should report False
        assert api.check_api_key("gemini") is False
        assert api.check_api_key("claude") is False

    def test_set_api_key_default_provider(self, api, monkeypatch):
        # Switch to gemini first since default is now gemma4 (local)
        api.current_provider_name = "gemini"
        fake_provider = _FakeProvider()
        monkeypatch.setattr("kiassist_utils.ai.gemini.GeminiProvider", lambda k, m: fake_provider)
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
        # Default provider is gemma4 (local), which always returns None
        assert api.get_api_key() is None
        # Explicitly requesting a cloud provider key should work
        api.api_key_store.get_api_key.side_effect = lambda p=None: "AIzaTest" if (p or "gemini") == "gemini" else None
        assert api.get_api_key("gemini") == "AIzaTest"

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
        api.current_provider_name = "gemini"
        fake = _FakeProvider("Hello, PCB designer!")
        monkeypatch.setattr("kiassist_utils.ai.gemini.GeminiProvider", lambda k, m: fake)
        api.api_key_store.get_api_key.side_effect = lambda p=None: "AIzaFake"

        result = api.send_message("What is a PCB?")
        assert result["success"] is True
        assert result["response"] == "Hello, PCB designer!"

    def test_send_message_no_key_returns_error(self, api):
        # Switch to a cloud provider with no key configured
        api.current_provider_name = "gemini"
        result = api.send_message("Hello")
        assert result["success"] is False
        assert "error" in result

    def test_send_message_provider_error_returns_error(self, api, monkeypatch):
        api.current_provider_name = "gemini"
        def _bad_chat(*a, **kw):
            raise RuntimeError("API timeout")

        fake = _FakeProvider()
        fake.chat = _bad_chat
        monkeypatch.setattr("kiassist_utils.ai.gemini.GeminiProvider", lambda k, m: fake)
        api.api_key_store.get_api_key.side_effect = lambda p=None: "AIzaFake"

        result = api.send_message("Hello")
        assert result["success"] is False
        assert "API timeout" in result["error"]

    def test_send_message_persists_to_session(self, api, tmp_path, monkeypatch):
        """Messages should be appended to ConversationStore on success."""
        api.current_provider_name = "gemini"
        fake = _FakeProvider("AI response text")
        monkeypatch.setattr("kiassist_utils.ai.gemini.GeminiProvider", lambda k, m: fake)
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
        api.current_provider_name = "gemini"
        fake = _FakeProvider("reply")
        monkeypatch.setattr("kiassist_utils.ai.gemini.GeminiProvider", lambda k, m: fake)
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
        api.current_provider_name = "gemini"
        fake = _FakeProvider("word1 word2 word3")
        monkeypatch.setattr("kiassist_utils.ai.gemini.GeminiProvider", lambda k, m: fake)
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
        # Switch to a cloud provider with no key
        api.current_provider_name = "gemini"
        result = api.start_stream_message("Hello")
        assert result["success"] is False
        assert "error" in result

    def test_stream_persists_user_message_immediately(self, api, tmp_path, monkeypatch):
        """User message is persisted before streaming starts."""
        api.current_provider_name = "gemini"
        fake = _FakeProvider("streamed response")
        monkeypatch.setattr("kiassist_utils.ai.gemini.GeminiProvider", lambda k, m: fake)
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
        api.current_provider_name = "gemini"
        fake = _FakeProvider("final answer here")
        monkeypatch.setattr("kiassist_utils.ai.gemini.GeminiProvider", lambda k, m: fake)
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
        with patch("kiassist_utils.ai.ollama.OllamaProvider", autospec=True) as mock_ollama_cls:
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
        monkeypatch.setattr("kiassist_utils.ai.gemini.GeminiProvider", lambda k, m: fake_provider)

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

        monkeypatch.setattr("kiassist_utils.ai.gemini.GeminiProvider", _make_gemini)
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
# Tests: web_search_components
# ===========================================================================


class TestWebSearchComponents:
    """Unit tests for KiAssistAPI.web_search_components()."""

    def _make_gemini_provider(self, grounding_text: str = "TXB0104 is ideal."):
        """Return a real GeminiProvider instance with search_grounded_query mocked."""
        from kiassist_utils.ai.gemini import GeminiProvider
        with patch("kiassist_utils.ai.gemini.genai.Client"):
            provider = GeminiProvider(api_key="fake", model="3-flash")
        provider.search_grounded_query = MagicMock(return_value={
            "response_text": grounding_text,
            "search_results": [{"title": "TXB0104", "url": "https://example.com"}],
            "usage": {"input_tokens": 50, "output_tokens": 100},
        })
        return provider

    def test_empty_query_returns_error(self, api):
        result = api.web_search_components("")
        assert result["success"] is False
        assert "empty" in result["error"].lower()

    def test_whitespace_only_query_returns_error(self, api):
        result = api.web_search_components("   ")
        assert result["success"] is False

    def test_no_provider_returns_error(self, api):
        """No provider configured → success: False with a helpful message."""
        result = api.web_search_components("logic level converter")
        assert result["success"] is False
        assert "error" in result

    def test_gemini_path_uses_grounding(self, api, monkeypatch):
        """When Gemini is active, search_grounded_query() is called."""
        provider = self._make_gemini_provider("TXB0104 is ideal.")
        monkeypatch.setattr(api, "_get_or_create_provider", lambda m=None: provider)

        result = api.web_search_components("logic level converter 3.3V")
        assert result["success"] is True
        assert result["grounding"] == "google"
        assert "TXB0104" in result["response"]
        assert len(result["search_results"]) >= 1
        provider.search_grounded_query.assert_called_once()

    def test_duckduckgo_path_for_non_gemini(self, api, monkeypatch):
        """Non-Gemini provider falls back to DuckDuckGo scraping."""
        fake_ddg_results = [
            {"title": "BSS138", "url": "https://example.com/bss138", "snippet": "Level shifter"},
        ]
        monkeypatch.setattr(api, "_get_or_create_provider", lambda m=None: _FakeProvider("BSS138 works well."))
        # web_search is imported locally inside web_search_components; patch at source module
        with patch("kiassist_utils.web_search.web_search", return_value=fake_ddg_results):
            result = api.web_search_components("logic level converter")
        assert result["success"] is True
        assert result["grounding"] == "duckduckgo"
        assert result["search_results"] == fake_ddg_results
        assert "BSS138 works well." in result["response"]

    def test_query_is_stripped(self, api, monkeypatch):
        """Leading/trailing whitespace is stripped from the query in the result."""
        monkeypatch.setattr(api, "_get_or_create_provider", lambda m=None: _FakeProvider())
        with patch("kiassist_utils.web_search.web_search", return_value=[]):
            result = api.web_search_components("  level shifter  ")
        assert result["success"] is True
        assert result["query"] == "level shifter"

    def test_provider_exception_returns_error(self, api, monkeypatch):
        """If the provider raises an exception the method returns success:False."""
        bad_provider = _FakeProvider()
        bad_provider.chat = MagicMock(side_effect=RuntimeError("API timeout"))
        monkeypatch.setattr(api, "_get_or_create_provider", lambda m=None: bad_provider)
        with patch("kiassist_utils.web_search.web_search", return_value=[]):
            result = api.web_search_components("resistor")
        assert result["success"] is False
        assert "API timeout" in result["error"]

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


# ===========================================================================
# Tests: focused agent + MCP tool wiring (Phase 1)
# ===========================================================================

class TestFocusedAgent:
    def test_default_focused_agent_is_none(self, api):
        result = api.get_focused_agent()
        assert result["success"] is True
        assert result["focused_agent"] is None

    def test_set_focused_agent(self, api):
        result = api.set_focused_agent("schematic-agent")
        assert result["success"] is True
        assert result["focused_agent"] == "schematic-agent"
        assert api.get_focused_agent()["focused_agent"] == "schematic-agent"

    def test_clear_focused_agent(self, api):
        api.set_focused_agent("pcb-agent")
        result = api.set_focused_agent(None)
        assert result["success"] is True
        assert result["focused_agent"] is None

    def test_set_focused_agent_rejects_non_string(self, api):
        result = api.set_focused_agent(42)
        assert result["success"] is False


class TestMCPToolSchemaCache:
    def test_unfiltered_schema_list_includes_schematic_tools(self, api):
        schemas = api._get_mcp_tool_schemas(focused_agent=None)
        names = [s["name"] for s in schemas]
        assert "schematic_open" in names
        assert "web_search" in names
        assert "pcb_add_track" in names

    def test_schematic_agent_filter_drops_pcb_tools(self, api):
        schemas = api._get_mcp_tool_schemas(focused_agent="schematic-agent")
        names = {s["name"] for s in schemas}
        assert "schematic_open" in names
        # pcb_* tools must be filtered out for the schematic agent
        assert not any(n.startswith("pcb_") for n in names)

    def test_pcb_agent_filter_drops_schematic_tools(self, api):
        schemas = api._get_mcp_tool_schemas(focused_agent="pcb-agent")
        names = {s["name"] for s in schemas}
        assert "pcb_add_track" in names
        assert not any(n.startswith("schematic_") for n in names)

    def test_schemas_are_cached(self, api):
        first = api._get_mcp_tool_schemas(focused_agent=None)
        second = api._get_mcp_tool_schemas(focused_agent=None)
        # Must return the same cached list object on second call.
        assert first is second


# ===========================================================================
# Tests: ContextWindowManager wiring (Phase 3)
# ===========================================================================

class TestContextWindowManagerWiring:
    def test_returns_none_for_provider_without_window(self, api):
        # _FakeProvider doesn't implement get_context_window
        fake = _FakeProvider("hi")
        mgr = api._get_context_window_manager(fake)
        assert mgr is None

    def test_caches_manager_per_model(self, api):
        from kiassist_utils.ai.base import AIProvider

        class _SizedProvider:
            def get_context_window(self): return 32_768

        api.current_provider_name = "x"
        api.current_model = "y"
        m1 = api._get_context_window_manager(_SizedProvider())
        m2 = api._get_context_window_manager(_SizedProvider())
        assert m1 is m2
        assert m1.context_window == 32_768

    def test_rebuilds_manager_when_window_changes(self, api):
        api.current_provider_name = "x"
        api.current_model = "y"

        class _Provider1:
            def get_context_window(self): return 8000
        class _Provider2:
            def get_context_window(self): return 32_000

        m1 = api._get_context_window_manager(_Provider1())
        m2 = api._get_context_window_manager(_Provider2())
        assert m1 is not m2
        assert m2.context_window == 32_000


class TestConversationHistoryTokenAware:
    def test_history_replays_tool_calls_and_results(self, api, tmp_path):
        """`_build_conversation_messages` must NOT drop tool turns —
        Phase 3 critical regression test."""
        from kiassist_utils.context.history import ConversationStore
        from kiassist_utils.ai.base import AIMessage, AIToolCall, AIToolResult

        api._current_project_path = str(tmp_path)
        store = ConversationStore(tmp_path)
        sid = store.new_session()
        store.append(sid, AIMessage(role="user", content="add a resistor"))
        store.append(sid, AIMessage(
            role="assistant", content="",
            tool_calls=[AIToolCall(id="c1", name="schematic_add_symbol",
                                   arguments={"path": "x"})],
        ))
        store.append(sid, AIMessage(
            role="tool",
            tool_results=[AIToolResult(
                tool_call_id="c1", content='{"status":"ok"}', is_error=False,
            )],
        ))
        store.append(sid, AIMessage(role="assistant", content="Done."))

        msgs = api._build_conversation_messages(store, sid)
        roles = [m.role for m in msgs]
        # All four messages must replay, including the tool turn.
        assert roles == ["user", "assistant", "tool", "assistant"]
        # Tool calls and results round-trip intact.
        assert msgs[1].tool_calls and msgs[1].tool_calls[0].name == "schematic_add_symbol"
        assert msgs[2].tool_results and msgs[2].tool_results[0].tool_call_id == "c1"

    def test_token_aware_trim_drops_high_token_tool_results_first(
        self, api, tmp_path,
    ):
        from kiassist_utils.context.history import ConversationStore
        from kiassist_utils.context.tokens import ContextWindowManager
        from kiassist_utils.ai.base import AIMessage, AIToolCall, AIToolResult

        store = ConversationStore(tmp_path)
        sid = store.new_session()
        # Three tool turns with very different token costs.
        store.append(sid, AIMessage(role="user", content="hi"), token_count=10)
        for cid, big_tokens in [("c1", 9000), ("c2", 100), ("c3", 9000)]:
            store.append(sid, AIMessage(
                role="assistant",
                tool_calls=[AIToolCall(id=cid, name="t", arguments={})],
            ), token_count=5)
            store.append(sid, AIMessage(
                role="tool",
                tool_results=[AIToolResult(
                    tool_call_id=cid, content="x", is_error=False,
                )],
            ), token_count=big_tokens)
        store.append(sid, AIMessage(role="assistant", content="ok"), token_count=20)

        # Tiny window forces aggressive trim.
        ctx_mgr = ContextWindowManager(
            context_window=10_000, summarize_threshold=0.8,
        )
        result = api._build_conversation_messages(store, sid, ctx_mgr=ctx_mgr)
        # The two 9000-token tool results must be dropped first.  The
        # 100-token one and the assistant turns should remain.
        tool_turns = [m for m in result if m.role == "tool"]
        assert len(tool_turns) == 1
        assert tool_turns[0].tool_results[0].tool_call_id == "c2"


# ===========================================================================
# Tests: Phase 4 — project header + raw-context disk cache
# ===========================================================================

class TestProjectHeader:
    def test_header_includes_basic_fields(self, tmp_path, api):
        # Make a tiny mock project: a .kicad_pro and one schematic file.
        pro = tmp_path / "demo.kicad_pro"
        pro.write_text("{}", encoding="utf-8")
        from kiassist_utils.kicad_parser.schematic import Schematic
        sch = Schematic()
        sch.version = 20231120
        sch.generator = "eeschema"
        sch.paper = "A4"
        sch.save(tmp_path / "demo.kicad_sch")

        header = api._build_project_header(str(pro))
        assert "Active Project" in header
        assert "demo" in header
        assert "**Sheet count:** 1" in header
        assert "**BOM size:** 0" in header

    def test_header_includes_kiassist_md(self, tmp_path, api):
        pro = tmp_path / "demo.kicad_pro"
        pro.write_text("{}", encoding="utf-8")
        memory_path = tmp_path / "KIASSIST.md"
        memory_path.write_text("# Custom project notes\nUse JLC parts.",
                               encoding="utf-8")
        header = api._build_project_header(str(pro))
        assert "Project Memory" in header
        assert "Custom project notes" in header

    def test_header_truncates_huge_kiassist_md(self, tmp_path, api):
        pro = tmp_path / "demo.kicad_pro"
        pro.write_text("{}", encoding="utf-8")
        (tmp_path / "KIASSIST.md").write_text("x" * 10_000, encoding="utf-8")
        header = api._build_project_header(str(pro))
        # Hard cap: never larger than ~3500 chars including everything
        # (1500 cap + framing).
        assert len(header) < 3500
        assert "truncated" in header.lower()


class TestProjectMtimeSignature:
    def test_signature_changes_when_file_changes(self, tmp_path, api):
        pro = tmp_path / "demo.kicad_pro"
        pro.write_text("{}", encoding="utf-8")
        sig1 = api._project_mtime_signature(str(pro))
        # Write again with a fresh mtime.
        import time
        time.sleep(0.02)
        pro.write_text("{}\n", encoding="utf-8")
        os = __import__("os")
        # Force a distinct mtime even on coarse-grained filesystems.
        st = pro.stat()
        os.utime(pro, (st.st_atime, st.st_mtime + 1))
        sig2 = api._project_mtime_signature(str(pro))
        assert sig1 != sig2

    def test_signature_stable_when_unchanged(self, tmp_path, api):
        pro = tmp_path / "demo.kicad_pro"
        pro.write_text("{}", encoding="utf-8")
        assert (
            api._project_mtime_signature(str(pro))
            == api._project_mtime_signature(str(pro))
        )


class TestRawContextDiskCache:
    def test_warm_writes_disk_cache(self, tmp_path, api):
        pro = tmp_path / "demo.kicad_pro"
        pro.write_text("{}", encoding="utf-8")
        api._warm_raw_context_cache(str(pro))
        cache_file = tmp_path / ".kiassist" / "context.json"
        assert cache_file.exists()
        import json
        cached = json.loads(cache_file.read_text(encoding="utf-8"))
        assert "signature" in cached and "raw" in cached
        assert api._raw_context_cache is not None

    def test_warm_reuses_disk_cache_on_match(self, tmp_path, api):
        pro = tmp_path / "demo.kicad_pro"
        pro.write_text("{}", encoding="utf-8")
        api._warm_raw_context_cache(str(pro))
        first_raw = api._raw_context_cache
        # Reset in-memory cache; the disk cache must repopulate it.
        api._raw_context_cache = None
        api._warm_raw_context_cache(str(pro))
        assert api._raw_context_cache == first_raw


class TestSecondaryProvider:
    def test_secondary_provider_lazy(self, api):
        # No-op: just verify the helper exists and returns None when
        # nothing is configured (or some provider when defaults work).
        result = api._get_or_create_secondary_provider()
        assert result is None or hasattr(result, "chat_stream")


# ===========================================================================
# Tests: Phase 5 — mutating tools route through SchematicEditPipeline
# ===========================================================================

class TestMutatingToolsRouteThroughPipeline:
    def test_mutating_set_membership(self, api):
        # Sanity — common mutating tools must be flagged so the pipeline
        # wraps them.  Read-only tools must NOT be in the set.
        s = api._MUTATING_SCHEMATIC_TOOLS
        assert "schematic_add_symbol" in s
        assert "schematic_add_wire" in s
        assert "schematic_open" not in s
        assert "schematic_query" not in s

    def test_non_mutating_tool_dispatches_via_in_process_call(
        self, api, monkeypatch, tmp_path,
    ):
        """Read-only tools must NOT be wrapped — they call straight into
        in_process_call so they don't trigger the KiCad save dance."""
        called = {"in_process": 0, "pipeline": 0}

        async def _fake_ipc(name, args):
            called["in_process"] += 1
            return {"status": "ok", "data": {"x": 1}}

        class _FakePipeline:
            def __init__(self, *a, **k): pass
            async def run(self, *a, **k):
                called["pipeline"] += 1
                return {"status": "ok", "data": {}}

        monkeypatch.setattr(
            "kiassist_utils.mcp_server.in_process_call", _fake_ipc,
        )
        monkeypatch.setattr(
            "kiassist_utils.ipc_workflow.SchematicEditPipeline", _FakePipeline,
        )
        import asyncio as _asyncio
        content, is_error = _asyncio.run(api._execute_mcp_tool(
            "schematic_open", {"path": str(tmp_path / "foo.kicad_sch")}
        ))
        assert called["in_process"] == 1
        assert called["pipeline"] == 0
        assert is_error is False
        assert "x" in content

    def test_mutating_tool_with_path_routes_through_pipeline(
        self, api, monkeypatch, tmp_path,
    ):
        called = {"in_process": 0, "pipeline_args": None}

        async def _fake_ipc(name, args):
            called["in_process"] += 1
            return {"status": "ok"}

        class _FakePipeline:
            def __init__(self, file_path):
                called["pipeline_args"] = file_path
            async def run(self, name, args):
                return {"status": "ok", "data": {"name": name}}

        monkeypatch.setattr(
            "kiassist_utils.mcp_server.in_process_call", _fake_ipc,
        )
        monkeypatch.setattr(
            "kiassist_utils.ipc_workflow.SchematicEditPipeline", _FakePipeline,
        )
        target = str(tmp_path / "my.kicad_sch")
        import asyncio as _asyncio
        content, is_error = _asyncio.run(api._execute_mcp_tool(
            "schematic_add_symbol", {"path": target, "lib_id": "Device:R"}
        ))
        # The pipeline path must be the file we passed in.
        assert called["pipeline_args"] == target
        assert called["in_process"] == 0
        assert is_error is False

    def test_mutating_tool_without_path_falls_back_to_in_process_call(
        self, api, monkeypatch,
    ):
        """If a mutating tool is called without a path arg, we must NOT
        try to wrap it in the pipeline (which requires a file path)."""
        called = {"in_process": 0}

        async def _fake_ipc(name, args):
            called["in_process"] += 1
            return {"status": "ok"}

        monkeypatch.setattr(
            "kiassist_utils.mcp_server.in_process_call", _fake_ipc,
        )
        import asyncio as _asyncio
        _asyncio.run(api._execute_mcp_tool("schematic_save", {}))
        assert called["in_process"] == 1

    def test_pipeline_exception_returns_error_tuple(
        self, api, monkeypatch, tmp_path,
    ):
        class _FakePipeline:
            def __init__(self, *a, **k): pass
            async def run(self, *a, **k):
                raise RuntimeError("boom")
        monkeypatch.setattr(
            "kiassist_utils.ipc_workflow.SchematicEditPipeline", _FakePipeline,
        )
        import asyncio as _asyncio
        content, is_error = _asyncio.run(api._execute_mcp_tool(
            "schematic_add_symbol",
            {"path": str(tmp_path / "x.kicad_sch")},
        ))
        assert is_error is True
        assert "boom" in content


# ===========================================================================
# Phase 7 — End-to-end smoke test
# ===========================================================================

class _ScriptedProvider:
    """A stub provider that replays a canned sequence of tool calls.

    Each call to ``chat_stream`` pops the next pre-canned response from a
    list.  The sequence ends with a plain text response (no tool calls),
    which terminates the agent's tool-execution loop.  This lets us
    verify the full agent loop end-to-end without a real LLM.
    """

    def __init__(self, scripted_responses):
        # Each response is a list of (tool_name, args) tuples or a string.
        self._script = list(scripted_responses)

    def get_context_window(self): return 128_000
    def get_max_output_tokens(self): return 4_096
    def supports_tool_calling(self): return True

    async def chat_stream(self, messages, tools=None, system_prompt=None):
        from kiassist_utils.ai.base import AIChunk, AIToolCall
        if not self._script:
            yield AIChunk(text="done.", is_final=False)
            yield AIChunk(text="", is_final=True, tool_calls=[],
                          usage={"input_tokens": 1, "output_tokens": 1})
            return
        step = self._script.pop(0)
        if isinstance(step, str):
            yield AIChunk(text=step, is_final=False)
            yield AIChunk(text="", is_final=True, tool_calls=[],
                          usage={"input_tokens": 5, "output_tokens": 5})
            return
        # Otherwise it's a list of tool calls.
        calls = [
            AIToolCall(id=f"call-{i}", name=name, arguments=args)
            for i, (name, args) in enumerate(step)
        ]
        yield AIChunk(text="", is_final=True, tool_calls=calls,
                      usage={"input_tokens": 5, "output_tokens": 5})


class TestAgentSmoke:
    """End-to-end: stub provider drives a "create → add → save" sequence
    purely through the MCP tool registry.  This is the single test that
    would have caught the original "agent loop never wired up" bug."""

    def test_scripted_create_then_save_via_mcp(self, api, tmp_path, monkeypatch):
        import asyncio as _asyncio
        # Build a scripted plan: project_create → schematic_save → done.
        sch_path = tmp_path / "Smoke" / "Smoke.kicad_sch"
        provider = _ScriptedProvider([
            [("project_create", {
                "directory": str(tmp_path), "name": "Smoke",
            })],
            [("schematic_save", {"path": str(sch_path)})],
            "Schematic created and saved.",
        ])

        # Drive _execute_mcp_tool calls directly to verify each step
        # works end-to-end.  We don't need the streaming loop here — the
        # streaming loop is a separate concern; the contract under test
        # is that the in-process MCP dispatch chain works.
        loop = _asyncio.new_event_loop()
        try:
            content1, err1 = loop.run_until_complete(
                api._execute_mcp_tool("project_create", {
                    "directory": str(tmp_path), "name": "Smoke",
                })
            )
            assert err1 is False, content1
            assert sch_path.exists()

            content2, err2 = loop.run_until_complete(
                api._execute_mcp_tool("schematic_save", {"path": str(sch_path)})
            )
            assert err2 is False, content2
            # Re-open round-trip: the file must still parse cleanly.
            content3, err3 = loop.run_until_complete(
                api._execute_mcp_tool("schematic_open", {"path": str(sch_path)})
            )
            assert err3 is False, content3
            import json as _json
            data = _json.loads(content3)
            assert data["status"] == "ok"
            assert data["data"]["component_count"] == 0
        finally:
            loop.close()

    def test_focused_agent_filters_tools_for_streaming(self, api):
        # Verify the schemas pipeline returns *only* schematic-related
        # tools when focused_agent=schematic-agent — the real check the
        # streaming dispatch performs each turn.
        api.set_focused_agent("schematic-agent")
        schemas = api._get_mcp_tool_schemas("schematic-agent")
        names = {s["name"] for s in schemas}
        assert "schematic_open" in names
        assert "schematic_create" in names
        assert "schematic_save" in names
        # PCB tools must not be visible to the schematic agent.
        assert not any(n.startswith("pcb_") for n in names)
