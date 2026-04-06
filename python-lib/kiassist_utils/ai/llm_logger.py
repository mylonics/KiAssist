"""In-memory logger for backend ↔ LLM interactions.

Captures every AI provider call (messages sent, system prompt, tools,
response, token usage, timing) so the frontend can display them for
debugging purposes.

Usage::

    from kiassist_utils.ai.llm_logger import llm_logger

    # Record the start of an LLM call
    entry_id = llm_logger.start(
        provider="gemini",
        model="3-flash",
        messages=[...],
        system_prompt="...",
        tool_count=5,
    )

    # ... perform the LLM call ...

    # Record the completion
    llm_logger.finish(entry_id, response_text="Hello!", usage={...})

    # Retrieve all entries (for the API)
    entries = llm_logger.get_entries()
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class LLMLogEntry:
    """A single logged LLM interaction.

    Attributes:
        id:              Unique identifier for this entry.
        timestamp:       Unix timestamp when the call started.
        provider:        Provider name (e.g. ``"gemini"``, ``"claude"``).
        model:           Model identifier string.
        system_prompt:   System prompt sent (may be truncated for display).
        messages:        Serialised conversation messages sent to the LLM.
        tool_count:      Number of tool schemas provided.
        tool_names:      Names of available tools.
        response_text:   Final text response from the LLM.
        response_tool_calls: Tool calls returned by the LLM (serialised).
        usage:           Token usage dict (input_tokens, output_tokens).
        duration_ms:     Wall-clock duration in milliseconds.
        is_stream:       Whether this was a streaming call.
        error:           Error message if the call failed.
        done:            Whether the call has completed.
    """

    id: str = ""
    timestamp: float = 0.0
    provider: str = ""
    model: str = ""
    system_prompt: str = ""
    messages: List[Dict[str, Any]] = field(default_factory=list)
    tool_count: int = 0
    tool_names: List[str] = field(default_factory=list)
    response_text: str = ""
    response_tool_calls: List[Dict[str, Any]] = field(default_factory=list)
    usage: Dict[str, int] = field(default_factory=dict)
    duration_ms: float = 0.0
    is_stream: bool = False
    error: str = ""
    done: bool = False

    def to_dict(self) -> Dict[str, Any]:
        """Serialise to a plain dict for JSON transport."""
        return {
            "id": self.id,
            "timestamp": self.timestamp,
            "provider": self.provider,
            "model": self.model,
            "system_prompt": self.system_prompt,
            "messages": self.messages,
            "tool_count": self.tool_count,
            "tool_names": self.tool_names,
            "response_text": self.response_text,
            "response_tool_calls": self.response_tool_calls,
            "usage": self.usage,
            "duration_ms": round(self.duration_ms, 1),
            "is_stream": self.is_stream,
            "error": self.error,
            "done": self.done,
        }


def _serialize_messages(messages: list) -> List[Dict[str, Any]]:
    """Convert a list of AIMessage objects to plain dicts.

    Handles both dataclass instances and already-serialised dicts gracefully.
    """
    result: List[Dict[str, Any]] = []
    for msg in messages:
        if isinstance(msg, dict):
            result.append(msg)
            continue
        entry: Dict[str, Any] = {
            "role": getattr(msg, "role", "unknown"),
            "content": getattr(msg, "content", ""),
        }
        # Include tool calls if present
        tool_calls = getattr(msg, "tool_calls", None)
        if tool_calls:
            entry["tool_calls"] = [
                {
                    "id": getattr(tc, "id", ""),
                    "name": getattr(tc, "name", ""),
                    "arguments": getattr(tc, "arguments", {}),
                }
                for tc in tool_calls
            ]
        # Include tool results if present
        tool_results = getattr(msg, "tool_results", None)
        if tool_results:
            entry["tool_results"] = [
                {
                    "tool_call_id": getattr(tr, "tool_call_id", ""),
                    "content": getattr(tr, "content", "")[:2000],  # truncate large results
                    "is_error": getattr(tr, "is_error", False),
                }
                for tr in tool_results
            ]
        result.append(entry)
    return result


class LLMLogger:
    """Thread-safe in-memory log of LLM interactions.

    Stores up to ``max_entries`` most recent interactions.
    """

    def __init__(self, max_entries: int = 200) -> None:
        self._lock = threading.Lock()
        self._entries: List[LLMLogEntry] = []
        self._max_entries = max_entries
        self._by_id: Dict[str, LLMLogEntry] = {}

    # ------------------------------------------------------------------
    # Recording API
    # ------------------------------------------------------------------

    def start(
        self,
        *,
        provider: str = "",
        model: str = "",
        messages: Optional[list] = None,
        system_prompt: Optional[str] = None,
        tool_count: int = 0,
        tool_names: Optional[List[str]] = None,
        is_stream: bool = False,
    ) -> str:
        """Record the start of an LLM call.  Returns the entry ID."""
        entry = LLMLogEntry(
            id=uuid.uuid4().hex[:12],
            timestamp=time.time(),
            provider=provider,
            model=model,
            system_prompt=system_prompt or "",
            messages=_serialize_messages(messages or []),
            tool_count=tool_count,
            tool_names=tool_names or [],
            is_stream=is_stream,
        )
        with self._lock:
            self._entries.append(entry)
            self._by_id[entry.id] = entry
            # Trim old entries
            if len(self._entries) > self._max_entries:
                removed = self._entries[: len(self._entries) - self._max_entries]
                self._entries = self._entries[-self._max_entries :]
                for r in removed:
                    self._by_id.pop(r.id, None)
        return entry.id

    def finish(
        self,
        entry_id: str,
        *,
        response_text: str = "",
        response_tool_calls: Optional[list] = None,
        usage: Optional[Dict[str, int]] = None,
        error: str = "",
    ) -> None:
        """Record the completion of an LLM call."""
        with self._lock:
            entry = self._by_id.get(entry_id)
            if entry is None:
                return
            entry.response_text = response_text
            if response_tool_calls:
                entry.response_tool_calls = [
                    {
                        "id": getattr(tc, "id", ""),
                        "name": getattr(tc, "name", ""),
                        "arguments": getattr(tc, "arguments", {}),
                    }
                    for tc in response_tool_calls
                ]
            entry.usage = usage or {}
            entry.error = error
            entry.duration_ms = (time.time() - entry.timestamp) * 1000
            entry.done = True

    def update_stream_response(self, entry_id: str, text: str) -> None:
        """Update the response text of a streaming entry (for live preview)."""
        with self._lock:
            entry = self._by_id.get(entry_id)
            if entry is not None:
                entry.response_text = text

    # ------------------------------------------------------------------
    # Query API
    # ------------------------------------------------------------------

    def get_entries(self, since_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Return all entries (or entries after *since_id*) as plain dicts.

        Args:
            since_id: If provided, only entries recorded after this ID are
                      returned.  Useful for incremental polling.

        Returns:
            List of serialised log entries, oldest first.
        """
        with self._lock:
            if since_id is None:
                return [e.to_dict() for e in self._entries]
            # Find the index of since_id and return everything after it
            for i, e in enumerate(self._entries):
                if e.id == since_id:
                    return [x.to_dict() for x in self._entries[i + 1 :]]
            # ID not found → return all
            return [e.to_dict() for e in self._entries]

    def get_entry(self, entry_id: str) -> Optional[Dict[str, Any]]:
        """Return a single entry by ID, or ``None``."""
        with self._lock:
            entry = self._by_id.get(entry_id)
            return entry.to_dict() if entry else None

    def clear(self) -> None:
        """Remove all logged entries."""
        with self._lock:
            self._entries.clear()
            self._by_id.clear()


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

llm_logger = LLMLogger()
