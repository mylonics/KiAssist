"""Context management package for KiAssist (Phase 4).

Provides four key subsystems:

* :mod:`~kiassist_utils.context.history`   — Append-only JSONL conversation
  history per project, with session management.
* :mod:`~kiassist_utils.context.tokens`    — Token counting and context-window
  management (auto-summarise when approaching the model's limit).
* :mod:`~kiassist_utils.context.prompts`   — Three-layer system prompt
  construction (base + project context + dynamic state).
* :mod:`~kiassist_utils.context.memory`    — KIASSIST.md project memory
  (design decisions, preferences, constraints).
* :mod:`~kiassist_utils.context.file_cache` — LRU cache tracking which files
  the AI has already "seen", with mtime-based invalidation.
"""

from .file_cache import FileStateCache
from .history import ConversationStore
from .memory import ProjectMemory
from .project_context import get_raw_context, get_llm_synthesized_context
from .prompts import SystemPromptBuilder
from .tokens import ContextWindowManager, usage_to_tokens

__all__ = [
    "ConversationStore",
    "ContextWindowManager",
    "FileStateCache",
    "ProjectMemory",
    "SystemPromptBuilder",
    "get_raw_context",
    "get_llm_synthesized_context",
    "usage_to_tokens",
]
