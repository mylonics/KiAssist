"""LRU file-state cache.

:class:`FileStateCache` tracks which files the AI has already "seen" (read via
MCP tools) and whether they have been modified since the last read.  This
allows the :class:`~kiassist_utils.context.prompts.SystemPromptBuilder` to
skip injecting content that is already present in the model's context window.

Example::

    cache = FileStateCache(max_size=128)

    cache.mark_seen("/project/main.kicad_sch")
    cache.mark_seen("/project/parts.kicad_sym")

    # Later — check if a file is still fresh (unmodified):
    if cache.is_fresh("/project/main.kicad_sch"):
        print("AI already has current content; skip re-injection.")
    else:
        print("File changed externally — re-inject content.")

The cache uses :func:`os.path.getmtime` to detect external modifications.
"""

from __future__ import annotations

import os
from collections import OrderedDict
from pathlib import Path
from typing import Optional


class FileStateCache:
    """LRU cache for file-read state with mtime-based invalidation.

    Args:
        max_size: Maximum number of entries to keep.  When the cache is full,
                  the least-recently-used entry is evicted.  Defaults to 128.
    """

    def __init__(self, max_size: int = 128) -> None:
        if max_size <= 0:
            raise ValueError("max_size must be positive")
        self._max_size = max_size
        # Maps str(absolute_path) → mtime_at_mark (float)
        self._cache: OrderedDict[str, float] = OrderedDict()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def max_size(self) -> int:
        """Maximum number of entries retained in the cache."""
        return self._max_size

    def mark_seen(self, path: str | Path) -> None:
        """Record that *path* has just been read by the AI.

        If the cache is at capacity the least-recently-used entry is evicted.

        Args:
            path: Absolute (or relative) path to the file.
        """
        key = self._key(path)
        mtime = self._get_mtime(key)
        if key in self._cache:
            # Move to most-recently-used position
            self._cache.move_to_end(key)
        else:
            if len(self._cache) >= self._max_size:
                self._cache.popitem(last=False)  # evict LRU
        self._cache[key] = mtime

    def is_fresh(self, path: str | Path) -> bool:
        """Return ``True`` if *path* is cached and has not been modified.

        A file is considered *stale* (returns ``False``) when:

        * It has never been marked as seen.
        * Its ``mtime`` differs from the recorded value (external write).
        * The file no longer exists.

        Args:
            path: Path to check.

        Returns:
            ``True`` if the cached mtime matches the current mtime.
        """
        key = self._key(path)
        if key not in self._cache:
            return False
        current_mtime = self._get_mtime(key)
        if current_mtime is None:
            # File disappeared — invalidate
            del self._cache[key]
            return False
        cached_mtime = self._cache[key]
        if cached_mtime is None:
            return False
        fresh = abs(current_mtime - cached_mtime) < 1e-3
        if not fresh:
            # Invalidate stale entry
            del self._cache[key]
        return fresh

    def invalidate(self, path: str | Path) -> bool:
        """Remove *path* from the cache.

        Args:
            path: Path to invalidate.

        Returns:
            ``True`` if the entry existed and was removed.
        """
        key = self._key(path)
        if key in self._cache:
            del self._cache[key]
            return True
        return False

    def clear(self) -> None:
        """Remove all entries from the cache."""
        self._cache.clear()

    def __len__(self) -> int:
        return len(self._cache)

    def __contains__(self, path: object) -> bool:
        """Support ``path in cache`` membership test."""
        if not isinstance(path, (str, Path)):
            return False
        return self._key(path) in self._cache

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _key(path: str | Path) -> str:
        """Normalise *path* to an absolute string key."""
        return str(Path(path).resolve())

    @staticmethod
    def _get_mtime(key: str) -> Optional[float]:
        """Return the mtime of *key* or ``None`` if the file is gone."""
        try:
            return os.path.getmtime(key)
        except OSError:
            return None
