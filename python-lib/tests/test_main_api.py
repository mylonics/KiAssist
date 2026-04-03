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
        # No keys configured yet
        result = api.get_providers()
        for p in result["providers"]:
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
