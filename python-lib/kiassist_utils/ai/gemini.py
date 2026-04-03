"""Google Gemini AI provider implementation.

Implements :class:`~kiassist_utils.ai.base.AIProvider` using the
``google-genai`` SDK (``google.genai``).  The provider supports:

* Multi-turn conversations via the native ``chat`` session API.
* MCP tool schemas converted to Gemini ``FunctionDeclaration`` objects.
* Synchronous and streaming response modes.

Supported models (``model`` parameter shortcuts):

=============  ============================================
Shortcut       Actual model ID
=============  ============================================
``3.1-pro``    ``gemini-3.1-pro-preview``
``3-flash``    ``gemini-3-flash-preview``
``3.1-flash-lite``  ``gemini-3.1-flash-lite-preview``
=============  ============================================
"""

from __future__ import annotations

import json
from typing import Any, AsyncIterator, Dict, List, Optional

from google import genai
from google.genai import errors, types

from .base import (
    AIChunk,
    AIMessage,
    AIProvider,
    AIResponse,
    AIToolCall,
    AIToolResult,
    ToolSchema,
)

# ---------------------------------------------------------------------------
# Model map (shortcut → real model ID)
# ---------------------------------------------------------------------------

_MODEL_MAP: Dict[str, str] = {
    "3.1-pro": "gemini-3.1-pro-preview",
    "3-flash": "gemini-3-flash-preview",
    "3.1-flash-lite": "gemini-3.1-flash-lite-preview",
}

_DEFAULT_MODEL = "3-flash"

# Context window sizes (approximate, in tokens)
_CONTEXT_WINDOWS: Dict[str, int] = {
    "gemini-3.1-pro-preview": 1_000_000,
    "gemini-3-flash-preview": 1_000_000,
    "gemini-3.1-flash-lite-preview": 1_000_000,
}

_MAX_OUTPUT_TOKENS: Dict[str, int] = {
    "gemini-3.1-pro-preview": 65_536,
    "gemini-3-flash-preview": 65_536,
    "gemini-3.1-flash-lite-preview": 32_768,
}


# ---------------------------------------------------------------------------
# Schema conversion helpers
# ---------------------------------------------------------------------------


def _mcp_schema_to_gemini(tool: ToolSchema) -> types.Tool:
    """Convert an MCP tool schema dict to a Gemini :class:`types.Tool`.

    Args:
        tool: MCP-style tool schema with ``name``, ``description``, and
              ``inputSchema`` keys.

    Returns:
        A :class:`types.Tool` containing a single
        :class:`types.FunctionDeclaration`.
    """
    input_schema = tool.get("inputSchema", {})
    properties: Dict[str, Any] = {}
    required: List[str] = input_schema.get("required", [])

    for prop_name, prop_def in input_schema.get("properties", {}).items():
        gemini_prop: Dict[str, Any] = {}
        prop_type = prop_def.get("type", "string")
        # Map JSON Schema types to Gemini Schema types
        type_map = {
            "string": "STRING",
            "number": "NUMBER",
            "integer": "INTEGER",
            "boolean": "BOOLEAN",
            "array": "ARRAY",
            "object": "OBJECT",
        }
        gemini_prop["type"] = type_map.get(prop_type, "STRING")
        if "description" in prop_def:
            gemini_prop["description"] = prop_def["description"]
        if prop_type == "array" and "items" in prop_def:
            item_type = prop_def["items"].get("type", "string")
            gemini_prop["items"] = {"type": type_map.get(item_type, "STRING")}
        properties[prop_name] = gemini_prop

    func_decl = types.FunctionDeclaration(
        name=tool["name"],
        description=tool.get("description", ""),
        parameters=types.Schema(
            type="OBJECT",
            properties={k: types.Schema(**v) for k, v in properties.items()},
            required=required if required else None,
        ),
    )
    return types.Tool(function_declarations=[func_decl])


