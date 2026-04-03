"""Tests for Phase 3: Provider-Agnostic AI Interface.

Covers:
* Data types (AIMessage, AIToolCall, AIToolResult, AIResponse, AIChunk)
* Abstract interface contract
* GeminiProvider (with mock client)
* ClaudeProvider (with mock client)
* OpenAIProvider (with mock client)
* ToolExecutor agentic loop (with mock provider and mock MCP calls)
* ApiKeyStore multi-provider support
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any, AsyncIterator, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kiassist_utils.ai.base import (
    AIChunk,
    AIMessage,
    AIProvider,
    AIResponse,
    AIToolCall,
    AIToolResult,
    ToolSchema,
)
from kiassist_utils.ai.gemini import (
    GeminiProvider,
    _mcp_schema_to_gemini,
    _messages_to_gemini,
    _extract_tool_calls as gemini_extract_tc,
)
from kiassist_utils.ai.claude import (
    ClaudeProvider,
    _mcp_schema_to_claude,
    _messages_to_claude,
)
from kiassist_utils.ai.openai import (
    OpenAIProvider,
    _mcp_schema_to_openai,
    _messages_to_openai,
)
from kiassist_utils.ai.tool_executor import ToolExecutor
from kiassist_utils.api_key import ApiKeyStore


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SAMPLE_TOOL: ToolSchema = {
    "name": "schematic_open",
    "description": "Open a schematic file",
    "inputSchema": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path to the schematic file"}
        },
        "required": ["path"],
    },
}


# ---------------------------------------------------------------------------
# Data type tests
# ---------------------------------------------------------------------------


class TestDataTypes:
    def test_ai_message_defaults(self):
        msg = AIMessage(role="user", content="hello")
        assert msg.role == "user"
        assert msg.content == "hello"
        assert msg.tool_calls == []
        assert msg.tool_results == []

    def test_ai_tool_call(self):
        tc = AIToolCall(id="call_1", name="my_tool", arguments={"x": 1})
        assert tc.id == "call_1"
        assert tc.name == "my_tool"
        assert tc.arguments == {"x": 1}

    def test_ai_tool_result(self):
        tr = AIToolResult(tool_call_id="call_1", content="ok", is_error=False)
        assert tr.tool_call_id == "call_1"
        assert not tr.is_error

    def test_ai_response_defaults(self):
        r = AIResponse()
        assert r.content == ""
        assert r.tool_calls == []
        assert r.usage == {}

    def test_ai_chunk_defaults(self):
        c = AIChunk()
        assert c.text == ""
        assert not c.is_final
        assert c.tool_calls == []

    def test_ai_message_with_tool_calls(self):
        tc = AIToolCall(id="c1", name="tool_a", arguments={})
        msg = AIMessage(role="assistant", content="", tool_calls=[tc])
        assert len(msg.tool_calls) == 1
        assert msg.tool_calls[0].name == "tool_a"

    def test_ai_message_with_tool_results(self):
        tr = AIToolResult(tool_call_id="c1", content="result", is_error=False)
        msg = AIMessage(role="tool", tool_results=[tr])
        assert len(msg.tool_results) == 1


# ---------------------------------------------------------------------------
# Abstract interface test
# ---------------------------------------------------------------------------


class TestAIProviderAbstract:
    def test_cannot_instantiate_abstract(self):
        with pytest.raises(TypeError):
            AIProvider()  # type: ignore[abstract]

    def test_concrete_subclass(self):
        class ConcreteProvider(AIProvider):
            def chat(self, messages, tools=None, system_prompt=None):
                return AIResponse(content="hello")

            async def chat_stream(self, messages, tools=None, system_prompt=None):
                yield AIChunk(text="hi", is_final=True)

            def get_context_window(self):
                return 4096

            def get_max_output_tokens(self):
                return 512

            def supports_tool_calling(self):
                return False

        p = ConcreteProvider()
        r = p.chat([AIMessage(role="user", content="hi")])
        assert r.content == "hello"
        assert p.provider_name == "ConcreteProvider"


# ---------------------------------------------------------------------------
# Gemini Provider tests (mocked)
# ---------------------------------------------------------------------------


class TestGeminiProvider:
    def _make_provider(self):
        with patch("kiassist_utils.ai.gemini.genai.Client"):
            return GeminiProvider(api_key="fake_key", model="3-flash")

    def test_model_name_resolved(self):
        p = self._make_provider()
        assert p._model_name == "gemini-3-flash-preview"

    def test_unknown_model_passthrough(self):
        with patch("kiassist_utils.ai.gemini.genai.Client"):
            p = GeminiProvider(api_key="fake", model="custom-model-id")
        assert p._model_name == "custom-model-id"

    def test_get_context_window(self):
        p = self._make_provider()
        assert p.get_context_window() == 1_000_000

    def test_get_max_output_tokens(self):
        p = self._make_provider()
        assert p.get_max_output_tokens() == 65_536

    def test_supports_tool_calling(self):
        p = self._make_provider()
        assert p.supports_tool_calling() is True

    def test_chat_returns_text(self):
        p = self._make_provider()

        # Build a mock response
        mock_resp = MagicMock()
        mock_resp.text = "Hello from Gemini"
        mock_resp.candidates = []  # no tool calls
        mock_resp.usage_metadata = None
        p._client.models.generate_content = MagicMock(return_value=mock_resp)

        r = p.chat([AIMessage(role="user", content="hi")])
        assert r.content == "Hello from Gemini"
        assert r.tool_calls == []

    def test_chat_with_tool_calls(self):
        p = self._make_provider()

        mock_fc = MagicMock()
        mock_fc.name = "schematic_open"
        mock_fc.args = {"path": "test.kicad_sch"}

        mock_part = MagicMock()
        mock_part.function_call = mock_fc

        mock_content = MagicMock()
        mock_content.parts = [mock_part]

        mock_candidate = MagicMock()
        mock_candidate.content = mock_content

        mock_resp = MagicMock()
        mock_resp.text = ""
        mock_resp.candidates = [mock_candidate]
        mock_resp.usage_metadata = None
        p._client.models.generate_content = MagicMock(return_value=mock_resp)

        r = p.chat([AIMessage(role="user", content="open schematic")])
        assert len(r.tool_calls) == 1
        assert r.tool_calls[0].name == "schematic_open"
        assert r.tool_calls[0].arguments == {"path": "test.kicad_sch"}

    def test_chat_usage_metadata(self):
        p = self._make_provider()

        mock_meta = MagicMock()
        mock_meta.prompt_token_count = 10
        mock_meta.candidates_token_count = 20

        mock_resp = MagicMock()
        mock_resp.text = "ok"
        mock_resp.candidates = []
        mock_resp.usage_metadata = mock_meta
        p._client.models.generate_content = MagicMock(return_value=mock_resp)

        r = p.chat([AIMessage(role="user", content="hi")])
        assert r.usage == {"input_tokens": 10, "output_tokens": 20}

    def test_mcp_schema_to_gemini_conversion(self):
        tool = _mcp_schema_to_gemini(SAMPLE_TOOL)
        assert tool.function_declarations[0].name == "schematic_open"

    def test_chat_with_tool_calls_unique_ids(self):
        """Multiple calls to the same tool get distinct IDs."""
        p = self._make_provider()

        def _make_fc(name):
            fc = MagicMock()
            fc.name = name
            fc.args = {}
            part = MagicMock()
            part.function_call = fc
            return part

        mock_content = MagicMock()
        mock_content.parts = [
            _make_fc("schematic_open"),
            _make_fc("schematic_open"),  # same tool twice
        ]
        mock_candidate = MagicMock()
        mock_candidate.content = mock_content

        mock_resp = MagicMock()
        mock_resp.text = ""
        mock_resp.candidates = [mock_candidate]
        mock_resp.usage_metadata = None
        p._client.models.generate_content = MagicMock(return_value=mock_resp)

        r = p.chat([AIMessage(role="user", content="open twice")])
        assert len(r.tool_calls) == 2
        ids = [tc.id for tc in r.tool_calls]
        # IDs must be unique even though the tool name is the same
        assert ids[0] != ids[1]
        # But both should be for the same function
        assert r.tool_calls[0].name == "schematic_open"
        assert r.tool_calls[1].name == "schematic_open"

    def test_messages_to_gemini_function_response_name_lookup(self):
        """Tool result FunctionResponse.name resolves to function name via id_to_name map."""
        tc = AIToolCall(id="schematic_open_abc12345", name="schematic_open", arguments={})
        tr = AIToolResult(tool_call_id="schematic_open_abc12345", content="ok")
        msgs = [
            AIMessage(role="user", content="do it"),
            AIMessage(role="assistant", content="", tool_calls=[tc]),
            AIMessage(role="tool", tool_results=[tr]),
        ]
        result = _messages_to_gemini(msgs)
        # The tool result content should be the last item with role="user"
        tool_resp_content = result[-1]
        assert tool_resp_content.role == "user"
        func_resp = tool_resp_content.parts[0].function_response
        # FunctionResponse.name must be the actual function name, not the synthetic ID
        assert func_resp.name == "schematic_open"


        msgs = [
            AIMessage(role="system", content="You are KiAssist"),
            AIMessage(role="user", content="hello"),
        ]
        result = _messages_to_gemini(msgs)
        assert all(c.role != "system" for c in result)
        assert any(c.role == "user" for c in result)

    def test_send_message_backward_compat(self):
        """Legacy send_message() API should still work."""
        p = self._make_provider()

        mock_resp = MagicMock()
        mock_resp.text = "Legacy response"
        mock_resp.candidates = []
        mock_resp.usage_metadata = None
        p._client.models.generate_content = MagicMock(return_value=mock_resp)

        result = p.send_message("hello")
        assert result == "Legacy response"


# ---------------------------------------------------------------------------
# Claude Provider tests (mocked)
# ---------------------------------------------------------------------------


class TestClaudeProvider:
    def _make_provider(self):
        with patch("kiassist_utils.ai.claude._anthropic.Anthropic"), \
             patch("kiassist_utils.ai.claude._anthropic.AsyncAnthropic"):
            return ClaudeProvider(api_key="fake_key", model="sonnet")

    def test_model_name_resolved(self):
        p = self._make_provider()
        assert p._model_name == "claude-sonnet-4-5"

    def test_unknown_model_passthrough(self):
        with patch("kiassist_utils.ai.claude._anthropic.Anthropic"), \
             patch("kiassist_utils.ai.claude._anthropic.AsyncAnthropic"):
            p = ClaudeProvider(api_key="fake", model="claude-custom-3")
        assert p._model_name == "claude-custom-3"

    def test_get_context_window(self):
        p = self._make_provider()
        assert p.get_context_window() == 200_000

    def test_supports_tool_calling(self):
        p = self._make_provider()
        assert p.supports_tool_calling() is True

    def test_chat_returns_text(self):
        p = self._make_provider()

        mock_block = MagicMock()
        mock_block.type = "text"
        mock_block.text = "Hello from Claude"

        mock_resp = MagicMock()
        mock_resp.content = [mock_block]
        mock_resp.usage = None
        p._client.messages.create = MagicMock(return_value=mock_resp)

        r = p.chat([AIMessage(role="user", content="hi")])
        assert r.content == "Hello from Claude"
        assert r.tool_calls == []

    def test_chat_with_tool_calls(self):
        p = self._make_provider()

        mock_block = MagicMock()
        mock_block.type = "tool_use"
        mock_block.id = "tool_call_1"
        mock_block.name = "schematic_open"
        mock_block.input = {"path": "test.kicad_sch"}

        mock_resp = MagicMock()
        mock_resp.content = [mock_block]
        mock_resp.usage = None
        p._client.messages.create = MagicMock(return_value=mock_resp)

        r = p.chat([AIMessage(role="user", content="open it")])
        assert len(r.tool_calls) == 1
        assert r.tool_calls[0].id == "tool_call_1"
        assert r.tool_calls[0].name == "schematic_open"

    def test_import_error_without_anthropic(self):
        import kiassist_utils.ai.claude as claude_mod
        orig = claude_mod._ANTHROPIC_AVAILABLE
        try:
            claude_mod._ANTHROPIC_AVAILABLE = False
            with pytest.raises(ImportError, match="anthropic"):
                ClaudeProvider(api_key="fake")
        finally:
            claude_mod._ANTHROPIC_AVAILABLE = orig

    def test_mcp_schema_to_claude(self):
        result = _mcp_schema_to_claude(SAMPLE_TOOL)
        assert result["name"] == "schematic_open"
        assert "input_schema" in result

    def test_messages_to_claude_skips_system(self):
        msgs = [
            AIMessage(role="system", content="You are KiAssist"),
            AIMessage(role="user", content="hello"),
        ]
        result = _messages_to_claude(msgs)
        assert all(m["role"] != "system" for m in result)

    def test_messages_to_claude_tool_results(self):
        msgs = [
            AIMessage(role="user", content="do it"),
            AIMessage(
                role="assistant",
                content="",
                tool_calls=[AIToolCall(id="c1", name="tool_a", arguments={})],
            ),
            AIMessage(
                role="tool",
                tool_results=[
                    AIToolResult(tool_call_id="c1", content="done", is_error=False)
                ],
            ),
        ]
        result = _messages_to_claude(msgs)
        # Last message should be user role with tool_result block
        last = result[-1]
        assert last["role"] == "user"
        assert last["content"][0]["type"] == "tool_result"


# ---------------------------------------------------------------------------
# OpenAI Provider tests (mocked)
# ---------------------------------------------------------------------------


class TestOpenAIProvider:
    def _make_provider(self):
        with patch("kiassist_utils.ai.openai._openai.OpenAI"), \
             patch("kiassist_utils.ai.openai._openai.AsyncOpenAI"):
            return OpenAIProvider(api_key="fake_key", model="gpt-4o")

    def test_model_name_resolved(self):
        p = self._make_provider()
        assert p._model_name == "gpt-4o"

    def test_unknown_model_passthrough(self):
        with patch("kiassist_utils.ai.openai._openai.OpenAI"), \
             patch("kiassist_utils.ai.openai._openai.AsyncOpenAI"):
            p = OpenAIProvider(api_key="fake", model="my-custom-model")
        assert p._model_name == "my-custom-model"

    def test_get_context_window(self):
        p = self._make_provider()
        assert p.get_context_window() == 128_000

    def test_supports_tool_calling_gpt4o(self):
        p = self._make_provider()
        assert p.supports_tool_calling() is True

    def test_supports_tool_calling_o1(self):
        with patch("kiassist_utils.ai.openai._openai.OpenAI"), \
             patch("kiassist_utils.ai.openai._openai.AsyncOpenAI"):
            p = OpenAIProvider(api_key="fake", model="o1")
        assert p.supports_tool_calling() is False

    def test_chat_returns_text(self):
        p = self._make_provider()

        mock_message = MagicMock()
        mock_message.content = "Hello from GPT-4o"
        mock_message.tool_calls = None

        mock_choice = MagicMock()
        mock_choice.message = mock_message

        mock_resp = MagicMock()
        mock_resp.choices = [mock_choice]
        mock_resp.usage = None
        p._client.chat.completions.create = MagicMock(return_value=mock_resp)

        r = p.chat([AIMessage(role="user", content="hi")])
        assert r.content == "Hello from GPT-4o"
        assert r.tool_calls == []

    def test_chat_with_tool_calls(self):
        p = self._make_provider()

        mock_tc = MagicMock()
        mock_tc.id = "call_abc"
        mock_tc.function.name = "schematic_open"
        mock_tc.function.arguments = json.dumps({"path": "test.kicad_sch"})

        mock_message = MagicMock()
        mock_message.content = ""
        mock_message.tool_calls = [mock_tc]

        mock_choice = MagicMock()
        mock_choice.message = mock_message

        mock_resp = MagicMock()
        mock_resp.choices = [mock_choice]
        mock_resp.usage = None
        p._client.chat.completions.create = MagicMock(return_value=mock_resp)

        r = p.chat([AIMessage(role="user", content="open it")])
        assert len(r.tool_calls) == 1
        assert r.tool_calls[0].id == "call_abc"
        assert r.tool_calls[0].name == "schematic_open"

    def test_import_error_without_openai(self):
        import kiassist_utils.ai.openai as openai_mod
        orig = openai_mod._OPENAI_AVAILABLE
        try:
            openai_mod._OPENAI_AVAILABLE = False
            with pytest.raises(ImportError, match="openai"):
                OpenAIProvider(api_key="fake")
        finally:
            openai_mod._OPENAI_AVAILABLE = orig

    def test_mcp_schema_to_openai(self):
        result = _mcp_schema_to_openai(SAMPLE_TOOL)
        assert result["type"] == "function"
        assert result["function"]["name"] == "schematic_open"

    def test_messages_to_openai_with_system(self):
        msgs = [AIMessage(role="user", content="hello")]
        result = _messages_to_openai(msgs, system_prompt="You are KiAssist")
        assert result[0]["role"] == "system"
        assert result[1]["role"] == "user"

    def test_messages_to_openai_tool_results(self):
        msgs = [
            AIMessage(role="user", content="do it"),
            AIMessage(
                role="assistant",
                content="",
                tool_calls=[AIToolCall(id="c1", name="tool_a", arguments={})],
            ),
            AIMessage(
                role="tool",
                tool_results=[
                    AIToolResult(tool_call_id="c1", content="done", is_error=False)
                ],
            ),
        ]
        result = _messages_to_openai(msgs)
        assert result[-1]["role"] == "tool"
        assert result[-1]["tool_call_id"] == "c1"


# ---------------------------------------------------------------------------
# ToolExecutor tests (mocked)
# ---------------------------------------------------------------------------


class TestToolExecutor:
    """Tests for the agentic loop in ToolExecutor."""

    def _make_mock_provider(self, responses: List[AIResponse]) -> AIProvider:
        """Create a mock AIProvider that returns responses in sequence."""
        call_count = {"n": 0}

        class MockProvider(AIProvider):
            def chat(self, messages, tools=None, system_prompt=None):
                idx = call_count["n"]
                call_count["n"] += 1
                return responses[idx]

            async def chat_stream(self, messages, tools=None, system_prompt=None):
                yield AIChunk(text="x", is_final=True)

            def get_context_window(self):
                return 4096

            def get_max_output_tokens(self):
                return 512

            def supports_tool_calling(self):
                return True

        return MockProvider()

    def test_run_no_tool_calls(self):
        """Executor returns immediately when AI gives final text response."""
        provider = self._make_mock_provider(
            [AIResponse(content="Final answer", tool_calls=[])]
        )
        executor = ToolExecutor(provider, tool_schemas=[SAMPLE_TOOL])

        result = asyncio.run(
            executor.run([AIMessage(role="user", content="hello")])
        )
        assert result.content == "Final answer"

    def test_run_with_one_tool_call(self, monkeypatch):
        """Executor dispatches tool, then returns final text."""
        tool_call = AIToolCall(
            id="c1", name="schematic_open", arguments={"path": "test.kicad_sch"}
        )
        provider = self._make_mock_provider(
            [
                AIResponse(content="", tool_calls=[tool_call]),
                AIResponse(content="Schematic opened", tool_calls=[]),
            ]
        )

        mock_in_process = AsyncMock(return_value={"status": "ok", "data": {}})

        import kiassist_utils.ai.tool_executor as te_mod
        monkeypatch.setattr(te_mod, "in_process_call", mock_in_process)

        executor = ToolExecutor(provider, tool_schemas=[SAMPLE_TOOL])
        result = asyncio.run(
            executor.run([AIMessage(role="user", content="open it")])
        )

        assert result.content == "Schematic opened"
        mock_in_process.assert_called_once_with(
            "schematic_open", {"path": "test.kicad_sch"}
        )

    def test_run_max_iterations_exceeded(self, monkeypatch):
        """Executor raises RuntimeError when max_iterations is exceeded."""
        tool_call = AIToolCall(id="c1", name="loop_tool", arguments={})
        # Always returns tool calls → infinite loop
        provider = self._make_mock_provider(
            [AIResponse(content="", tool_calls=[tool_call])] * 5
        )

        mock_in_process = AsyncMock(return_value={"status": "ok", "data": {}})

        import kiassist_utils.ai.tool_executor as te_mod
        monkeypatch.setattr(te_mod, "in_process_call", mock_in_process)

        executor = ToolExecutor(provider, max_iterations=3, tool_schemas=[SAMPLE_TOOL])

        with pytest.raises(RuntimeError, match="max_iterations"):
            asyncio.run(
                executor.run([AIMessage(role="user", content="loop")])
            )

    def test_run_callbacks_invoked(self, monkeypatch):
        """on_tool_call and on_tool_result callbacks are invoked."""
        tool_call = AIToolCall(id="c1", name="my_tool", arguments={})
        provider = self._make_mock_provider(
            [
                AIResponse(content="", tool_calls=[tool_call]),
                AIResponse(content="done", tool_calls=[]),
            ]
        )

        call_log: List[str] = []

        def on_call(tc: AIToolCall):
            call_log.append(f"call:{tc.name}")

        def on_result(tc: AIToolCall, tr: AIToolResult):
            call_log.append(f"result:{tc.name}:{tr.is_error}")

        mock_in_process = AsyncMock(return_value={"status": "ok"})

        import kiassist_utils.ai.tool_executor as te_mod
        monkeypatch.setattr(te_mod, "in_process_call", mock_in_process)

        executor = ToolExecutor(
            provider,
            tool_schemas=[SAMPLE_TOOL],
            on_tool_call=on_call,
            on_tool_result=on_result,
        )
        asyncio.run(executor.run([AIMessage(role="user", content="go")]))

        assert "call:my_tool" in call_log
        assert "result:my_tool:False" in call_log

    def test_run_tool_error_captured(self, monkeypatch):
        """Tool execution errors are captured as AIToolResult with is_error=True."""
        tool_call = AIToolCall(id="c1", name="broken_tool", arguments={})
        provider = self._make_mock_provider(
            [
                AIResponse(content="", tool_calls=[tool_call]),
                AIResponse(content="handled error", tool_calls=[]),
            ]
        )

        async def _fail(*args, **kwargs):
            raise RuntimeError("Tool exploded")

        import kiassist_utils.ai.tool_executor as te_mod
        monkeypatch.setattr(te_mod, "in_process_call", _fail)

        executor = ToolExecutor(provider, tool_schemas=[SAMPLE_TOOL])
        result = asyncio.run(
            executor.run([AIMessage(role="user", content="break it")])
        )

        assert result.content == "handled error"

    def test_run_parallel_tool_calls(self, monkeypatch):
        """Multiple tool calls in one iteration are executed concurrently."""
        tc1 = AIToolCall(id="c1", name="tool_one", arguments={})
        tc2 = AIToolCall(id="c2", name="tool_two", arguments={})

        provider = self._make_mock_provider(
            [
                AIResponse(content="", tool_calls=[tc1, tc2]),
                AIResponse(content="parallel done", tool_calls=[]),
            ]
        )

        called: List[str] = []

        async def _mock_call(name, args):
            called.append(name)
            return {"status": "ok"}

        import kiassist_utils.ai.tool_executor as te_mod
        monkeypatch.setattr(te_mod, "in_process_call", _mock_call)

        executor = ToolExecutor(provider, tool_schemas=[SAMPLE_TOOL])
        asyncio.run(executor.run([AIMessage(role="user", content="parallel")]))
        assert set(called) == {"tool_one", "tool_two"}


# ---------------------------------------------------------------------------
# ApiKeyStore multi-provider tests
# ---------------------------------------------------------------------------


class TestApiKeyStoreMultiProvider:
    def setup_method(self):
        self.store = ApiKeyStore()
        # Avoid touching real environment variables – clear them first
        for env_var in ("GEMINI_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY"):
            os.environ.pop(env_var, None)
        self.store._memory_keys = {p: None for p in ("gemini", "claude", "openai")}
        # Disable keyring to avoid side effects
        self.store._keyring_available = False

    def test_set_and_get_gemini(self, tmp_path, monkeypatch):
        monkeypatch.setattr(self.store, "_get_config_path", lambda: tmp_path / "config.json")
        monkeypatch.setattr(self.store, "_ensure_config_dir", lambda: tmp_path / "config.json")
        self.store.set_api_key("gemini-key", "gemini")
        assert self.store.get_api_key("gemini") == "gemini-key"

    def test_set_and_get_claude(self, tmp_path, monkeypatch):
        monkeypatch.setattr(self.store, "_get_config_path", lambda: tmp_path / "config.json")
        monkeypatch.setattr(self.store, "_ensure_config_dir", lambda: tmp_path / "config.json")
        self.store.set_api_key("claude-key", "claude")
        assert self.store.get_api_key("claude") == "claude-key"

    def test_set_and_get_openai(self, tmp_path, monkeypatch):
        monkeypatch.setattr(self.store, "_get_config_path", lambda: tmp_path / "config.json")
        monkeypatch.setattr(self.store, "_ensure_config_dir", lambda: tmp_path / "config.json")
        self.store.set_api_key("openai-key", "openai")
        assert self.store.get_api_key("openai") == "openai-key"

    def test_default_provider_is_gemini(self, tmp_path, monkeypatch):
        monkeypatch.setattr(self.store, "_get_config_path", lambda: tmp_path / "config.json")
        monkeypatch.setattr(self.store, "_ensure_config_dir", lambda: tmp_path / "config.json")
        self.store.set_api_key("default-key")
        assert self.store.get_api_key() == "default-key"
        assert self.store.get_api_key("gemini") == "default-key"

    def test_providers_are_independent(self, tmp_path, monkeypatch):
        monkeypatch.setattr(self.store, "_get_config_path", lambda: tmp_path / "config.json")
        monkeypatch.setattr(self.store, "_ensure_config_dir", lambda: tmp_path / "config.json")
        self.store.set_api_key("g-key", "gemini")
        self.store.set_api_key("c-key", "claude")
        self.store.set_api_key("o-key", "openai")
        assert self.store.get_api_key("gemini") == "g-key"
        assert self.store.get_api_key("claude") == "c-key"
        assert self.store.get_api_key("openai") == "o-key"

    def test_has_api_key(self, tmp_path, monkeypatch):
        monkeypatch.setattr(self.store, "_get_config_path", lambda: tmp_path / "config.json")
        monkeypatch.setattr(self.store, "_ensure_config_dir", lambda: tmp_path / "config.json")
        assert not self.store.has_api_key("claude")
        self.store.set_api_key("c-key", "claude")
        assert self.store.has_api_key("claude")

    def test_clear_api_key(self, tmp_path, monkeypatch):
        monkeypatch.setattr(self.store, "_get_config_path", lambda: tmp_path / "config.json")
        monkeypatch.setattr(self.store, "_ensure_config_dir", lambda: tmp_path / "config.json")
        self.store.set_api_key("g-key", "gemini")
        self.store.clear_api_key("gemini")
        assert self.store.get_api_key("gemini") is None

    def test_unknown_provider_raises(self):
        with pytest.raises(ValueError, match="Unknown provider"):
            self.store.get_api_key("unknown_provider")

    def test_env_var_priority_gemini(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "env-gemini-key")
        self.store._memory_keys["gemini"] = "memory-key"
        assert self.store.get_api_key("gemini") == "env-gemini-key"

    def test_env_var_priority_claude(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "env-claude-key")
        assert self.store.get_api_key("claude") == "env-claude-key"

    def test_env_var_priority_openai(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "env-openai-key")
        assert self.store.get_api_key("openai") == "env-openai-key"

    def test_backward_compat_memory_key_property(self):
        """_memory_key property still works for Gemini (backward compat)."""
        self.store._memory_key = "old-style-key"
        assert self.store._memory_keys["gemini"] == "old-style-key"
        assert self.store._memory_key == "old-style-key"

    def test_file_persistence_multiple_providers(self, tmp_path, monkeypatch):
        """Multiple provider keys co-exist in the same config.json."""
        config_path = tmp_path / "config.json"
        monkeypatch.setattr(self.store, "_get_config_path", lambda: config_path)
        monkeypatch.setattr(self.store, "_ensure_config_dir", lambda: config_path)
        config_path.parent.mkdir(parents=True, exist_ok=True)

        self.store._save_to_file("g-key", "gemini")
        self.store._save_to_file("c-key", "claude")
        self.store._save_to_file("o-key", "openai")

        assert self.store._load_from_file("gemini") == "g-key"
        assert self.store._load_from_file("claude") == "c-key"
        assert self.store._load_from_file("openai") == "o-key"

    def test_delete_from_file_single_provider(self, tmp_path, monkeypatch):
        """Deleting one provider key does not affect others in config.json."""
        config_path = tmp_path / "config.json"
        monkeypatch.setattr(self.store, "_get_config_path", lambda: config_path)
        monkeypatch.setattr(self.store, "_ensure_config_dir", lambda: config_path)
        config_path.parent.mkdir(parents=True, exist_ok=True)

        self.store._save_to_file("g-key", "gemini")
        self.store._save_to_file("c-key", "claude")
        self.store._delete_from_file("gemini")

        assert self.store._load_from_file("gemini") is None
        assert self.store._load_from_file("claude") == "c-key"

    def test_save_to_file_handles_non_dict_json(self, tmp_path, monkeypatch):
        """_save_to_file recovers gracefully if config.json contains a non-dict value."""
        config_path = tmp_path / "config.json"
        monkeypatch.setattr(self.store, "_get_config_path", lambda: config_path)
        monkeypatch.setattr(self.store, "_ensure_config_dir", lambda: config_path)
        config_path.parent.mkdir(parents=True, exist_ok=True)

        # Write a list (non-dict) to config.json to simulate corruption
        config_path.write_text("[1, 2, 3]", encoding="utf-8")

        # Should succeed (reset to fresh dict) rather than raising TypeError
        result = self.store._save_to_file("g-key", "gemini")
        assert result is True
        assert self.store._load_from_file("gemini") == "g-key"

    def test_save_to_file_handles_json_string_value(self, tmp_path, monkeypatch):
        """_save_to_file recovers when config.json contains a bare JSON string."""
        config_path = tmp_path / "config.json"
        monkeypatch.setattr(self.store, "_get_config_path", lambda: config_path)
        monkeypatch.setattr(self.store, "_ensure_config_dir", lambda: config_path)
        config_path.parent.mkdir(parents=True, exist_ok=True)

        config_path.write_text('"just-a-string"', encoding="utf-8")

        result = self.store._save_to_file("o-key", "openai")
        assert result is True
        assert self.store._load_from_file("openai") == "o-key"


# ---------------------------------------------------------------------------
# OllamaProvider tests
# ---------------------------------------------------------------------------

from kiassist_utils.ai.ollama import OllamaProvider, _DEFAULT_CONTEXT_WINDOW, _DEFAULT_MAX_OUTPUT_TOKENS


class TestOllamaProvider:
    def _make_provider(self, model="llama3.2", base_url="http://localhost:11434/v1"):
        with patch("kiassist_utils.ai.openai._openai.OpenAI"), \
             patch("kiassist_utils.ai.openai._openai.AsyncOpenAI"):
            return OllamaProvider(model=model, base_url=base_url)

    def test_provider_name(self):
        p = self._make_provider()
        assert p.provider_name == "OllamaProvider"

    def test_model_name_passes_through(self):
        p = self._make_provider(model="mistral")
        assert p.model_name == "mistral"

    def test_base_url_stored(self):
        p = self._make_provider(base_url="http://localhost:1234/v1")
        assert p.base_url == "http://localhost:1234/v1"

    def test_get_context_window(self):
        p = self._make_provider()
        assert p.get_context_window() == _DEFAULT_CONTEXT_WINDOW

    def test_get_max_output_tokens(self):
        p = self._make_provider()
        assert p.get_max_output_tokens() == _DEFAULT_MAX_OUTPUT_TOKENS

    def test_supports_tool_calling(self):
        p = self._make_provider()
        assert p.supports_tool_calling() is True

    def test_chat_delegates_to_delegate(self):
        p = self._make_provider()
        fake_response = AIResponse(content="local model says hi", tool_calls=[], usage={})
        p._delegate.chat = MagicMock(return_value=fake_response)
        msgs = [AIMessage(role="user", content="hello")]
        result = p.chat(msgs)
        assert result.content == "local model says hi"
        p._delegate.chat.assert_called_once_with(msgs, None, None)

    def test_default_base_url_is_ollama(self):
        from kiassist_utils.ai.ollama import _DEFAULT_BASE_URL
        assert "11434" in _DEFAULT_BASE_URL  # Ollama default port

    def test_exported_from_package(self):
        from kiassist_utils.ai import OllamaProvider as _OP
        assert _OP is OllamaProvider


# ---------------------------------------------------------------------------
# ApiKeyStore: local provider tests
# ---------------------------------------------------------------------------


class TestApiKeyStoreLocalProvider:
    def setup_method(self):
        self.store = ApiKeyStore()
        os.environ.pop("LOCAL_BASE_URL", None)
        self.store._memory_keys = {p: None for p in ("gemini", "claude", "openai", "local")}
        self.store._keyring_available = False

    def test_set_and_get_local_base_url(self, tmp_path, monkeypatch):
        monkeypatch.setattr(self.store, "_get_config_path", lambda: tmp_path / "config.json")
        monkeypatch.setattr(self.store, "_ensure_config_dir", lambda: tmp_path / "config.json")
        self.store.set_api_key("http://localhost:1234/v1", "local")
        assert self.store.get_api_key("local") == "http://localhost:1234/v1"

    def test_local_base_url_env_var(self, monkeypatch):
        monkeypatch.setenv("LOCAL_BASE_URL", "http://192.168.1.100:11434/v1")
        assert self.store.get_api_key("local") == "http://192.168.1.100:11434/v1"

    def test_local_url_stored_in_config_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr(self.store, "_get_config_path", lambda: tmp_path / "config.json")
        monkeypatch.setattr(self.store, "_ensure_config_dir", lambda: tmp_path / "config.json")
        self.store._save_to_file("http://localhost:11434/v1", "local")
        assert self.store._load_from_file("local") == "http://localhost:11434/v1"

    def test_local_key_does_not_interfere_with_others(self, tmp_path, monkeypatch):
        monkeypatch.setattr(self.store, "_get_config_path", lambda: tmp_path / "config.json")
        monkeypatch.setattr(self.store, "_ensure_config_dir", lambda: tmp_path / "config.json")
        self.store.set_api_key("http://localhost:11434/v1", "local")
        self.store.set_api_key("AIzaKey", "gemini")
        assert self.store.get_api_key("local") == "http://localhost:11434/v1"
        assert self.store.get_api_key("gemini") == "AIzaKey"
