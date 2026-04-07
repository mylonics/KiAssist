"""Append-only JSONL conversation history store.

Each project stores its conversation history in
``{project_dir}/.kiassist/history.jsonl``.  Every message is written as a
single JSON line so that the file is always in a consistent state even after a
crash.

Schema of each line::

    {
        "session_id": "abc123",
        "timestamp":  "2024-01-15T10:30:00.000000",
        "role":       "user" | "assistant" | "tool",
        "content":    "...",
        "tool_calls": [...],   // serialised AIToolCall dicts, may be []
        "tool_results": [...], // serialised AIToolResult dicts, may be []
        "token_count": 42      // provider-reported tokens for this turn, or 0
    }

Example usage::

    store = ConversationStore("/path/to/project")
    session_id = store.new_session()

    store.append(session_id, AIMessage(role="user", content="Hello"))
    messages = store.load_session(session_id)

    sessions = store.list_sessions()
    store.purge_old(max_sessions=100)
"""

from __future__ import annotations

import json
import os
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Any

from ..ai.base import AIMessage, AIToolCall, AIToolResult


# ---------------------------------------------------------------------------
# Serialisation helpers
# ---------------------------------------------------------------------------


def _tool_call_to_dict(tc: AIToolCall) -> Dict[str, Any]:
    return {"id": tc.id, "name": tc.name, "arguments": tc.arguments}


def _tool_result_to_dict(tr: AIToolResult) -> Dict[str, Any]:
    return {
        "tool_call_id": tr.tool_call_id,
        "content": tr.content,
        "is_error": tr.is_error,
    }


def _tool_call_from_dict(d: Dict[str, Any]) -> AIToolCall:
    return AIToolCall(id=d["id"], name=d["name"], arguments=d.get("arguments", {}))


def _tool_result_from_dict(d: Dict[str, Any]) -> AIToolResult:
    return AIToolResult(
        tool_call_id=d["tool_call_id"],
        content=d.get("content", ""),
        is_error=d.get("is_error", False),
    )


def _message_to_entry(
    session_id: str,
    message: AIMessage,
    token_count: int = 0,
) -> Dict[str, Any]:
    return {
        "session_id": session_id,
        "timestamp": datetime.now(tz=timezone.utc).isoformat(),
        "role": message.role,
        "content": message.content,
        "tool_calls": [_tool_call_to_dict(tc) for tc in message.tool_calls],
        "tool_results": [_tool_result_to_dict(tr) for tr in message.tool_results],
        "token_count": token_count,
    }


def _entry_to_message(entry: Dict[str, Any]) -> AIMessage:
    return AIMessage(
        role=entry["role"],
        content=entry.get("content", ""),
        tool_calls=[_tool_call_from_dict(d) for d in entry.get("tool_calls", [])],
        tool_results=[
            _tool_result_from_dict(d) for d in entry.get("tool_results", [])
        ],
    )


# ---------------------------------------------------------------------------
# ConversationStore
# ---------------------------------------------------------------------------