def _messages_to_gemini(
    messages: List[AIMessage],
) -> List[types.Content]:
    """Convert a list of :class:`AIMessage` to Gemini ``Content`` objects.

    ``system`` role messages are filtered out here because they are handled
    separately via ``system_instruction``.

    Args:
        messages: Ordered conversation turns.

    Returns:
        List of :class:`types.Content` ready for the Gemini API.
    """
    contents: List[types.Content] = []
    for msg in messages:
        if msg.role == "system":
            continue  # injected as system_instruction

        if msg.role in ("user", "assistant"):
            parts: List[types.Part] = []

            if msg.content:
                parts.append(types.Part(text=msg.content))

            # Assistant messages may carry tool calls
            for tc in msg.tool_calls:
                parts.append(
                    types.Part(
                        function_call=types.FunctionCall(
                            name=tc.name,
                            args=tc.arguments,
                        )
                    )
                )

            role_map = {"user": "user", "assistant": "model"}
            if parts:
                contents.append(
                    types.Content(role=role_map[msg.role], parts=parts)
                )

        elif msg.role == "tool":
            # Tool results become "user" messages with function_response parts
            parts = []
            for tr in msg.tool_results:
                # Find matching tool call name from previous messages for Gemini
                parts.append(
                    types.Part(
                        function_response=types.FunctionResponse(
                            name=tr.tool_call_id,  # used as function name key
                            response={
                                "content": tr.content,
                                "is_error": tr.is_error,
                            },
                        )
                    )
                )
            if parts:
                contents.append(types.Content(role="user", parts=parts))

    return contents


def _extract_tool_calls(response: Any) -> List[AIToolCall]:
    """Extract tool calls from a Gemini response object.

    Args:
        response: The ``GenerateContentResponse`` object.

    Returns:
        List of :class:`AIToolCall` objects (may be empty).
    """
    tool_calls: List[AIToolCall] = []
    try:
        for part in response.candidates[0].content.parts:
            if part.function_call:
                fc = part.function_call
                tool_calls.append(
                    AIToolCall(
                        id=fc.name,  # Gemini uses function name as ID
                        name=fc.name,
                        arguments=dict(fc.args) if fc.args else {},
                    )
                )
    except (AttributeError, IndexError):
        pass
    return tool_calls


# ---------------------------------------------------------------------------
# Provider class
# ---------------------------------------------------------------------------


