"""OpenAI AI provider implementation.

Implements :class:`~kiassist_utils.ai.base.AIProvider` using the
``openai`` Python SDK.  The provider supports:

* Multi-turn conversations.
* MCP tool schemas converted to OpenAI function-calling format.
* Synchronous and streaming response modes.

Supported models (``model`` parameter shortcuts):

==========  ==================
Shortcut    Actual model ID
==========  ==================
``gpt-4o``  ``gpt-4o``
``gpt-4o-mini``  ``gpt-4o-mini``
``o3``      ``o3``
``o3-mini`` ``o3-mini``
``o1``      ``o1``
==========  ==================

The ``openai`` package is an optional dependency.  Importing this module
without it installed raises :class:`ImportError` with a helpful message.
"""

from __future__ import annotations

import json
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
    import openai as _openai
    _OPENAI_AVAILABLE = True
except ImportError:  # pragma: no cover
    _OPENAI_AVAILABLE = False

# ---------------------------------------------------------------------------
# Model map
# ---------------------------------------------------------------------------

_MODEL_MAP: Dict[str, str] = {
    "gpt-4o": "gpt-4o",
    "gpt-4o-mini": "gpt-4o-mini",
    "o3": "o3",
    "o3-mini": "o3-mini",
    "o1": "o1",
}

_DEFAULT_MODEL = "gpt-4o"

_CONTEXT_WINDOWS: Dict[str, int] = {
    "gpt-4o": 128_000,
    "gpt-4o-mini": 128_000,
    "o3": 200_000,
    "o3-mini": 200_000,
    "o1": 200_000,
}

_MAX_OUTPUT_TOKENS: Dict[str, int] = {
    "gpt-4o": 16_384,
    "gpt-4o-mini": 16_384,
    "o3": 100_000,
    "o3-mini": 65_536,
    "o1": 100_000,
}

# ---------------------------------------------------------------------------
# Schema conversion helpers
# ---------------------------------------------------------------------------


def _mcp_schema_to_openai(tool: ToolSchema) -> Dict[str, Any]:
    """Convert an MCP tool schema to an OpenAI function-calling tool dict.

    Args:
        tool: MCP-style tool schema.

    Returns:
        Dict compatible with the OpenAI ``tools`` parameter.
    """
    return {
        "type": "function",
        "function": {
            "name": tool["name"],
            "description": tool.get("description", ""),
            "parameters": tool.get(
                "inputSchema",
                {"type": "object", "properties": {}},
            ),
        },
    }


