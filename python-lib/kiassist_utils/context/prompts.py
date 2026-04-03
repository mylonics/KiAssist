"""Three-layer system prompt construction (Claude Code pattern).

:class:`SystemPromptBuilder` assembles a system prompt from three layers:

1. **Base layer** — KiCad assistant identity, available tools, response format.
   Loaded from ``public/agents/kicad-assistant.md`` (relative to the repo
   root, resolved via the ``KIASSIST_BASE_PROMPT`` env var or the default
   bundled path).

2. **Project context layer** — Auto-injected from project files:
   schematic component summary, library paths, design rules.  Also includes
   KIASSIST.md project memory if present.  Cached per session; refreshed on
   project switch.

3. **Dynamic context layer** — Caller-supplied runtime state: which KiCad
   editors are open, active schematic sheet, selected project path.

Example::

    builder = SystemPromptBuilder()

    # Once at session start:
    prompt = builder.build(
        project_path="/project/my_board",
        dynamic_context="Active schematic: top.kicad_sch",
    )

    # On project switch, call clear_cache() to force a refresh:
    builder.clear_cache()
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from .memory import ProjectMemory

if TYPE_CHECKING:  # pragma: no cover
    from .file_cache import FileStateCache

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Default base-prompt path resolution
# ---------------------------------------------------------------------------

# Callers may override via the KIASSIST_BASE_PROMPT environment variable.
_DEFAULT_BASE_PROMPT_FILENAME = "kicad-assistant.md"

# Walk upwards from this file to find the repo root (contains public/agents/).
_THIS_FILE = Path(__file__).resolve()


def _find_default_base_prompt() -> Optional[Path]:
    """Locate the default ``kicad-assistant.md`` by searching upwards."""
    candidate = _THIS_FILE
    for _ in range(10):  # at most 10 levels up
        candidate = candidate.parent
        prompt_path = candidate / "public" / "agents" / _DEFAULT_BASE_PROMPT_FILENAME
        if prompt_path.exists():
            return prompt_path
    return None


# Message used when a schematic is skipped because the AI already has current content.
_ALREADY_IN_CONTEXT_MSG = "already in context (unchanged)"


class SystemPromptBuilder:
    """Builds the three-layer KiAssist system prompt.

    Args:
        base_prompt_path: Explicit path to the base prompt Markdown file.
                          If ``None``, the builder first checks the
                          ``KIASSIST_BASE_PROMPT`` environment variable and
                          then searches upward for
                          ``public/agents/kicad-assistant.md``.
        cache_project_context: When ``True`` (default), project-context text
                               is cached after the first call to
                               :meth:`build` and reused on subsequent calls
                               with the same *project_path*.  Call
                               :meth:`clear_cache` to force a refresh.
        file_cache:       Optional :class:`~kiassist_utils.context.file_cache.FileStateCache`.
                          When provided, schematic files that the AI has
                          already seen (via MCP tools) and whose content has
                          not changed are noted as "already in context" rather
                          than having their full component list re-injected.
    """

    def __init__(
        self,
        base_prompt_path: Optional[str | Path] = None,
        cache_project_context: bool = True,
        file_cache: Optional["FileStateCache"] = None,
    ) -> None:
        # Resolve base prompt path
        if base_prompt_path is not None:
            self._base_prompt_path: Optional[Path] = Path(base_prompt_path)
        else:
            env_path = os.environ.get("KIASSIST_BASE_PROMPT")
            if env_path:
                self._base_prompt_path = Path(env_path)
            else:
                self._base_prompt_path = _find_default_base_prompt()

        self._cache_project_context = cache_project_context
        self._file_cache = file_cache
        # Maps project_dir → cached project-context string
        self._project_cache: Dict[str, str] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def build(
        self,
        project_path: Optional[str | Path] = None,
        dynamic_context: Optional[str] = None,
    ) -> str:
        """Assemble and return the complete system prompt.

        Args:
            project_path:    Path to the ``.kicad_pro`` file or project
                             directory.  If ``None`` only the base layer is
                             included.
            dynamic_context: Caller-supplied runtime state string appended as
                             the third layer.

        Returns:
            Complete system prompt as a single string.
        """
        sections: List[str] = []

        # --- Layer 1: Base prompt ---
        base = self._load_base_prompt()
        if base:
            sections.append(base.strip())

        # --- Layer 2: Project context ---
        if project_path is not None:
            project_ctx = self._get_project_context(project_path)
            if project_ctx:
                sections.append(project_ctx.strip())

        # --- Layer 3: Dynamic context ---
        if dynamic_context:
            sections.append(
                f"## Current Session Context\n\n{dynamic_context.strip()}"
            )

        return "\n\n---\n\n".join(sections)

    def clear_cache(self, project_path: Optional[str | Path] = None) -> None:
        """Invalidate the project-context cache.

        Args:
            project_path: If provided, only that project's cache entry is
                          removed.  If ``None``, the entire cache is cleared.
        """
        if project_path is None:
            self._project_cache.clear()
        else:
            key = self._project_key(project_path)
            self._project_cache.pop(key, None)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load_base_prompt(self) -> str:
        """Return the base prompt text, or an empty string on failure."""
        if self._base_prompt_path is None:
            return ""
        try:
            return self._base_prompt_path.read_text(encoding="utf-8")
        except OSError:
            return ""

    def _get_project_context(self, project_path: str | Path) -> str:
        """Return (possibly cached) project context for *project_path*."""
        key = self._project_key(project_path)
        if self._cache_project_context and key in self._project_cache:
            return self._project_cache[key]

        ctx = self._build_project_context(project_path)
        if self._cache_project_context:
            self._project_cache[key] = ctx
        return ctx

    @staticmethod
    def _project_key(project_path: str | Path) -> str:
        p = Path(project_path)
        project_dir = p.parent if p.is_file() else p
        return str(project_dir.resolve())

    def _build_project_context(self, project_path: str | Path) -> str:
        """Build the project-context layer from disk.

        Reads schematic files for a component summary, discovers library
        paths, lists design-rule files, and includes KIASSIST.md if present.

        When a :class:`~kiassist_utils.context.file_cache.FileStateCache` is
        attached, schematic files the AI has already read via MCP tools (and
        whose content is unchanged) are noted as "already in context" rather
        than having their full component list re-injected.

        Errors are silently swallowed so that context injection never breaks
        the chat flow.
        """
        p = Path(project_path)
        project_dir = p.parent if p.is_file() else p

        lines: List[str] = ["## Project Context"]

        lines.append(f"\n**Project directory:** `{project_dir}`")

        # --- Schematic component summary ---
        schematics = sorted(project_dir.rglob("*.kicad_sch"))
        if schematics:
            lines.append(f"\n**Schematics ({len(schematics)}):**")
            bom_rows: List[str] = []
            for sch_path in schematics:
                # If the file cache reports the AI already has this file
                # and it hasn't changed, skip the full component list.
                if self._file_cache is not None and self._file_cache.is_fresh(sch_path):
                    lines.append(f"- `{sch_path.name}` — {_ALREADY_IN_CONTEXT_MSG}")
                    continue
                try:
                    from ..kicad_parser.schematic import Schematic  # lazy import

                    sch = Schematic.load(sch_path)
                    lines.append(f"- `{sch_path.name}` — {len(sch.symbols)} symbol(s)")
                    for sym in sch.symbols:
                        bom_rows.append(
                            f"  - {sym.reference}: {sym.value}"
                            + (f" [{sym.footprint}]" if sym.footprint else "")
                        )
                except Exception as exc:  # noqa: BLE001
                    logger.debug("Failed to parse schematic %s: %s", sch_path, exc)
                    lines.append(f"- `{sch_path.name}` (parse error)")
            if bom_rows:
                lines.append("\n**Component list:**")
                lines.extend(bom_rows)

        # --- Library paths ---
        try:
            from ..kicad_parser.library import LibraryDiscovery  # lazy import

            disc = LibraryDiscovery(project_dir)
            sym_libs = disc.list_symbol_libraries()
            fp_libs = disc.list_footprint_libraries()
            if sym_libs:
                lines.append(
                    f"\n**Symbol libraries ({len(sym_libs)}):** "
                    + ", ".join(e.nickname for e in sym_libs[:10])
                    + ("…" if len(sym_libs) > 10 else "")
                )
            if fp_libs:
                lines.append(
                    f"**Footprint libraries ({len(fp_libs)}):** "
                    + ", ".join(e.nickname for e in fp_libs[:10])
                    + ("…" if len(fp_libs) > 10 else "")
                )
        except Exception as exc:  # noqa: BLE001
            logger.debug("Library discovery failed for %s: %s", project_dir, exc)
            pass

        # --- Design-rule files ---
        dru_files = list(project_dir.rglob("*.kicad_dru"))
        if dru_files:
            lines.append(
                f"\n**Design-rule files:** "
                + ", ".join(f.name for f in dru_files)
            )

        # --- KIASSIST.md project memory ---
        memory = ProjectMemory(project_dir)
        memory_content = memory.read()
        if memory_content:
            lines.append(f"\n## Project Memory (KIASSIST.md)\n\n{memory_content.strip()}")

        return "\n".join(lines)
