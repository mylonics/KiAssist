"""Abstract AI provider interface and shared data types.

All provider implementations (Gemini, Claude, OpenAI) implement the
:class:`AIProvider` abstract base class so they can be used interchangeably
by the :class:`~kiassist_utils.ai.tool_executor.ToolExecutor` and the rest of
the application.

Data flow::

    user input
        ↓
    list[AIMessage]  ──────────────────────────────┐
        ↓                                          │
    AIProvider.chat(messages, tools, system_prompt)│
        ↓                                          │
    AIResponse                                     │
        ↓ (contains tool_calls?)                   │
    ToolExecutor dispatches → MCP tools            │
        ↓ (AIToolResult)                           │
    append to messages ────────────────────────────┘
        ↓ (until no more tool_calls)
    final text response
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Dict, List, Optional


# ---------------------------------------------------------------------------
# Shared data types
# ---------------------------------------------------------------------------


@dataclass
class AIToolCall:
    """A tool call requested by the AI model.

    Attributes:
        id:        Provider-assigned unique identifier for this call.
        name:      Name of the MCP tool to invoke.
        arguments: Parsed JSON arguments dict.
    """

    id: str
    name: str
    arguments: Dict[str, Any]


@dataclass
class AIToolResult:
    """The result of executing a tool call.

    Attributes:
        tool_call_id: Matches :attr:`AIToolCall.id`.
        content:      String representation of the result.
        is_error:     True if the tool raised an exception.
    """

    tool_call_id: str
    content: str
    is_error: bool = False


@dataclass
class AIMessage:
    """A single message in a conversation.

    Attributes:
        role:         One of ``"system"``, ``"user"``, ``"assistant"``,
                      ``"tool"``.
        content:      Plain-text content (may be empty when *tool_calls* is
                      set).
        tool_calls:   List of :class:`AIToolCall` objects present in an
                      assistant message.
        tool_results: List of :class:`AIToolResult` objects present in a
                      tool-result message.
    """

    role: str  # "system" | "user" | "assistant" | "tool"
    content: str = ""
    tool_calls: List[AIToolCall] = field(default_factory=list)
    tool_results: List[AIToolResult] = field(default_factory=list)


@dataclass
class AIResponse:
    """A complete response from the AI model.

    Attributes:
        content:    Final text produced by the model (empty when tool calls
                    are present).
        tool_calls: Tool calls the model wants to make.
        usage:      Provider-reported token usage (optional, for logging /
                    context window management).
    """

    content: str = ""
    tool_calls: List[AIToolCall] = field(default_factory=list)
    usage: Dict[str, int] = field(default_factory=dict)


@dataclass
class AIChunk:
    """A single streamed chunk from the model.

    Attributes:
        text:       Incremental text fragment (may be empty).
        is_final:   True on the last chunk of a stream.
        tool_calls: Tool calls accumulated so far (populated only on the
                    final chunk, if any).
        usage:      Token usage (populated only on the final chunk, if any).
    """

    text: str = ""
    is_final: bool = False
    tool_calls: List[AIToolCall] = field(default_factory=list)
    usage: Dict[str, int] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Tool schema type alias
# ---------------------------------------------------------------------------

# A tool schema follows the JSON Schema / MCP shape:
# {"name": str, "description": str, "inputSchema": {"type": "object", "properties": {...}}}
ToolSchema = Dict[str, Any]


# ---------------------------------------------------------------------------
# Abstract provider
# ---------------------------------------------------------------------------


class AIProvider(ABC):
    """Abstract base class for all AI provider implementations."""

    # ------------------------------------------------------------------
    # Core methods (must be implemented)
    # ------------------------------------------------------------------

    @abstractmethod
    def chat(
        self,
        messages: List[AIMessage],
        tools: Optional[List[ToolSchema]] = None,
        system_prompt: Optional[str] = None,
    ) -> AIResponse:
        """Send a (possibly multi-turn) conversation and return a response.

        Args:
            messages:      Ordered list of conversation turns.
            tools:         MCP-style tool schemas the model may call.
            system_prompt: Override the default system prompt.

        Returns:
            :class:`AIResponse` containing either final text or tool calls.
        """

    @abstractmethod
    async def chat_stream(
        self,
        messages: List[AIMessage],
        tools: Optional[List[ToolSchema]] = None,
        system_prompt: Optional[str] = None,
    ) -> AsyncIterator[AIChunk]:
        """Stream a response token-by-token.

        Yields :class:`AIChunk` instances; the final chunk has
        ``is_final=True`` and carries any accumulated tool calls / usage.
        """
        # Make the return type explicit for type checkers even though the
        # body is abstract.  Subclasses should use "yield" to turn the method
        # into an async generator.
        raise NotImplementedError  # pragma: no cover
        yield AIChunk()  # noqa: unreachable — satisfies the iterator contract

    @abstractmethod
    def get_context_window(self) -> int:
        """Return the model's maximum context size in tokens."""

    @abstractmethod
    def get_max_output_tokens(self) -> int:
        """Return the model's maximum *output* token count."""

    @abstractmethod
    def supports_tool_calling(self) -> bool:
        """Return True if the current model supports function/tool calling."""

    # ------------------------------------------------------------------
    # Optional convenience helpers
    # ------------------------------------------------------------------

    @property
    def provider_name(self) -> str:
        """Human-readable provider name, e.g. ``"Gemini"``."""
        return type(self).__name__

    @property
    def model_name(self) -> str:
        """The model identifier string as passed to the provider API."""
        return getattr(self, "_model_name", "unknown")