def _messages_to_openai(
    messages: List[AIMessage],
    system_prompt: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Convert :class:`AIMessage` list to OpenAI message dicts.

    Args:
        messages:      Ordered conversation turns.
        system_prompt: Optional system message to prepend.

    Returns:
        List of message dicts accepted by the OpenAI client.
    """
    result: List[Dict[str, Any]] = []

    if system_prompt:
        result.append({"role": "system", "content": system_prompt})

    for msg in messages:
        if msg.role == "system":
            # Inline system messages – merge or append
            result.append({"role": "system", "content": msg.content})

        elif msg.role == "user":
            result.append({"role": "user", "content": msg.content})

        elif msg.role == "assistant":
            openai_msg: Dict[str, Any] = {"role": "assistant"}
            if msg.content:
                openai_msg["content"] = msg.content
            if msg.tool_calls:
                openai_msg["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments),
                        },
                    }
                    for tc in msg.tool_calls
                ]
            result.append(openai_msg)

        elif msg.role == "tool":
            for tr in msg.tool_results:
                result.append({
                    "role": "tool",
                    "tool_call_id": tr.tool_call_id,
                    "content": tr.content,
                })

    return result


def _extract_tool_calls(response: Any) -> List[AIToolCall]:
    """Extract tool calls from an OpenAI response.

    Args:
        response: An ``openai.types.chat.ChatCompletion`` object.

    Returns:
        List of :class:`AIToolCall`.
    """
    tool_calls: List[AIToolCall] = []
    try:
        message = response.choices[0].message
        for tc in (message.tool_calls or []):
            try:
                arguments = json.loads(tc.function.arguments)
            except (json.JSONDecodeError, AttributeError):
                arguments = {}
            tool_calls.append(
                AIToolCall(
                    id=tc.id,
                    name=tc.function.name,
                    arguments=arguments,
                )
            )
    except (AttributeError, IndexError):
        pass
    return tool_calls


def _extract_text(response: Any) -> str:
    """Extract plain text from an OpenAI response.

    Args:
        response: An ``openai.types.chat.ChatCompletion`` object.

    Returns:
        Response text or empty string.
    """
    try:
        return response.choices[0].message.content or ""
    except (AttributeError, IndexError):
        return ""


# ---------------------------------------------------------------------------
# Provider class
# ---------------------------------------------------------------------------


class OpenAIProvider(AIProvider):
    """OpenAI AI provider.

    Args:
        api_key:  OpenAI API key.
        model:    Model shortcut or full model ID.
        base_url: Optional base URL override (for Azure OpenAI or proxies).

    Raises:
        ImportError: If the ``openai`` package is not installed.
    """

    def __init__(
        self,
        api_key: str,
        model: str = _DEFAULT_MODEL,
        base_url: Optional[str] = None,
    ) -> None:
        if not _OPENAI_AVAILABLE:
            raise ImportError(
                "The 'openai' package is required for OpenAIProvider. "
                "Install it with: pip install openai>=1.30.0"
            )
        self._api_key = api_key
        self._model_shortcut = model
        self._model_name = _MODEL_MAP.get(model, model)
        client_kwargs: Dict[str, Any] = {"api_key": api_key}
        if base_url:
            client_kwargs["base_url"] = base_url
        self._client = _openai.OpenAI(**client_kwargs)

    # ------------------------------------------------------------------
    # AIProvider interface
    # ------------------------------------------------------------------

    def get_context_window(self) -> int:
        return _CONTEXT_WINDOWS.get(self._model_name, 128_000)

    def get_max_output_tokens(self) -> int:
        return _MAX_OUTPUT_TOKENS.get(self._model_name, 16_384)

    def supports_tool_calling(self) -> bool:
        # o1 doesn't support tool calling in some versions; others do
        return self._model_name not in {"o1"}

    def chat(
        self,
        messages: List[AIMessage],
        tools: Optional[List[ToolSchema]] = None,
        system_prompt: Optional[str] = None,
    ) -> AIResponse:
        """Send messages to OpenAI and return an :class:`AIResponse`.

        Args:
            messages:      Conversation history.
            tools:         Optional MCP tool schemas.
            system_prompt: Optional system prompt.

        Returns:
            :class:`AIResponse` with text content and/or tool calls.

        Raises:
            Exception: On OpenAI API errors.
        """
        openai_messages = _messages_to_openai(messages, system_prompt)

        kwargs: Dict[str, Any] = {
            "model": self._model_name,
            "messages": openai_messages,
        }

        if tools and self.supports_tool_calling():
            kwargs["tools"] = [_mcp_schema_to_openai(t) for t in tools]
            kwargs["tool_choice"] = "auto"

        try:
            response = self._client.chat.completions.create(**kwargs)
        except Exception as exc:
            raise Exception(f"OpenAI API error: {exc}") from exc

        tool_calls = _extract_tool_calls(response)
        text = _extract_text(response) if not tool_calls else ""

        usage: Dict[str, int] = {}
        if hasattr(response, "usage") and response.usage:
            usage = {
                "input_tokens": getattr(response.usage, "prompt_tokens", 0) or 0,
                "output_tokens": getattr(response.usage, "completion_tokens", 0) or 0,
            }

        return AIResponse(content=text, tool_calls=tool_calls, usage=usage)

    async def chat_stream(
        self,
        messages: List[AIMessage],
        tools: Optional[List[ToolSchema]] = None,
        system_prompt: Optional[str] = None,
    ) -> AsyncIterator[AIChunk]:
        """Stream an OpenAI response.

        Args:
            messages:      Conversation history.
            tools:         Optional MCP tool schemas.
            system_prompt: Optional system prompt.

        Yields:
            :class:`AIChunk`; final chunk has ``is_final=True``.

        Raises:
            Exception: On OpenAI API errors.
        """
        openai_messages = _messages_to_openai(messages, system_prompt)

        kwargs: Dict[str, Any] = {
            "model": self._model_name,
            "messages": openai_messages,
            "stream": True,
            "stream_options": {"include_usage": True},
        }

        if tools and self.supports_tool_calling():
            kwargs["tools"] = [_mcp_schema_to_openai(t) for t in tools]
            kwargs["tool_choice"] = "auto"

        # Accumulate tool call deltas across chunks
        # Structure: {index: {"id": str, "name": str, "arguments": str}}
        tool_call_accum: Dict[int, Dict[str, str]] = {}
        usage: Dict[str, int] = {}

        try:
            stream = self._client.chat.completions.create(**kwargs)
            for chunk in stream:
                # Usage is reported on the final chunk when stream_options used
                if hasattr(chunk, "usage") and chunk.usage:
                    usage = {
                        "input_tokens": getattr(chunk.usage, "prompt_tokens", 0) or 0,
                        "output_tokens": getattr(chunk.usage, "completion_tokens", 0) or 0,
                    }

                if not chunk.choices:
                    continue

                delta = chunk.choices[0].delta

                # Accumulate text
                if delta.content:
                    yield AIChunk(text=delta.content)

                # Accumulate tool call fragments
                for tc_delta in (delta.tool_calls or []):
                    idx = tc_delta.index
                    if idx not in tool_call_accum:
                        tool_call_accum[idx] = {"id": "", "name": "", "arguments": ""}
                    if tc_delta.id:
                        tool_call_accum[idx]["id"] = tc_delta.id
                    if tc_delta.function and tc_delta.function.name:
                        tool_call_accum[idx]["name"] = tc_delta.function.name
                    if tc_delta.function and tc_delta.function.arguments:
                        tool_call_accum[idx]["arguments"] += tc_delta.function.arguments

        except Exception as exc:
            raise Exception(f"OpenAI stream error: {exc}") from exc

        # Build final tool calls
        final_tool_calls: List[AIToolCall] = []
        for _idx, tc_data in sorted(tool_call_accum.items()):
            try:
                args = json.loads(tc_data["arguments"]) if tc_data["arguments"] else {}
            except json.JSONDecodeError:
                args = {}
            final_tool_calls.append(
                AIToolCall(
                    id=tc_data["id"],
                    name=tc_data["name"],
                    arguments=args,
                )
            )

        yield AIChunk(is_final=True, tool_calls=final_tool_calls, usage=usage)
