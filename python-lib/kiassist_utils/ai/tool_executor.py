"""Agentic tool execution engine.

:class:`ToolExecutor` orchestrates the agentic loop:

1. Call the AI provider with the current conversation + available tools.
2. If the response contains tool calls, dispatch each one to the MCP server
   via :func:`~kiassist_utils.mcp_server.in_process_call` (no network hop).
3. Append the tool results to the conversation and repeat from step 1.
4. Stop when the AI returns a final text response (no tool calls) or when
   ``max_iterations`` is reached.

Tool calls within a single iteration that are *independent* (no data
dependency) are executed in parallel using :mod:`asyncio`.

Optionally accepts a :class:`~kiassist_utils.context.tokens.ContextWindowManager`
to automatically trim tool results and summarise the conversation when approaching
the model's context limit, and a
:class:`~kiassist_utils.context.history.ConversationStore` to persist every turn
to disk for later session resume.

Example::

    import asyncio
    from kiassist_utils.ai import GeminiProvider, ToolExecutor, AIMessage

    provider = GeminiProvider(api_key="...", model="3-flash")
    executor = ToolExecutor(provider, max_iterations=10)

    messages = [AIMessage(role="user", content="Open my schematic.")]
    result = asyncio.run(executor.run(messages, system_prompt="You are KiAssist."))
    print(result.content)
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional

from .base import (
    AIMessage,
    AIProvider,
    AIResponse,
    AIToolCall,
    AIToolResult,
    ToolSchema,
)

if TYPE_CHECKING:  # pragma: no cover
    from ..context.history import ConversationStore
    from ..context.tokens import ContextWindowManager

# Import the shared token-counting helper at module level.
# context.tokens only depends on ai.base so there is no circular import.
from ..context.tokens import usage_to_tokens as _usage_to_tokens  # noqa: E402

logger = logging.getLogger(__name__)

# Default maximum number of agentic loop iterations before giving up.
DEFAULT_MAX_ITERATIONS = 20

# Module-level reference to in_process_call, resolved lazily on first use.
# Exposed at module level so tests can monkeypatch it:
#   patch("kiassist_utils.ai.tool_executor.in_process_call", ...)
in_process_call: Optional[Any] = None


def _get_in_process_call() -> Any:
    """Return the MCP in_process_call function, importing it on first use.

    Using a resolver function keeps the import lazy (avoids circular-import
    issues at module load time) while still exposing the name at module level
    so test code can monkeypatch ``kiassist_utils.ai.tool_executor.in_process_call``.
    """
    global in_process_call
    if in_process_call is None:
        from ..mcp_server import in_process_call as _ipc  # noqa: PLC0415
        in_process_call = _ipc
    return in_process_call


class ToolExecutor:
    """Runs the AI ↔ MCP tool agentic loop.

    Args:
        provider:         An :class:`~kiassist_utils.ai.base.AIProvider`
                          instance (Gemini, Claude, or OpenAI).
        max_iterations:   Maximum number of AI↔tool round-trips.  Prevents
                          runaway loops.  Defaults to
                          :data:`DEFAULT_MAX_ITERATIONS`.
        tool_schemas:     Pre-fetched list of MCP tool schemas.  If ``None``,
                          the executor will fetch them at the start of each
                          :meth:`run` call via the MCP server.
        on_tool_call:     Optional callback invoked *before* each tool is
                          executed: ``on_tool_call(tool_call: AIToolCall)``.
        on_tool_result:   Optional callback invoked *after* each tool returns:
                          ``on_tool_result(tool_call: AIToolCall, result: AIToolResult)``.
        context_manager:  Optional :class:`~kiassist_utils.context.tokens.ContextWindowManager`.
                          When provided, token usage is tracked after every AI
                          response, tool results are trimmed to the configured
                          character budget, and the conversation is automatically
                          summarised when approaching the context-window limit.
        history_store:    Optional :class:`~kiassist_utils.context.history.ConversationStore`.
                          When provided, every message added to the conversation
                          is persisted to the JSONL history file.  Requires
                          *session_id* to be passed to :meth:`run`.
    """

    def __init__(
        self,
        provider: AIProvider,
        max_iterations: int = DEFAULT_MAX_ITERATIONS,
        tool_schemas: Optional[List[ToolSchema]] = None,
        on_tool_call: Optional[Callable[[AIToolCall], None]] = None,
        on_tool_result: Optional[
            Callable[[AIToolCall, AIToolResult], None]
        ] = None,
        context_manager: Optional["ContextWindowManager"] = None,
        history_store: Optional["ConversationStore"] = None,
    ) -> None:
        self.provider = provider
        self.max_iterations = max_iterations
        self._tool_schemas = tool_schemas
        self.on_tool_call = on_tool_call
        self.on_tool_result = on_tool_result
        self.context_manager = context_manager
        self.history_store = history_store

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def run(
        self,
        messages: List[AIMessage],
        system_prompt: Optional[str] = None,
        tool_schemas: Optional[List[ToolSchema]] = None,
        session_id: Optional[str] = None,
    ) -> AIResponse:
        """Run the agentic loop until a final text response is produced.

        Args:
            messages:      Seed conversation (typically a single user message).
            system_prompt: Optional system instruction forwarded to the provider.
            tool_schemas:  Override the executor's tool schemas for this run.
            session_id:    Session identifier used when persisting messages via
                           *history_store*.  Required when *history_store* is
                           set; a ``ValueError`` is raised otherwise.

        Returns:
            The final :class:`~kiassist_utils.ai.base.AIResponse` from the
            AI once it has no more tool calls to make.

        Raises:
            ValueError:   If *history_store* is provided but *session_id* is
                          ``None``.
            RuntimeError: If ``max_iterations`` is exceeded without a final
                          text response.
        """
        if self.history_store is not None and session_id is None:
            raise ValueError(
                "session_id is required when history_store is provided. "
                "Generate one with history_store.new_session()."
            )

        # Resolve tool schemas: argument > instance-level > fetch from MCP
        schemas = tool_schemas or self._tool_schemas
        if schemas is None:
            schemas = await self._fetch_tool_schemas()

        conversation: List[AIMessage] = list(messages)

        # Persist the seed messages (e.g. the initial user turn)
        if self.history_store is not None and session_id is not None:
            for msg in conversation:
                self.history_store.append(session_id, msg)

        for iteration in range(self.max_iterations):
            logger.debug("ToolExecutor iteration %d/%d", iteration + 1, self.max_iterations)

            # Apply context-window management before calling the AI:
            # summarise old messages if we are approaching the token limit.
            if self.context_manager is not None:
                conversation = self.context_manager.maybe_summarize(
                    conversation, self.provider, system_prompt
                )

            # Run the synchronous provider.chat() in a thread pool so it
            # does not block the event loop during network I/O.
            response = await asyncio.to_thread(
                self.provider.chat,
                conversation,
                tools=schemas if self.provider.supports_tool_calling() else None,
                system_prompt=system_prompt,
            )

            # Track token usage reported by the provider.
            if self.context_manager is not None and response.usage:
                self.context_manager.track_usage(response.usage)

            # Derive per-turn token count for history persistence.
            turn_tokens = _usage_to_tokens(response.usage) if response.usage else 0

            # If no tool calls → we have the final answer
            if not response.tool_calls:
                logger.debug("No tool calls; returning final response.")
                # Persist the final assistant message before returning.
                if self.history_store is not None and session_id is not None:
                    final_msg = AIMessage(
                        role="assistant",
                        content=response.content,
                    )
                    self.history_store.append(session_id, final_msg, token_count=turn_tokens)
                return response

            # Append assistant message (with tool calls) to conversation
            assistant_msg = AIMessage(
                role="assistant",
                content=response.content,
                tool_calls=response.tool_calls,
            )
            conversation.append(assistant_msg)
            if self.history_store is not None and session_id is not None:
                self.history_store.append(session_id, assistant_msg, token_count=turn_tokens)

            # Execute tool calls (parallel where possible)
            tool_results = await self._execute_tool_calls(response.tool_calls)

            # Append tool results as a single "tool" message
            tool_msg = AIMessage(role="tool", tool_results=tool_results)
            conversation.append(tool_msg)
            if self.history_store is not None and session_id is not None:
                self.history_store.append(session_id, tool_msg)

        raise RuntimeError(
            f"ToolExecutor exceeded max_iterations={self.max_iterations} "
            "without receiving a final text response from the AI."
        )

    # ------------------------------------------------------------------
    # Tool schema retrieval
    # ------------------------------------------------------------------

    async def _fetch_tool_schemas(self) -> List[ToolSchema]:
        """Fetch available tool schemas from the MCP server.

        Returns:
            List of MCP tool schema dicts.
        """
        try:
            from ..mcp_server import mcp as _mcp  # lazy import to avoid circular deps

            tools = await _mcp.list_tools()
            schemas: List[ToolSchema] = []
            for tool in tools:
                schema: ToolSchema = {
                    "name": tool.name,
                    "description": tool.description or "",
                    "inputSchema": (
                        tool.inputSchema
                        if isinstance(tool.inputSchema, dict)
                        else {}
                    ),
                }
                schemas.append(schema)
            return schemas
        except Exception as exc:
            logger.warning("Failed to fetch MCP tool schemas: %s", exc)
            return []

    # ------------------------------------------------------------------
    # Tool execution
    # ------------------------------------------------------------------

    async def _execute_tool_calls(
        self, tool_calls: List[AIToolCall]
    ) -> List[AIToolResult]:
        """Execute a list of tool calls in parallel.

        Args:
            tool_calls: Tool calls from the AI response.

        Returns:
            List of :class:`~kiassist_utils.ai.base.AIToolResult` in the
            same order as the input.
        """
        tasks = [self._execute_one(tc) for tc in tool_calls]
        return list(await asyncio.gather(*tasks))

    async def _execute_one(self, tool_call: AIToolCall) -> AIToolResult:
        """Execute a single tool call.

        Args:
            tool_call: The tool call to dispatch.

        Returns:
            :class:`~kiassist_utils.ai.base.AIToolResult`.
        """
        if self.on_tool_call:
            try:
                self.on_tool_call(tool_call)
            except Exception:
                pass

        is_error = False
        content: str

        try:
            ipc = _get_in_process_call()
            result = await ipc(tool_call.name, tool_call.arguments)
            content = json.dumps(result) if not isinstance(result, str) else result
        except Exception as exc:
            is_error = True
            content = f"Tool execution error: {exc}"
            logger.warning(
                "Tool %r failed: %s", tool_call.name, exc, exc_info=True
            )

        # Trim over-long tool results to stay within the context budget.
        if self.context_manager is not None:
            content = self.context_manager.trim_tool_result(content)

        tool_result = AIToolResult(
            tool_call_id=tool_call.id,
            content=content,
            is_error=is_error,
        )

        if self.on_tool_result:
            try:
                self.on_tool_result(tool_call, tool_result)
            except Exception:
                pass

        return tool_result