class ConversationStore:
    """Append-only JSONL conversation history for a KiAssist project.

    Args:
        project_path: Path to the ``.kicad_pro`` file or project directory.
                      The store is created at
                      ``{project_dir}/.kiassist/history.jsonl``.

    Entries are appended to the history file in JSON Lines format using a
    standard ``open(..., "a")`` call.  The class provides no additional
    cross-process or multi-thread safety guarantees beyond normal file-system
    semantics.
    """

    def __init__(self, project_path: str | Path) -> None:
        p = Path(project_path)
        self._project_dir: Path = p.parent if p.is_file() else p
        self._store_dir: Path = self._project_dir / ".kiassist"
        self._history_path: Path = self._store_dir / "history.jsonl"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def history_path(self) -> Path:
        """Absolute path to the JSONL history file."""
        return self._history_path

    def new_session(self) -> str:
        """Generate and return a new unique session ID (UUID4 hex)."""
        return uuid.uuid4().hex

    def append(
        self,
        session_id: str,
        message: AIMessage,
        token_count: int = 0,
    ) -> None:
        """Append *message* to the history file.

        Args:
            session_id:  Session this message belongs to.
            message:     :class:`~kiassist_utils.ai.base.AIMessage` to record.
            token_count: Provider-reported token count for this turn (0 if
                         unknown).
        """
        self._ensure_store_dir()
        entry = _message_to_entry(session_id, message, token_count)
        line = json.dumps(entry, ensure_ascii=False) + "\n"
        with open(self._history_path, "a", encoding="utf-8") as fh:
            fh.write(line)

    def load_session(self, session_id: str) -> List[AIMessage]:
        """Return all messages for *session_id* in chronological order.

        Args:
            session_id: Session to load.

        Returns:
            Ordered list of :class:`~kiassist_utils.ai.base.AIMessage`.
            Returns an empty list if the session is not found or the file does
            not exist.
        """
        messages: List[AIMessage] = []
        for entry in self._iter_entries():
            if entry.get("session_id") == session_id:
                messages.append(_entry_to_message(entry))
        return messages

    def list_sessions(self) -> List[Dict[str, Any]]:
        """Return metadata for every session in the history file.

        Returns:
            List of dicts (one per unique session_id) with keys:

            * ``session_id`` — unique identifier
            * ``started_at`` — ISO timestamp of the first message
            * ``last_at``    — ISO timestamp of the last message
            * ``message_count`` — total number of messages
        """
        sessions: Dict[str, Dict[str, Any]] = {}
        for entry in self._iter_entries():
            sid = entry.get("session_id", "")
            ts = entry.get("timestamp", "")
            if sid not in sessions:
                sessions[sid] = {
                    "session_id": sid,
                    "started_at": ts,
                    "last_at": ts,
                    "message_count": 0,
                }
            sessions[sid]["last_at"] = ts
            sessions[sid]["message_count"] += 1
        return list(sessions.values())

    def purge_old(self, max_sessions: int = 100) -> int:
        """Remove the oldest sessions so that at most *max_sessions* remain.

        Args:
            max_sessions: Maximum number of sessions to retain.

        Returns:
            Number of sessions removed.
        """
        all_entries = list(self._iter_entries())
        if not all_entries:
            return 0

        # Collect session IDs in first-seen order
        seen_order: List[str] = []
        seen_set: set = set()
        for entry in all_entries:
            sid = entry.get("session_id", "")
            if sid and sid not in seen_set:
                seen_order.append(sid)
                seen_set.add(sid)

        if len(seen_order) <= max_sessions:
            return 0

        sessions_to_remove = set(seen_order[: len(seen_order) - max_sessions])
        kept = [e for e in all_entries if e.get("session_id") not in sessions_to_remove]

        self._rewrite(kept)
        return len(sessions_to_remove)

    def delete_session(self, session_id: str) -> int:
        """Remove all entries for *session_id* from the history.

        Returns:
            Number of entries deleted.
        """
        all_entries = list(self._iter_entries())
        kept = [e for e in all_entries if e.get("session_id") != session_id]
        removed = len(all_entries) - len(kept)
        if removed:
            self._rewrite(kept)
        return removed

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _ensure_store_dir(self) -> None:
        self._store_dir.mkdir(parents=True, exist_ok=True)

    def _iter_entries(self):
        """Yield parsed JSON dicts from the history file, skipping bad lines."""
        if not self._history_path.exists():
            return
        with open(self._history_path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue  # skip corrupted lines

    def _rewrite(self, entries: List[Dict[str, Any]]) -> None:
        """Overwrite the history file with *entries*.

        Uses a unique temporary file in the same directory for an atomic
        replace.  This avoids a fixed ``.jsonl.tmp`` path that two concurrent
        processes could collide on.
        """
        self._ensure_store_dir()
        tmp_fd, tmp_path_str = tempfile.mkstemp(
            dir=self._history_path.parent, suffix=".tmp"
        )
        tmp_path = Path(tmp_path_str)
        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as fh:
                for entry in entries:
                    fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
            os.replace(tmp_path, self._history_path)
        except (OSError, IOError, ValueError):
            tmp_path.unlink(missing_ok=True)
            raise
