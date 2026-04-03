"""Anthropic Claude AI provider implementation.

Implements :class:`~kiassist_utils.ai.base.AIProvider` using the
``anthropic`` Python SDK.  The provider supports:

* Multi-turn conversations.
* MCP tool schemas converted to Anthropic ``ToolParam`` dicts.
* Synchronous and streaming response modes.
* Extended thinking for complex operations.

Supported models (``model`` parameter shortcuts):

====================  =========================================
Shortcut              Actual model ID
====================  =========================================
``sonnet``            ``claude-sonnet-4-5``
``sonnet-4``          ``claude-sonnet-4-5``
``opus``              ``claude-opus-4-5``
``opus-4``            ``claude-opus-4-5``
``haiku``             ``claude-haiku-4-5``
====================  =========================================

The ``anthropic`` package is an optional dependency.  Importing this module
without it installed raises :class:`ImportError` with a helpful message.
"""

from __future__ import annotations

from typing import Any, AsyncIterator, Dict, List, Optional

from .base import (
    AIChunk,
    AIMessage,
    AIProvider,
    AIResponse,
    AIToolCall,
    AIToolResult,
    ToolSchema,
)

try:
    import anthropic as _anthropic
    from anthropic.types import (
        MessageParam,
        TextBlockParam,
        ToolResultBlockParam,
        ToolUseBlockParam,
    )
    _ANTHROPIC_AVAILABLE = True
except ImportError:  # pragma: no cover
    _ANTHROPIC_AVAILABLE = False

# ---------------------------------------------------------------------------
# Model map
# ---------------------------------------------------------------------------

_MODEL_MAP: Dict[str, str] = {
    "sonnet": "claude-sonnet-4-5",
    "sonnet-4": "claude-sonnet-4-5",
    "opus": "claude-opus-4-5",
    "opus-4": "claude-opus-4-5",
    "haiku": "claude-haiku-4-5",
}

_DEFAULT_MODEL = "sonnet"

_CONTEXT_WINDOWS: Dict[str, int] = {
    "claude-sonnet-4-5": 200_000,
    "claude-opus-4-5": 200_000,
    "claude-haiku-4-5": 200_000,
}

_MAX_OUTPUT_TOKENS: Dict[str, int] = {
    "claude-sonnet-4-5": 8_192,
    "claude-opus-4-5": 8_192,
    "claude-haiku-4-5": 4_096,
}

# ---------------------------------------------------------------------------
# Schema conversion helpers
# ---------------------------------------------------------------------------


def _mcp_schema_to_claude(tool: ToolSchema) -> Dict[str, Any]:
    """Convert an MCP tool schema to an Anthropic ``ToolParam`` dict.

    Args:
        tool: MCP-style tool schema.

    Returns:
        Dict compatible with ``anthropic.types.ToolParam``.
    """
    return {
        "name": tool["name"],
        "description": tool.get("description", ""),
        "input_schema": tool.get("inputSchema", {"type": "object", "properties": {}}),
    }


def _messages_to_claude(messages: List[AIMessage]) -> List[Dict[str, Any]]:
    """Convert :class:`AIMessage` list to Anthropic ``MessageParam`` list.

    System messages are filtered out (handled via the ``system`` parameter).

    Args:
        messages: Ordered conversation turns.

    Returns:
        List of message dicts accepted by the Anthropic client.
    """
    result: List[Dict[str, Any]] = []
    for msg in messages:
        if msg.role == "system":
            continue

        if msg.role == "user":
            if msg.content:
                result.append({"role": "user", "content": msg.content})

        elif msg.role == "assistant":
            content: List[Dict[str, Any]] = []
            if msg.content:
                content.append({"type": "text", "text": msg.content})
            for tc in msg.tool_calls:
                content.append({
                    "type": "tool_use",
                    "id": tc.id,
                    "name": tc.name,
                    "input": tc.arguments,
                })
            if content:
                result.append({"role": "assistant", "content": content})

        elif msg.role == "tool":
            # Tool results are sent as a user message with tool_result blocks
            content = []
            for tr in msg.tool_results:
                content.append({
                    "type": "tool_result",
                    "tool_use_id": tr.tool_call_id,
                    "content": tr.content,
                    "is_error": tr.is_error,
                })
            if content:
                result.append({"role": "user", "content": content})

    return result