class GeminiProvider(AIProvider):
    """Gemini AI provider.

    Args:
        api_key: Google Gemini API key.
        model:   Model shortcut (``"3-flash"``, ``"3.1-pro"``, etc.) or a
                 full model ID.
    """

    def __init__(self, api_key: str, model: str = _DEFAULT_MODEL) -> None:
        self._api_key = api_key
        self._model_shortcut = model
        self._model_name = _MODEL_MAP.get(model, model)
        self._client = genai.Client(api_key=api_key)

    # ------------------------------------------------------------------
    # AIProvider interface
    # ------------------------------------------------------------------

    def get_context_window(self) -> int:
        return _CONTEXT_WINDOWS.get(self._model_name, 1_000_000)

    def get_max_output_tokens(self) -> int:
        return _MAX_OUTPUT_TOKENS.get(self._model_name, 65_536)

    def supports_tool_calling(self) -> bool:
        return True

    def chat(
        self,
        messages: List[AIMessage],
        tools: Optional[List[ToolSchema]] = None,
        system_prompt: Optional[str] = None,
    ) -> AIResponse:
        """Send messages to Gemini and return an :class:`AIResponse`.

        Args:
            messages:      Conversation history.
            tools:         Optional MCP tool schemas.
            system_prompt: Optional system instruction override.

        Returns:
            :class:`AIResponse` with either text content or tool calls.

        Raises:
            Exception: On Gemini API errors.
        """
        contents = _messages_to_gemini(messages)

        # Build config
        config_kwargs: Dict[str, Any] = {}
        if system_prompt:
            config_kwargs["system_instruction"] = system_prompt
        if tools:
            config_kwargs["tools"] = [
                _mcp_schema_to_gemini(t) for t in tools
            ]

        config = types.GenerateContentConfig(**config_kwargs) if config_kwargs else None

        try:
            if config is not None:
                response = self._client.models.generate_content(
                    model=self._model_name,
                    contents=contents,
                    config=config,
                )
            else:
                response = self._client.models.generate_content(
                    model=self._model_name,
                    contents=contents,
                )
        except errors.APIError as exc:
            raise Exception(f"Gemini API error: {exc}") from exc

        tool_calls = _extract_tool_calls(response)
        text = response.text or "" if not tool_calls else ""

        usage: Dict[str, int] = {}
        if hasattr(response, "usage_metadata") and response.usage_metadata:
            meta = response.usage_metadata
            usage = {
                "input_tokens": getattr(meta, "prompt_token_count", 0) or 0,
                "output_tokens": getattr(meta, "candidates_token_count", 0) or 0,
            }

        return AIResponse(content=text, tool_calls=tool_calls, usage=usage)

    async def chat_stream(
        self,
        messages: List[AIMessage],
        tools: Optional[List[ToolSchema]] = None,
        system_prompt: Optional[str] = None,
    ) -> AsyncIterator[AIChunk]:
        """Stream a Gemini response token-by-token.

        Args:
            messages:      Conversation history.
            tools:         Optional MCP tool schemas.
            system_prompt: Optional system instruction override.

        Yields:
            :class:`AIChunk` instances; the final chunk has ``is_final=True``.

        Raises:
            Exception: On Gemini API errors.
        """
        contents = _messages_to_gemini(messages)

        config_kwargs: Dict[str, Any] = {}
        if system_prompt:
            config_kwargs["system_instruction"] = system_prompt
        if tools:
            config_kwargs["tools"] = [
                _mcp_schema_to_gemini(t) for t in tools
            ]

        config = types.GenerateContentConfig(**config_kwargs) if config_kwargs else None

        try:
            if config is not None:
                stream = self._client.models.generate_content_stream(
                    model=self._model_name,
                    contents=contents,
                    config=config,
                )
            else:
                stream = self._client.models.generate_content_stream(
                    model=self._model_name,
                    contents=contents,
                )
        except errors.APIError as exc:
            raise Exception(f"Gemini API error: {exc}") from exc

        accumulated_tool_calls: List[AIToolCall] = []
        for chunk in stream:
            tool_calls_in_chunk = _extract_tool_calls(chunk)
            accumulated_tool_calls.extend(tool_calls_in_chunk)
            yield AIChunk(
                text=chunk.text or "",
                is_final=False,
            )

        # Emit final sentinel
        yield AIChunk(
            text="",
            is_final=True,
            tool_calls=accumulated_tool_calls,
        )

    # ------------------------------------------------------------------
    # Legacy compatibility helpers (used by existing main.py code)
    # ------------------------------------------------------------------

    def send_message(self, message: str, model: Optional[str] = None) -> str:
        """Simple single-turn helper for backward compatibility.

        Args:
            message: User message text.
            model:   Optional model shortcut to override the instance model.

        Returns:
            Response text.
        """
        if model is not None and model != self._model_shortcut:
            provider = GeminiProvider(self._api_key, model)
            return provider.send_message(message)

        response = self.chat([AIMessage(role="user", content=message)])
        return response.content

    def send_message_stream(self, message: str, model: Optional[str] = None):
        """Simple single-turn streaming helper for backward compatibility.

        Yields text chunks synchronously.

        Args:
            message: User message text.
            model:   Optional model shortcut to override the instance model.
        """
        import asyncio

        if model is not None and model != self._model_shortcut:
            provider = GeminiProvider(self._api_key, model)
            yield from provider.send_message_stream(message)
            return

        async def _collect():
            chunks = []
            async for chunk in await self.chat_stream(
                [AIMessage(role="user", content=message)]
            ):
                chunks.append(chunk)
            return chunks

        loop = asyncio.new_event_loop()
        try:
            chunks = loop.run_until_complete(_collect())
        finally:
            loop.close()

        for chunk in chunks:
            if chunk.text:
                yield chunk.text
