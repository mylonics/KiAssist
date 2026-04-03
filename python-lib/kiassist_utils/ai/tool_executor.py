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
from typing import Any, Callable, Dict, List, Optional

from .base import (
    AIMessage,
    AIProvider,
    AIResponse,
    AIToolCall,
    AIToolResult,
    ToolSchema,
)

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
        provider:       An :class:`~kiassist_utils.ai.base.AIProvider`
                        instance (Gemini, Claude, or OpenAI).
        max_iterations: Maximum number of AI↔tool round-trips.  Prevents
                        runaway loops.  Defaults to
                        :data:`DEFAULT_MAX_ITERATIONS`.
        tool_schemas:   Pre-fetched list of MCP tool schemas.  If ``None``,
                        the executor will fetch them at the start of each
                        :meth:`run` call via the MCP server.
        on_tool_call:   Optional callback invoked *before* each tool is
                        executed: ``on_tool_call(tool_call: AIToolCall)``.
        on_tool_result: Optional callback invoked *after* each tool returns:
                        ``on_tool_result(tool_call: AIToolCall, result: AIToolResult)``.
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
    ) -> None:
        self.provider = provider
        self.max_iterations = max_iterations
        self._tool_schemas = tool_schemas
        self.on_tool_call = on_tool_call
        self.on_tool_result = on_tool_result

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def run(
        self,
        messages: List[AIMessage],
        system_prompt: Optional[str] = None,
        tool_schemas: Optional[List[ToolSchema]] = None,
    ) -> AIResponse:
        """Run the agentic loop until a final text response is produced.

        Args:
            messages:      Seed conversation (typically a single user message).
            system_prompt: Optional system instruction forwarded to the provider.
            tool_schemas:  Override the executor's tool schemas for this run.

        Returns:
            The final :class:`~kiassist_utils.ai.base.AIResponse` from the
            AI once it has no more tool calls to make.

        Raises:
            RuntimeError: If ``max_iterations`` is exceeded without a final
                          text response.
        """
        # Resolve tool schemas: argument > instance-level > fetch from MCP
        schemas = tool_schemas or self._tool_schemas
        if schemas is None:
            schemas = await self._fetch_tool_schemas()

        conversation: List[AIMessage] = list(messages)

        for iteration in range(self.max_iterations):
            logger.debug("ToolExecutor iteration %d/%d", iteration + 1, self.max_iterations)

            response = self.provider.chat(
                conversation,
                tools=schemas if self.provider.supports_tool_calling() else None,
                system_prompt=system_prompt,
            )

            # If no tool calls → we have the final answer
            if not response.tool_calls:
                logger.debug("No tool calls; returning final response.")
                return response

            # Append assistant message (with tool calls) to conversation
            conversation.append(
                AIMessage(
                    role="assistant",
                    content=response.content,
                    tool_calls=response.tool_calls,
                )
            )

            # Execute tool calls (parallel where possible)
            tool_results = await self._execute_tool_calls(response.tool_calls)

            # Append tool results as a single "tool" message
            conversation.append(
                AIMessage(role="tool", tool_results=tool_results)
            )

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
