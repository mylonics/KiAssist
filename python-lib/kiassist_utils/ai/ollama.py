"""Local model AI provider using an OpenAI-compatible API endpoint.

This provider targets locally-hosted models served by tools such as
`Ollama <https://ollama.com>`_ or `LM Studio <https://lmstudio.ai>`_, both of
which expose an OpenAI-compatible HTTP API.

The implementation is a thin wrapper around :class:`OpenAIProvider` that
pre-configures the ``base_url`` for the local server and makes the API key
optional (most local servers do not require authentication).

Default server endpoints:

* **Ollama** – ``http://localhost:11434/v1``
* **LM Studio** – ``http://localhost:1234/v1``

Example::

    from kiassist_utils.ai import OllamaProvider, AIMessage

    # Connect to default Ollama server
    provider = OllamaProvider(model="llama3.2")
    response = provider.chat([AIMessage(role="user", content="Hello")])
    print(response.content)

    # Connect to LM Studio
    provider = OllamaProvider(
        model="mistral",
        base_url="http://localhost:1234/v1",
    )
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
from .openai import OpenAIProvider

# ---------------------------------------------------------------------------
# Default configuration
# ---------------------------------------------------------------------------

_DEFAULT_BASE_URL = "http://localhost:11434/v1"

# Placeholder key used when the local server requires no authentication.
# The OpenAI SDK mandates a non-empty api_key so we supply a sentinel value.
_PLACEHOLDER_API_KEY = "local"

# Context-window and output-token defaults for local models.
# These are conservative estimates; individual models may differ significantly.
_DEFAULT_CONTEXT_WINDOW = 32_768
_DEFAULT_MAX_OUTPUT_TOKENS = 8_192

# ---------------------------------------------------------------------------
# Provider class
# ---------------------------------------------------------------------------


class OllamaProvider(AIProvider):
    """AI provider for locally-hosted OpenAI-compatible servers.

    Delegates all API calls to :class:`~kiassist_utils.ai.openai.OpenAIProvider`
    with a custom ``base_url`` pointing at the local server.

    Args:
        model:    Model name as known to the local server (e.g. ``"llama3.2"``
                  for Ollama or the name shown in LM Studio).
        base_url: Base URL of the local OpenAI-compatible server.  Defaults to
                  :data:`_DEFAULT_BASE_URL` (Ollama: ``http://localhost:11434/v1``).
        api_key:  Authentication key.  Most local servers accept any non-empty
                  string.  Defaults to :data:`_PLACEHOLDER_API_KEY` (``"local"``).

    Raises:
        ImportError: If the ``openai`` package is not installed.
    """

    def __init__(
        self,
        model: str = "llama3.2",
        base_url: str = _DEFAULT_BASE_URL,
        api_key: str = _PLACEHOLDER_API_KEY,
    ) -> None:
        self._model_shortcut = model
        self._model_name = model  # passed verbatim to the local server
        self._base_url = base_url
        self._delegate = OpenAIProvider(
            api_key=api_key,
            model=model,
            base_url=base_url,
        )
        # Keep the model name in sync with the delegate so callers can read it
        # via the base-class `model_name` property.
        self._delegate._model_name = model

    # ------------------------------------------------------------------
    # AIProvider interface – delegate to OpenAIProvider
    # ------------------------------------------------------------------

    def get_context_window(self) -> int:
        return _DEFAULT_CONTEXT_WINDOW

    def get_max_output_tokens(self) -> int:
        return _DEFAULT_MAX_OUTPUT_TOKENS

    def supports_tool_calling(self) -> bool:
        # Most local models support the OpenAI tool-calling protocol; the
        # caller can override this by subclassing if a specific model does not.
        return True

    def chat(
        self,
        messages: List[AIMessage],
        tools: Optional[List[ToolSchema]] = None,
        system_prompt: Optional[str] = None,
    ) -> AIResponse:
        """Send messages to the local server and return an :class:`AIResponse`.

        Args:
            messages:      Conversation history.
            tools:         Optional MCP tool schemas.
            system_prompt: Optional system prompt.

        Returns:
            :class:`AIResponse` with text content and/or tool calls.

        Raises:
            Exception: On API errors from the local server.
        """
        return self._delegate.chat(messages, tools, system_prompt)

    async def chat_stream(
        self,
        messages: List[AIMessage],
        tools: Optional[List[ToolSchema]] = None,
        system_prompt: Optional[str] = None,
    ) -> AsyncIterator[AIChunk]:
        """Stream a response from the local server.

        Args:
            messages:      Conversation history.
            tools:         Optional MCP tool schemas.
            system_prompt: Optional system prompt.

        Yields:
            :class:`AIChunk`; final chunk has ``is_final=True``.

        Raises:
            Exception: On streaming errors from the local server.
        """
        async for chunk in self._delegate.chat_stream(messages, tools, system_prompt):
            yield chunk

    # ------------------------------------------------------------------
    # Convenience properties
    # ------------------------------------------------------------------

    @property
    def provider_name(self) -> str:
        return "OllamaProvider"

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def base_url(self) -> str:
        """The base URL of the local server."""
        return self._base_url