def _extract_tool_calls(response: Any) -> List[AIToolCall]:
    """Extract tool calls from an Anthropic response.

    Args:
        response: An ``anthropic.types.Message`` object.

    Returns:
        List of :class:`AIToolCall`.
    """
    tool_calls: List[AIToolCall] = []
    for block in getattr(response, "content", []):
        if getattr(block, "type", None) == "tool_use":
            tool_calls.append(
                AIToolCall(
                    id=block.id,
                    name=block.name,
                    arguments=dict(block.input) if block.input else {},
                )
            )
    return tool_calls


def _extract_text(response: Any) -> str:
    """Extract plain text from an Anthropic response.

    Args:
        response: An ``anthropic.types.Message`` object.

    Returns:
        Concatenated text from all text blocks.
    """
    parts: List[str] = []
    for block in getattr(response, "content", []):
        if getattr(block, "type", None) == "text":
            parts.append(block.text)
    return "".join(parts)


# ---------------------------------------------------------------------------
# Provider class
# ---------------------------------------------------------------------------


class ClaudeProvider(AIProvider):
    """Anthropic Claude AI provider.

    Args:
        api_key:         Anthropic API key.
        model:           Model shortcut or full model ID.
        enable_thinking: Enable extended thinking (Sonnet/Opus only).
        thinking_budget: Token budget for extended thinking (default 5000).

    Raises:
        ImportError: If the ``anthropic`` package is not installed.
    """

    def __init__(
        self,
        api_key: str,
        model: str = _DEFAULT_MODEL,
        enable_thinking: bool = False,
        thinking_budget: int = 5_000,
    ) -> None:
        if not _ANTHROPIC_AVAILABLE:
            raise ImportError(
                "The 'anthropic' package is required for ClaudeProvider. "
                "Install it with: pip install anthropic>=0.30.0"
            )
        self._api_key = api_key
        self._model_shortcut = model
        self._model_name = _MODEL_MAP.get(model, model)
        self._enable_thinking = enable_thinking
        self._thinking_budget = thinking_budget
        self._client = _anthropic.Anthropic(api_key=api_key)
        self._async_client = _anthropic.AsyncAnthropic(api_key=api_key)

    # ------------------------------------------------------------------
    # AIProvider interface
    # ------------------------------------------------------------------

    def get_context_window(self) -> int:
        return _CONTEXT_WINDOWS.get(self._model_name, 200_000)

    def get_max_output_tokens(self) -> int:
        return _MAX_OUTPUT_TOKENS.get(self._model_name, 8_192)

    def supports_tool_calling(self) -> bool:
        return True

    def chat(
        self,
        messages: List[AIMessage],
        tools: Optional[List[ToolSchema]] = None,
        system_prompt: Optional[str] = None,
    ) -> AIResponse:
        """Send messages to Claude and return an :class:`AIResponse`.

        Args:
            messages:      Conversation history.
            tools:         Optional MCP tool schemas.
            system_prompt: Optional system instruction.

        Returns:
            :class:`AIResponse` with text content and/or tool calls.

        Raises:
            Exception: On Anthropic API errors.
        """
        claude_messages = _messages_to_claude(messages)

        kwargs: Dict[str, Any] = {
            "model": self._model_name,
            "max_tokens": self.get_max_output_tokens(),
            "messages": claude_messages,
        }

        if system_prompt:
            kwargs["system"] = system_prompt

        if tools:
            kwargs["tools"] = [_mcp_schema_to_claude(t) for t in tools]

        if self._enable_thinking:
            kwargs["thinking"] = {
                "type": "enabled",
                "budget_tokens": self._thinking_budget,
            }

        try:
            response = self._client.messages.create(**kwargs)
        except Exception as exc:
            raise Exception(f"Claude API error: {exc}") from exc

        tool_calls = _extract_tool_calls(response)
        text = _extract_text(response) if not tool_calls else ""

        usage: Dict[str, int] = {}
        if hasattr(response, "usage") and response.usage:
            usage = {
                "input_tokens": getattr(response.usage, "input_tokens", 0) or 0,
                "output_tokens": getattr(response.usage, "output_tokens", 0) or 0,
            }

        return AIResponse(content=text, tool_calls=tool_calls, usage=usage)

    async def chat_stream(
        self,
        messages: List[AIMessage],
        tools: Optional[List[ToolSchema]] = None,
        system_prompt: Optional[str] = None,
    ) -> AsyncIterator[AIChunk]:
        """Stream a Claude response using the native async client.

        Uses :class:`anthropic.AsyncAnthropic` so the event loop is not
        blocked during network streaming.  The same ``thinking`` configuration
        applied in :meth:`chat` is also applied here so streaming and
        non-streaming behaviour remain consistent.

        Args:
            messages:      Conversation history.
            tools:         Optional MCP tool schemas.
            system_prompt: Optional system instruction.

        Yields:
            :class:`AIChunk`; final chunk has ``is_final=True``.

        Raises:
            Exception: On Anthropic API errors.
        """
        import json as _json

        claude_messages = _messages_to_claude(messages)

        kwargs: Dict[str, Any] = {
            "model": self._model_name,
            "max_tokens": self.get_max_output_tokens(),
            "messages": claude_messages,
        }

        if system_prompt:
            kwargs["system"] = system_prompt

        if tools:
            kwargs["tools"] = [_mcp_schema_to_claude(t) for t in tools]

        if self._enable_thinking:
            kwargs["thinking"] = {
                "type": "enabled",
                "budget_tokens": self._thinking_budget,
            }

        accumulated_tool_calls: List[AIToolCall] = []
        current_tool_name: Optional[str] = None
        current_tool_id: Optional[str] = None
        current_tool_input: str = ""
        usage: Dict[str, int] = {}

        try:
            async with self._async_client.messages.stream(**kwargs) as stream:
                async for event in stream:
                    event_type = getattr(event, "type", None)

                    if event_type == "content_block_start":
                        block = getattr(event, "content_block", None)
                        if block and getattr(block, "type", None) == "tool_use":
                            current_tool_name = block.name
                            current_tool_id = block.id
                            current_tool_input = ""

                    elif event_type == "content_block_delta":
                        delta = getattr(event, "delta", None)
                        if delta:
                            delta_type = getattr(delta, "type", None)
                            if delta_type == "text_delta":
                                yield AIChunk(text=getattr(delta, "text", ""))
                            elif delta_type == "input_json_delta":
                                current_tool_input += getattr(
                                    delta, "partial_json", ""
                                )

                    elif event_type == "content_block_stop":
                        if current_tool_name and current_tool_id:
                            try:
                                args = _json.loads(current_tool_input) if current_tool_input else {}
                            except _json.JSONDecodeError:
                                args = {}
                            accumulated_tool_calls.append(
                                AIToolCall(
                                    id=current_tool_id,
                                    name=current_tool_name,
                                    arguments=args,
                                )
                            )
                            current_tool_name = None
                            current_tool_id = None
                            current_tool_input = ""

                final_msg = await stream.get_final_message()
                if hasattr(final_msg, "usage") and final_msg.usage:
                    usage = {
                        "input_tokens": getattr(final_msg.usage, "input_tokens", 0) or 0,
                        "output_tokens": getattr(final_msg.usage, "output_tokens", 0) or 0,
                    }

        except Exception as exc:
            raise Exception(f"Claude stream error: {exc}") from exc

        yield AIChunk(is_final=True, tool_calls=accumulated_tool_calls, usage=usage)
