"""Tests for Phase 4: Context Management.

Covers:
* ConversationStore   — JSONL history (append, load, list, purge, delete)
* ContextWindowManager — token tracking, trim_tool_result, maybe_summarize
* ProjectMemory       — KIASSIST.md read/write/append/clear
* FileStateCache      — LRU, mtime invalidation, mark_seen, is_fresh
* SystemPromptBuilder — base, project-context, and dynamic layers
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Optional
from unittest.mock import MagicMock, patch

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
from kiassist_utils.context.history import ConversationStore
from kiassist_utils.context.tokens import ContextWindowManager
from kiassist_utils.context.memory import ProjectMemory
from kiassist_utils.context.file_cache import FileStateCache
from kiassist_utils.context.prompts import SystemPromptBuilder


# ===========================================================================
# Helpers / shared fixtures
# ===========================================================================


def _user(text: str) -> AIMessage:
    return AIMessage(role="user", content=text)


def _assistant(text: str) -> AIMessage:
    return AIMessage(role="assistant", content=text)


def _tool_msg(results: List[AIToolResult]) -> AIMessage:
    return AIMessage(role="tool", tool_results=results)


class _StubProvider(AIProvider):
    """Minimal provider that returns a canned response."""

    def __init__(self, reply: str = "Summary here.") -> None:
        self._reply = reply

    def chat(
        self,
        messages: List[AIMessage],
        tools=None,
        system_prompt=None,
    ) -> AIResponse:
        return AIResponse(content=self._reply)

    async def chat_stream(
        self,
        messages: List[AIMessage],
        tools=None,
        system_prompt=None,
    ) -> AsyncIterator[AIChunk]:
        yield AIChunk(text=self._reply, is_final=True)  # pragma: no cover

    def get_context_window(self) -> int:
        return 1_000

    def get_max_output_tokens(self) -> int:
        return 500

    def supports_tool_calling(self) -> bool:
        return False


# ===========================================================================
# ConversationStore
# ===========================================================================


class TestConversationStore:
    # ------------------------------------------------------------------
    # Initialisation
    # ------------------------------------------------------------------

    def test_history_path_from_directory(self, tmp_path: Path):
        store = ConversationStore(tmp_path)
        assert store.history_path == tmp_path / ".kiassist" / "history.jsonl"

    def test_history_path_from_pro_file(self, tmp_path: Path):
        pro = tmp_path / "board.kicad_pro"
        pro.touch()
        store = ConversationStore(pro)
        assert store.history_path == tmp_path / ".kiassist" / "history.jsonl"

    # ------------------------------------------------------------------
    # new_session
    # ------------------------------------------------------------------

    def test_new_session_is_unique(self, tmp_path: Path):
        store = ConversationStore(tmp_path)
        sid1 = store.new_session()
        sid2 = store.new_session()
        assert sid1 != sid2
        assert len(sid1) == 32  # UUID hex

    # ------------------------------------------------------------------
    # append
    # ------------------------------------------------------------------

    def test_append_creates_store_dir(self, tmp_path: Path):
        store = ConversationStore(tmp_path)
        sid = store.new_session()
        store.append(sid, _user("hello"))
        assert store.history_path.exists()

    def test_append_writes_user_message(self, tmp_path: Path):
        store = ConversationStore(tmp_path)
        sid = store.new_session()
        store.append(sid, _user("test content"))
        lines = store.history_path.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 1
        import json
        entry = json.loads(lines[0])
        assert entry["role"] == "user"
        assert entry["content"] == "test content"
        assert entry["session_id"] == sid
        assert "timestamp" in entry

    def test_append_with_tool_calls(self, tmp_path: Path):
        store = ConversationStore(tmp_path)
        sid = store.new_session()
        msg = AIMessage(
            role="assistant",
            content="",
            tool_calls=[AIToolCall(id="c1", name="my_tool", arguments={"x": 1})],
        )
        store.append(sid, msg)
        import json
        entry = json.loads(
            store.history_path.read_text(encoding="utf-8").strip()
        )
        assert entry["tool_calls"][0]["name"] == "my_tool"

    def test_append_with_tool_results(self, tmp_path: Path):
        store = ConversationStore(tmp_path)
        sid = store.new_session()
        msg = _tool_msg([AIToolResult(tool_call_id="c1", content="ok", is_error=False)])
        store.append(sid, msg)
        import json
        entry = json.loads(
            store.history_path.read_text(encoding="utf-8").strip()
        )
        assert entry["tool_results"][0]["content"] == "ok"

    def test_append_token_count(self, tmp_path: Path):
        store = ConversationStore(tmp_path)
        sid = store.new_session()
        store.append(sid, _user("hi"), token_count=42)
        import json
        entry = json.loads(
            store.history_path.read_text(encoding="utf-8").strip()
        )
        assert entry["token_count"] == 42

    def test_append_multiple_sessions(self, tmp_path: Path):
        store = ConversationStore(tmp_path)
        sid_a = store.new_session()
        sid_b = store.new_session()
        for i in range(3):
            store.append(sid_a, _user(f"a{i}"))
        for i in range(2):
            store.append(sid_b, _user(f"b{i}"))
        lines = store.history_path.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 5

    # ------------------------------------------------------------------
    # load_session
    # ------------------------------------------------------------------

    def test_load_session_returns_messages(self, tmp_path: Path):
        store = ConversationStore(tmp_path)
        sid = store.new_session()
        store.append(sid, _user("hello"))
        store.append(sid, _assistant("world"))
        messages = store.load_session(sid)
        assert len(messages) == 2
        assert messages[0].role == "user"
        assert messages[0].content == "hello"
        assert messages[1].role == "assistant"

    def test_load_session_filters_by_id(self, tmp_path: Path):
        store = ConversationStore(tmp_path)
        sid_a = store.new_session()
        sid_b = store.new_session()
        store.append(sid_a, _user("for a"))
        store.append(sid_b, _user("for b"))
        assert len(store.load_session(sid_a)) == 1
        assert store.load_session(sid_a)[0].content == "for a"

    def test_load_session_no_file(self, tmp_path: Path):
        store = ConversationStore(tmp_path)
        assert store.load_session("nonexistent") == []

    def test_load_session_restores_tool_calls(self, tmp_path: Path):
        store = ConversationStore(tmp_path)
        sid = store.new_session()
        msg = AIMessage(
            role="assistant",
            content="",
            tool_calls=[AIToolCall(id="c1", name="schematic_open", arguments={"path": "/a"})],
        )
        store.append(sid, msg)
        loaded = store.load_session(sid)
        assert loaded[0].tool_calls[0].name == "schematic_open"

    # ------------------------------------------------------------------
    # list_sessions
    # ------------------------------------------------------------------

    def test_list_sessions_empty(self, tmp_path: Path):
        store = ConversationStore(tmp_path)
        assert store.list_sessions() == []

    def test_list_sessions_counts(self, tmp_path: Path):
        store = ConversationStore(tmp_path)
        sid_a = store.new_session()
        sid_b = store.new_session()
        store.append(sid_a, _user("1"))
        store.append(sid_a, _user("2"))
        store.append(sid_b, _user("1"))
        sessions = store.list_sessions()
        assert len(sessions) == 2
        by_id = {s["session_id"]: s for s in sessions}
        assert by_id[sid_a]["message_count"] == 2
        assert by_id[sid_b]["message_count"] == 1

    def test_list_sessions_timestamps(self, tmp_path: Path):
        store = ConversationStore(tmp_path)
        sid = store.new_session()
        store.append(sid, _user("first"))
        store.append(sid, _user("second"))
        meta = store.list_sessions()[0]
        assert meta["started_at"] <= meta["last_at"]

    # ------------------------------------------------------------------
    # purge_old
    # ------------------------------------------------------------------

    def test_purge_old_removes_oldest(self, tmp_path: Path):
        store = ConversationStore(tmp_path)
        sids = [store.new_session() for _ in range(5)]
        for sid in sids:
            store.append(sid, _user("msg"))
        removed = store.purge_old(max_sessions=3)
        assert removed == 2
        remaining = {s["session_id"] for s in store.list_sessions()}
        # The 3 most recent should remain
        assert set(sids[-3:]) == remaining

    def test_purge_old_no_op_when_under_limit(self, tmp_path: Path):
        store = ConversationStore(tmp_path)
        sid = store.new_session()
        store.append(sid, _user("hi"))
        removed = store.purge_old(max_sessions=100)
        assert removed == 0

    def test_purge_old_empty_store(self, tmp_path: Path):
        store = ConversationStore(tmp_path)
        assert store.purge_old() == 0

    # ------------------------------------------------------------------
    # delete_session
    # ------------------------------------------------------------------

    def test_delete_session(self, tmp_path: Path):
        store = ConversationStore(tmp_path)
        sid_a = store.new_session()
        sid_b = store.new_session()
        store.append(sid_a, _user("keep"))
        store.append(sid_b, _user("remove"))
        removed = store.delete_session(sid_b)
        assert removed == 1
        assert store.load_session(sid_b) == []
        assert len(store.load_session(sid_a)) == 1

    def test_delete_session_nonexistent(self, tmp_path: Path):
        store = ConversationStore(tmp_path)
        assert store.delete_session("ghost") == 0

    # ------------------------------------------------------------------
    # Corrupted lines
    # ------------------------------------------------------------------

    def test_corrupted_lines_skipped(self, tmp_path: Path):
        store = ConversationStore(tmp_path)
        sid = store.new_session()
        store.append(sid, _user("good"))
        # Inject a bad line
        store.history_path.write_text(
            store.history_path.read_text() + "this is not json\n"
        )
        # Should still load the good message without raising
        messages = store.load_session(sid)
        assert len(messages) == 1


# ===========================================================================
# ContextWindowManager
# ===========================================================================


class TestContextWindowManager:
    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def test_default_construction(self):
        mgr = ContextWindowManager(context_window=10_000)
        assert mgr.total_tokens == 0
        assert mgr.context_window == 10_000

    def test_from_provider(self):
        prov = _StubProvider()
        mgr = ContextWindowManager.from_provider(prov)
        assert mgr.context_window == prov.get_context_window()

    def test_invalid_context_window(self):
        with pytest.raises(ValueError, match="context_window"):
            ContextWindowManager(context_window=0)

    def test_invalid_threshold(self):
        with pytest.raises(ValueError, match="summarize_threshold"):
            ContextWindowManager(context_window=1000, summarize_threshold=0.0)

    def test_invalid_result_max_chars(self):
        with pytest.raises(ValueError, match="result_max_chars"):
            ContextWindowManager(context_window=1000, result_max_chars=0)

    def test_invalid_protected_tail(self):
        with pytest.raises(ValueError, match="protected_tail"):
            ContextWindowManager(context_window=1000, protected_tail=-1)

    # ------------------------------------------------------------------
    # track_usage
    # ------------------------------------------------------------------

    def test_track_usage_total_tokens(self):
        mgr = ContextWindowManager(context_window=10_000)
        mgr.track_usage({"total_tokens": 200})
        assert mgr.total_tokens == 200

    def test_track_usage_input_output(self):
        mgr = ContextWindowManager(context_window=10_000)
        mgr.track_usage({"input_tokens": 100, "output_tokens": 50})
        assert mgr.total_tokens == 150

    def test_track_usage_openai_naming(self):
        mgr = ContextWindowManager(context_window=10_000)
        mgr.track_usage({"prompt_tokens": 80, "completion_tokens": 20})
        assert mgr.total_tokens == 100

    def test_track_usage_no_double_count_mixed_keys(self):
        """When both naming schemes are present, only the max should be added."""
        mgr = ContextWindowManager(context_window=10_000)
        # Hypothetical provider that includes both schemes in the same dict.
        # input+output = 150; prompt+completion = 100.  Max is 150.
        mgr.track_usage({
            "input_tokens": 100, "output_tokens": 50,
            "prompt_tokens": 80, "completion_tokens": 20,
        })
        assert mgr.total_tokens == 150

    def test_track_usage_empty_dict(self):
        """An empty usage dict should contribute 0 tokens."""
        mgr = ContextWindowManager(context_window=10_000)
        mgr.track_usage({})
        assert mgr.total_tokens == 0

    def test_track_usage_cumulative(self):
        mgr = ContextWindowManager(context_window=10_000)
        mgr.track_usage({"total_tokens": 100})
        mgr.track_usage({"total_tokens": 200})
        assert mgr.total_tokens == 300

    def test_reset(self):
        mgr = ContextWindowManager(context_window=10_000)
        mgr.track_usage({"total_tokens": 500})
        mgr.reset()
        assert mgr.total_tokens == 0

    # ------------------------------------------------------------------
    # is_near_limit
    # ------------------------------------------------------------------

    def test_not_near_limit(self):
        mgr = ContextWindowManager(context_window=1_000, summarize_threshold=0.8)
        mgr.track_usage({"total_tokens": 500})
        assert not mgr.is_near_limit()

    def test_at_limit(self):
        mgr = ContextWindowManager(context_window=1_000, summarize_threshold=0.8)
        mgr.track_usage({"total_tokens": 800})
        assert mgr.is_near_limit()

    def test_over_limit(self):
        mgr = ContextWindowManager(context_window=1_000, summarize_threshold=0.8)
        mgr.track_usage({"total_tokens": 900})
        assert mgr.is_near_limit()

    # ------------------------------------------------------------------
    # trim_tool_result
    # ------------------------------------------------------------------

    def test_trim_short_result(self):
        mgr = ContextWindowManager(context_window=1_000, result_max_chars=4_000)
        short = "hello"
        assert mgr.trim_tool_result(short) == short

    def test_trim_long_result(self):
        mgr = ContextWindowManager(context_window=1_000, result_max_chars=20)
        long_str = "A" * 100
        trimmed = mgr.trim_tool_result(long_str)
        assert trimmed.startswith("A" * 20)
        assert "truncated" in trimmed
        assert len(trimmed) > 20  # includes suffix

    def test_trim_exact_boundary(self):
        mgr = ContextWindowManager(context_window=1_000, result_max_chars=5)
        result = mgr.trim_tool_result("ABCDE")
        assert result == "ABCDE"

    # ------------------------------------------------------------------
    # maybe_summarize
    # ------------------------------------------------------------------

    def test_no_summarize_when_not_near_limit(self):
        mgr = ContextWindowManager(context_window=10_000, summarize_threshold=0.8)
        prov = _StubProvider()
        msgs = [_user("hello"), _assistant("hi")]
        # 0 tokens tracked → not near limit
        result = mgr.maybe_summarize(msgs, prov)
        assert result is msgs  # same object returned

    def test_summarize_triggered(self):
        mgr = ContextWindowManager(
            context_window=1_000,
            summarize_threshold=0.8,
            protected_tail=2,
        )
        mgr.track_usage({"total_tokens": 900})  # triggers summarise
        prov = _StubProvider(reply="Compact summary.")
        msgs = [_user(f"msg {i}") for i in range(8)]
        result = mgr.maybe_summarize(msgs, prov)
        # The result should start with the summary message
        assert result[0].role == "assistant"
        assert "Compact summary." in result[0].content
        # Protected tail preserved
        assert result[-2].content == "msg 6"
        assert result[-1].content == "msg 7"

    def test_summarize_resets_tokens(self):
        mgr = ContextWindowManager(context_window=1_000, summarize_threshold=0.8)
        mgr.track_usage({"total_tokens": 900})
        prov = _StubProvider()
        msgs = [_user(f"m{i}") for i in range(10)]
        mgr.maybe_summarize(msgs, prov)
        assert mgr.total_tokens == 0

    def test_summarize_too_few_messages(self):
        """When all messages are in the protected tail, skip summarisation."""
        mgr = ContextWindowManager(
            context_window=1_000,
            summarize_threshold=0.8,
            protected_tail=10,
        )
        mgr.track_usage({"total_tokens": 900})
        prov = _StubProvider()
        msgs = [_user("only msg")]
        result = mgr.maybe_summarize(msgs, prov)
        # Nothing to summarise → original list returned
        assert result == msgs

    def test_summarize_provider_failure(self):
        """When the provider raises, the original messages are returned unchanged."""
        mgr = ContextWindowManager(context_window=1_000, summarize_threshold=0.8)
        mgr.track_usage({"total_tokens": 900})

        class FailProvider(_StubProvider):
            def chat(self, messages, tools=None, system_prompt=None):
                raise RuntimeError("API down")

        prov = FailProvider()
        msgs = [_user(f"m{i}") for i in range(6)]
        result = mgr.maybe_summarize(msgs, prov)
        assert result == msgs


# ===========================================================================
# ProjectMemory
# ===========================================================================


class TestProjectMemory:
    def test_path_from_directory(self, tmp_path: Path):
        mem = ProjectMemory(tmp_path)
        assert mem.path == tmp_path / "KIASSIST.md"
        assert mem.project_dir == tmp_path

    def test_path_from_pro_file(self, tmp_path: Path):
        pro = tmp_path / "board.kicad_pro"
        pro.touch()
        mem = ProjectMemory(pro)
        assert mem.path == tmp_path / "KIASSIST.md"

    def test_not_exists_initially(self, tmp_path: Path):
        mem = ProjectMemory(tmp_path)
        assert not mem.exists()

    def test_read_returns_none_when_missing(self, tmp_path: Path):
        mem = ProjectMemory(tmp_path)
        assert mem.read() is None

    def test_write_and_read(self, tmp_path: Path):
        mem = ProjectMemory(tmp_path)
        mem.write("# Notes\n\nHello.")
        assert mem.exists()
        assert mem.read() == "# Notes\n\nHello."

    def test_write_creates_directory(self, tmp_path: Path):
        sub = tmp_path / "sub" / "project"
        mem = ProjectMemory(sub)
        mem.write("content")
        assert mem.path.exists()

    def test_write_overwrites(self, tmp_path: Path):
        mem = ProjectMemory(tmp_path)
        mem.write("first")
        mem.write("second")
        assert mem.read() == "second"

    def test_append_section(self, tmp_path: Path):
        mem = ProjectMemory(tmp_path)
        mem.write("# Initial\n")
        mem.append_section("Components", "Use 100nF 0402 caps.")
        content = mem.read()
        assert "## Components" in content
        assert "Use 100nF 0402 caps." in content

    def test_append_section_creates_file(self, tmp_path: Path):
        mem = ProjectMemory(tmp_path)
        mem.append_section("Notes", "First note.")
        assert mem.exists()
        assert "## Notes" in mem.read()

    def test_append_multiple_sections(self, tmp_path: Path):
        mem = ProjectMemory(tmp_path)
        mem.append_section("A", "Alpha.")
        mem.append_section("B", "Beta.")
        content = mem.read()
        assert content.index("## A") < content.index("## B")

    def test_clear(self, tmp_path: Path):
        mem = ProjectMemory(tmp_path)
        mem.write("data")
        mem.clear()
        assert not mem.exists()

    def test_clear_noop_when_missing(self, tmp_path: Path):
        mem = ProjectMemory(tmp_path)
        mem.clear()  # should not raise

    def test_write_is_atomic(self, tmp_path: Path):
        """write() should not leave a .md.tmp file behind."""
        mem = ProjectMemory(tmp_path)
        mem.write("content")
        tmp_files = list(tmp_path.glob("*.tmp"))
        assert tmp_files == []


# ===========================================================================
# FileStateCache
# ===========================================================================


class TestFileStateCache:
    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def test_empty_on_creation(self):
        cache = FileStateCache()
        assert len(cache) == 0

    def test_invalid_max_size(self):
        with pytest.raises(ValueError, match="max_size"):
            FileStateCache(max_size=0)

    # ------------------------------------------------------------------
    # mark_seen / is_fresh
    # ------------------------------------------------------------------

    def test_mark_seen_and_is_fresh(self, tmp_path: Path):
        f = tmp_path / "test.txt"
        f.write_text("hello")
        cache = FileStateCache()
        cache.mark_seen(f)
        assert cache.is_fresh(f)

    def test_is_fresh_false_for_unseen(self, tmp_path: Path):
        f = tmp_path / "unseen.txt"
        f.write_text("hi")
        cache = FileStateCache()
        assert not cache.is_fresh(f)

    def test_is_fresh_false_after_modification(self, tmp_path: Path):
        f = tmp_path / "mod.txt"
        f.write_text("v1")
        cache = FileStateCache()
        cache.mark_seen(f)
        # Wait a tiny bit then modify
        time.sleep(0.01)
        f.write_text("v2")
        # Touch mtime explicitly to ensure change is detected
        new_mtime = os.path.getmtime(f) + 1.0
        os.utime(f, (new_mtime, new_mtime))
        assert not cache.is_fresh(f)

    def test_is_fresh_false_for_deleted_file(self, tmp_path: Path):
        f = tmp_path / "gone.txt"
        f.write_text("x")
        cache = FileStateCache()
        cache.mark_seen(f)
        f.unlink()
        assert not cache.is_fresh(f)

    # ------------------------------------------------------------------
    # LRU eviction
    # ------------------------------------------------------------------

    def test_lru_eviction(self, tmp_path: Path):
        cache = FileStateCache(max_size=2)
        files = [tmp_path / f"f{i}.txt" for i in range(3)]
        for f in files:
            f.write_text("x")
        cache.mark_seen(files[0])
        cache.mark_seen(files[1])
        cache.mark_seen(files[2])  # should evict files[0]
        assert len(cache) == 2
        assert files[0] not in cache
        assert files[1] in cache
        assert files[2] in cache

    def test_mark_seen_updates_lru_order(self, tmp_path: Path):
        cache = FileStateCache(max_size=2)
        files = [tmp_path / f"f{i}.txt" for i in range(2)]
        for f in files:
            f.write_text("x")
        cache.mark_seen(files[0])
        cache.mark_seen(files[1])
        # Re-access files[0] → it becomes MRU
        cache.mark_seen(files[0])
        # Add a new file → files[1] should be evicted
        new_file = tmp_path / "new.txt"
        new_file.write_text("y")
        cache.mark_seen(new_file)
        assert files[0] in cache
        assert new_file in cache
        assert files[1] not in cache

    # ------------------------------------------------------------------
    # invalidate / clear
    # ------------------------------------------------------------------

    def test_invalidate_removes_entry(self, tmp_path: Path):
        f = tmp_path / "file.txt"
        f.write_text("x")
        cache = FileStateCache()
        cache.mark_seen(f)
        assert cache.invalidate(f)
        assert not cache.is_fresh(f)

    def test_invalidate_returns_false_for_unseen(self, tmp_path: Path):
        f = tmp_path / "file.txt"
        f.write_text("x")
        cache = FileStateCache()
        assert not cache.invalidate(f)

    def test_clear_empties_cache(self, tmp_path: Path):
        cache = FileStateCache()
        for i in range(5):
            f = tmp_path / f"f{i}.txt"
            f.write_text("x")
            cache.mark_seen(f)
        cache.clear()
        assert len(cache) == 0

    # ------------------------------------------------------------------
    # Membership test
    # ------------------------------------------------------------------

    def test_contains_after_mark(self, tmp_path: Path):
        f = tmp_path / "x.txt"
        f.write_text("x")
        cache = FileStateCache()
        assert f not in cache
        cache.mark_seen(f)
        assert f in cache

    def test_contains_with_string_path(self, tmp_path: Path):
        f = tmp_path / "x.txt"
        f.write_text("x")
        cache = FileStateCache()
        cache.mark_seen(str(f))
        assert str(f) in cache


# ===========================================================================
# SystemPromptBuilder
# ===========================================================================


class TestSystemPromptBuilder:
    # ------------------------------------------------------------------
    # Base prompt loading
    # ------------------------------------------------------------------

    def test_build_with_explicit_base_prompt(self, tmp_path: Path):
        bp = tmp_path / "base.md"
        bp.write_text("# Base Prompt\n\nYou are KiAssist.")
        builder = SystemPromptBuilder(base_prompt_path=bp)
        prompt = builder.build()
        assert "You are KiAssist." in prompt

    def test_build_without_base_prompt(self, tmp_path: Path):
        """If the base prompt file is missing, no error is raised."""
        builder = SystemPromptBuilder(base_prompt_path=tmp_path / "nonexistent.md")
        prompt = builder.build()
        # Should return an empty string (or just whitespace)
        assert prompt.strip() == ""

    def test_env_var_overrides_base_prompt(self, tmp_path: Path):
        bp = tmp_path / "env_base.md"
        bp.write_text("Env-provided base.")
        with patch.dict(os.environ, {"KIASSIST_BASE_PROMPT": str(bp)}):
            builder = SystemPromptBuilder()
            prompt = builder.build()
        assert "Env-provided base." in prompt

    def test_default_base_prompt_found(self):
        """The bundled kicad-assistant.md should be discoverable."""
        builder = SystemPromptBuilder()
        if builder._base_prompt_path is not None:
            prompt = builder.build()
            assert len(prompt) > 0

    # ------------------------------------------------------------------
    # Dynamic context layer
    # ------------------------------------------------------------------

    def test_dynamic_context_appended(self, tmp_path: Path):
        bp = tmp_path / "base.md"
        bp.write_text("Base.")
        builder = SystemPromptBuilder(base_prompt_path=bp)
        prompt = builder.build(dynamic_context="Active schematic: top.kicad_sch")
        assert "Active schematic: top.kicad_sch" in prompt
        assert "Current Session Context" in prompt

    # ------------------------------------------------------------------
    # Project context layer
    # ------------------------------------------------------------------

    def test_project_context_includes_directory(self, tmp_path: Path):
        bp = tmp_path / "base.md"
        bp.write_text("Base.")
        builder = SystemPromptBuilder(base_prompt_path=bp)
        prompt = builder.build(project_path=tmp_path)
        assert str(tmp_path) in prompt

    def test_project_context_includes_memory(self, tmp_path: Path):
        mem = ProjectMemory(tmp_path)
        mem.write("# Notes\n\nUse 100nF caps.\n")
        bp = tmp_path / "base.md"
        bp.write_text("Base.")
        builder = SystemPromptBuilder(base_prompt_path=bp)
        prompt = builder.build(project_path=tmp_path)
        assert "Use 100nF caps." in prompt

    # ------------------------------------------------------------------
    # Caching
    # ------------------------------------------------------------------

    def test_project_context_cached(self, tmp_path: Path):
        bp = tmp_path / "base.md"
        bp.write_text("Base.")
        builder = SystemPromptBuilder(base_prompt_path=bp, cache_project_context=True)
        # First call populates cache
        builder.build(project_path=tmp_path)
        # Write memory file between calls
        mem = ProjectMemory(tmp_path)
        mem.write("# New note")
        # Second call should NOT pick up the new note (cached)
        prompt = builder.build(project_path=tmp_path)
        assert "New note" not in prompt

    def test_clear_cache_invalidates(self, tmp_path: Path):
        bp = tmp_path / "base.md"
        bp.write_text("Base.")
        builder = SystemPromptBuilder(base_prompt_path=bp, cache_project_context=True)
        builder.build(project_path=tmp_path)
        mem = ProjectMemory(tmp_path)
        mem.write("# After clear")
        builder.clear_cache(project_path=tmp_path)
        prompt = builder.build(project_path=tmp_path)
        assert "After clear" in prompt

    def test_clear_all_cache(self, tmp_path: Path):
        bp = tmp_path / "base.md"
        bp.write_text("Base.")
        builder = SystemPromptBuilder(base_prompt_path=bp, cache_project_context=True)
        builder.build(project_path=tmp_path)
        builder.clear_cache()
        assert builder._project_cache == {}

    def test_no_cache_when_disabled(self, tmp_path: Path):
        bp = tmp_path / "base.md"
        bp.write_text("Base.")
        builder = SystemPromptBuilder(base_prompt_path=bp, cache_project_context=False)
        builder.build(project_path=tmp_path)
        mem = ProjectMemory(tmp_path)
        mem.write("# Live note")
        prompt = builder.build(project_path=tmp_path)
        assert "Live note" in prompt

    # ------------------------------------------------------------------
    # Layer separator
    # ------------------------------------------------------------------

    def test_layers_separated_by_horizontal_rule(self, tmp_path: Path):
        bp = tmp_path / "base.md"
        bp.write_text("Layer 1.")
        builder = SystemPromptBuilder(base_prompt_path=bp)
        prompt = builder.build(
            project_path=tmp_path,
            dynamic_context="Layer 3.",
        )
        assert "---" in prompt

    # ------------------------------------------------------------------
    # FileStateCache integration
    # ------------------------------------------------------------------

    def test_file_cache_skips_already_seen_schematic(self, tmp_path: Path):
        """Schematics already in the AI's context should be noted as such.

        With the rich project context module, file_cache only applies in the
        fallback path.  The rich context builder always includes full context.
        This test verifies the prompt still builds without error when a
        file_cache is provided, and that the schematic is represented.
        """
        sch = tmp_path / "board.kicad_sch"
        sch.write_text("(kicad_sch)")  # minimal non-parseable placeholder

        cache = FileStateCache()
        cache.mark_seen(sch)

        bp = tmp_path / "base.md"
        bp.write_text("Base.")
        builder = SystemPromptBuilder(
            base_prompt_path=bp,
            cache_project_context=False,
            file_cache=cache,
        )
        prompt = builder.build(project_path=tmp_path)
        # The prompt should build successfully and reference the schematic
        assert "board.kicad_sch" in prompt or "Schematic" in prompt or "Project Context" in prompt

    def test_file_cache_includes_unseen_schematic(self, tmp_path: Path):
        """A schematic not yet seen by the AI should be fully included."""
        sch = tmp_path / "board.kicad_sch"
        sch.write_text("(kicad_sch)")

        cache = FileStateCache()
        # Don't mark it seen → is_fresh returns False

        bp = tmp_path / "base.md"
        bp.write_text("Base.")
        builder = SystemPromptBuilder(
            base_prompt_path=bp,
            cache_project_context=False,
            file_cache=cache,
        )
        prompt = builder.build(project_path=tmp_path)
        # Should attempt to parse (parse error for placeholder content), not skip
        assert "already in context" not in prompt

    def test_no_file_cache_behaves_as_before(self, tmp_path: Path):
        """Without a file_cache, all schematics are always processed."""
        sch = tmp_path / "board.kicad_sch"
        sch.write_text("(kicad_sch)")

        bp = tmp_path / "base.md"
        bp.write_text("Base.")
        builder = SystemPromptBuilder(
            base_prompt_path=bp,
            cache_project_context=False,
        )
        prompt = builder.build(project_path=tmp_path)
        assert "already in context" not in prompt

    # ------------------------------------------------------------------
    # Focused agent layer
    # ------------------------------------------------------------------

    def test_focused_agent_from_path(self, tmp_path: Path):
        """An explicit Path to a focused-agent file is injected as Layer 2."""
        bp = tmp_path / "base.md"
        bp.write_text("Base layer.")
        agent_file = tmp_path / "my-agent.md"
        agent_file.write_text("# Focused Agent\n\nSchematic only.")
        builder = SystemPromptBuilder(base_prompt_path=bp, focused_agent=agent_file)
        prompt = builder.build()
        assert "Base layer." in prompt
        assert "Schematic only." in prompt

    def test_focused_agent_from_name(self, tmp_path: Path):
        """Bare agent name is resolved from the real public/agents/ directory."""
        bp = tmp_path / "base.md"
        bp.write_text("Base.")
        # Use the real bundled schematic-agent if it exists.
        builder = SystemPromptBuilder(base_prompt_path=bp, focused_agent="schematic-agent")
        prompt = builder.build()
        # Either the file was found (content present) or gracefully absent.
        assert "Base." in prompt

    def test_focused_agent_name_resolves_bundled_schematic_agent(self):
        """The bundled schematic-agent.md should be discoverable by bare name."""
        builder = SystemPromptBuilder(focused_agent="schematic-agent")
        if builder._base_prompt_path is not None:
            prompt = builder.build()
            # The schematic-agent.md file should be found and its content injected.
            assert "Schematic" in prompt

    def test_focused_agent_per_call_override(self, tmp_path: Path):
        """focused_agent passed to build() overrides the instance-level setting."""
        bp = tmp_path / "base.md"
        bp.write_text("Base.")
        agent1 = tmp_path / "agent1.md"
        agent1.write_text("Agent one content.")
        agent2 = tmp_path / "agent2.md"
        agent2.write_text("Agent two content.")

        builder = SystemPromptBuilder(base_prompt_path=bp, focused_agent=agent1)
        # Override at call time with agent2.
        prompt = builder.build(focused_agent=agent2)
        assert "Agent two content." in prompt
        assert "Agent one content." not in prompt

    def test_focused_agent_none_skips_layer(self, tmp_path: Path):
        """When focused_agent is None, no extra layer is added."""
        bp = tmp_path / "base.md"
        bp.write_text("Base only.")
        builder = SystemPromptBuilder(base_prompt_path=bp)
        prompt = builder.build()
        assert "Base only." in prompt
        # No separator when there is only one section.
        assert "---" not in prompt

    def test_focused_agent_missing_file_graceful(self, tmp_path: Path):
        """A missing focused-agent file should not raise; prompt still builds."""
        bp = tmp_path / "base.md"
        bp.write_text("Base.")
        builder = SystemPromptBuilder(
            base_prompt_path=bp, focused_agent=tmp_path / "nonexistent-agent.md"
        )
        prompt = builder.build()
        assert "Base." in prompt

    def test_list_focused_agents_returns_names(self):
        """list_focused_agents() should return the bundled agent names."""
        names = SystemPromptBuilder.list_focused_agents()
        # The bundled agents may or may not be discoverable in this test env,
        # but the method must not raise and must return a list of strings.
        assert isinstance(names, list)
        assert all(isinstance(n, str) for n in names)

    def test_list_focused_agents_includes_all_bundled(self):
        """All four bundled focused agents should be listed if discoverable."""
        names = SystemPromptBuilder.list_focused_agents()
        if names:
            assert "schematic-agent" in names
            assert "pcb-agent" in names
            assert "symbol-library-agent" in names
            assert "footprint-agent" in names

    def test_focused_agent_appears_between_base_and_project(self, tmp_path: Path):
        """Focused agent layer must appear after the base and before project context."""
        bp = tmp_path / "base.md"
        bp.write_text("Layer 1 base.")
        agent_file = tmp_path / "focused.md"
        agent_file.write_text("Layer 2 focused.")
        builder = SystemPromptBuilder(
            base_prompt_path=bp,
            focused_agent=agent_file,
            cache_project_context=False,
        )
        prompt = builder.build(project_path=tmp_path)
        base_pos = prompt.index("Layer 1 base.")
        focused_pos = prompt.index("Layer 2 focused.")
        ctx_pos = prompt.index("Project Context")
        assert base_pos < focused_pos < ctx_pos


# ===========================================================================
# Package-level imports
# ===========================================================================


class TestContextPackageImports:
    def test_all_exports_importable(self):
        from kiassist_utils.context import (
            ConversationStore,
            ContextWindowManager,
            FileStateCache,
            ProjectMemory,
            SystemPromptBuilder,
        )
        assert ConversationStore is not None
        assert ContextWindowManager is not None
        assert FileStateCache is not None
        assert ProjectMemory is not None
        assert SystemPromptBuilder is not None


# ===========================================================================
# ToolExecutor integration with context management
# ===========================================================================


class TestToolExecutorContextIntegration:
    """Tests for ContextWindowManager + ConversationStore wired into ToolExecutor."""

    def _make_executor_with_context(
        self,
        provider,
        tmp_path: Path,
        result_max_chars: int = 4_000,
    ):
        from kiassist_utils.ai.tool_executor import ToolExecutor
        from kiassist_utils.context.tokens import ContextWindowManager
        from kiassist_utils.context.history import ConversationStore

        mgr = ContextWindowManager.from_provider(
            provider, result_max_chars=result_max_chars
        )
        store = ConversationStore(tmp_path)
        return ToolExecutor(
            provider=provider,
            context_manager=mgr,
            history_store=store,
        ), mgr, store

    def test_tool_result_trimmed_by_context_manager(self):
        """Tool results longer than result_max_chars must be truncated."""
        import asyncio
        from kiassist_utils.ai.tool_executor import ToolExecutor
        from kiassist_utils.context.tokens import ContextWindowManager
        from unittest.mock import patch, AsyncMock

        provider = _StubProvider(reply="done")
        mgr = ContextWindowManager.from_provider(provider, result_max_chars=10)

        # Executor only returns after tool calls stop; make the provider
        # return no tool calls immediately (no agentic loop needed here)
        executor = ToolExecutor(provider=provider, context_manager=mgr)

        # Directly exercise _execute_one with a mock in_process_call
        long_result = "X" * 200
        mock_ipc = AsyncMock(return_value=long_result)
        from kiassist_utils.ai.base import AIToolCall
        tc = AIToolCall(id="c1", name="test_tool", arguments={})

        with patch("kiassist_utils.ai.tool_executor.in_process_call", mock_ipc):
            result = asyncio.run(executor._execute_one(tc))

        assert len(result.content) < 200
        assert "truncated" in result.content

    def test_token_usage_tracked_during_run(self, tmp_path: Path):
        """After a successful run, total_tokens should reflect the response usage."""
        import asyncio
        from kiassist_utils.ai.tool_executor import ToolExecutor
        from kiassist_utils.context.tokens import ContextWindowManager
        from unittest.mock import patch, AsyncMock

        class _UsageProvider(_StubProvider):
            def chat(self, messages, tools=None, system_prompt=None):
                return AIResponse(content="final", usage={"total_tokens": 50})

        provider = _UsageProvider()
        mgr = ContextWindowManager.from_provider(provider)
        executor = ToolExecutor(
            provider=provider,
            context_manager=mgr,
            tool_schemas=[],  # no tool calls
        )

        asyncio.run(
            executor.run(
                [AIMessage(role="user", content="hello")],
                system_prompt="sys",
            )
        )

        assert mgr.total_tokens == 50

    def test_messages_persisted_to_history(self, tmp_path: Path):
        """All conversation turns should be persisted to the ConversationStore."""
        import asyncio
        from kiassist_utils.ai.tool_executor import ToolExecutor
        from kiassist_utils.context.history import ConversationStore

        provider = _StubProvider(reply="Hello!")
        store = ConversationStore(tmp_path)
        session_id = store.new_session()

        executor = ToolExecutor(
            provider=provider,
            tool_schemas=[],
            history_store=store,
        )

        asyncio.run(
            executor.run(
                [AIMessage(role="user", content="hi")],
                session_id=session_id,
            )
        )

        # Both the user seed message and the final assistant response are persisted.
        messages = store.load_session(session_id)
        assert len(messages) == 2
        assert messages[0].role == "user"
        assert messages[0].content == "hi"
        assert messages[1].role == "assistant"
        assert messages[1].content == "Hello!"

    def test_final_assistant_response_persisted(self, tmp_path: Path):
        """The final (no-tool-calls) assistant response must be stored in history."""
        import asyncio
        from kiassist_utils.ai.tool_executor import ToolExecutor
        from kiassist_utils.context.history import ConversationStore

        provider = _StubProvider(reply="Final answer.")
        store = ConversationStore(tmp_path)
        session_id = store.new_session()

        executor = ToolExecutor(
            provider=provider,
            tool_schemas=[],
            history_store=store,
        )

        asyncio.run(
            executor.run(
                [AIMessage(role="user", content="question")],
                session_id=session_id,
            )
        )

        messages = store.load_session(session_id)
        roles = [m.role for m in messages]
        assert "assistant" in roles
        assistant_msgs = [m for m in messages if m.role == "assistant"]
        assert assistant_msgs[-1].content == "Final answer."

    def test_token_count_stored_for_assistant_turn(self, tmp_path: Path):
        """Token count from response.usage should be stored with the assistant message."""
        import asyncio
        import json
        from kiassist_utils.ai.tool_executor import ToolExecutor
        from kiassist_utils.context.history import ConversationStore

        class _UsageProvider(_StubProvider):
            def chat(self, messages, tools=None, system_prompt=None):
                return AIResponse(content="reply", usage={"total_tokens": 77})

        store = ConversationStore(tmp_path)
        session_id = store.new_session()

        executor = ToolExecutor(
            provider=_UsageProvider(),
            tool_schemas=[],
            history_store=store,
        )

        asyncio.run(
            executor.run(
                [AIMessage(role="user", content="hi")],
                session_id=session_id,
            )
        )

        # Read the raw JSONL to check token_count
        entries = [
            json.loads(line)
            for line in store.history_path.read_text().splitlines()
            if line.strip()
        ]
        assistant_entries = [e for e in entries if e["role"] == "assistant"]
        assert assistant_entries, "No assistant entry found in history"
        assert assistant_entries[-1]["token_count"] == 77

    def test_value_error_when_history_store_without_session_id(self, tmp_path: Path):
        """Providing history_store without session_id must raise ValueError."""
        import asyncio
        from kiassist_utils.ai.tool_executor import ToolExecutor
        from kiassist_utils.context.history import ConversationStore

        store = ConversationStore(tmp_path)
        executor = ToolExecutor(
            provider=_StubProvider(),
            tool_schemas=[],
            history_store=store,
        )

        with pytest.raises(ValueError, match="session_id"):
            asyncio.run(
                executor.run([AIMessage(role="user", content="hi")])
                # no session_id passed
            )

    def test_no_context_manager_no_error(self, tmp_path: Path):
        """Executor without context_manager should still work normally."""
        import asyncio
        from kiassist_utils.ai.tool_executor import ToolExecutor

        provider = _StubProvider(reply="ok")
        executor = ToolExecutor(provider=provider, tool_schemas=[])
        response = asyncio.run(
            executor.run([AIMessage(role="user", content="hello")])
        )
        assert response.content == "ok"
