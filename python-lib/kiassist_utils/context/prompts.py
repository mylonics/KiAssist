"""Multi-layer system prompt construction (Claude Code pattern).

:class:`SystemPromptBuilder` assembles a system prompt from up to four layers:

1. **Base layer** — Minimal KiAssist identity, response format, safety rules, and
   project-memory guidance.  Loaded from ``public/agents/kicad-assistant.md``
   (resolved via the ``KIASSIST_BASE_PROMPT`` env var or auto-discovery).

2. **Focused agent layer** *(optional)* — Per-command specialisation loaded from one
   of the focused agent files in ``public/agents/``:

   * ``schematic-agent.md``      — schematic editing tools and design guidelines
   * ``symbol-library-agent.md`` — symbol library tools and symbol creation guidelines
   * ``footprint-agent.md``      — footprint tools and footprint design guidelines
   * ``pcb-agent.md``            — PCB layout tools and routing guidelines

   Pass the agent name (without ``.md``) or a full path as *focused_agent* when
   constructing :class:`SystemPromptBuilder` or call :meth:`build` with the same
   argument to override per-call.

3. **Project context layer** — Auto-injected from project files:
   schematic component summary, library paths, design rules.  Also includes
   KIASSIST.md project memory if present.  Cached per session; refreshed on
   project switch.

4. **Dynamic context layer** — Caller-supplied runtime state: which KiCad
   editors are open, active schematic sheet, selected project path.

Example::

    # General assistant (no focused agent):
    builder = SystemPromptBuilder()
    prompt = builder.build(project_path="/project/my_board")

    # Schematic-focused session:
    builder = SystemPromptBuilder(focused_agent="schematic-agent")
    prompt = builder.build(
        project_path="/project/my_board",
        dynamic_context="Active schematic: top.kicad_sch",
    )

    # Override focused agent per-call:
    prompt = builder.build(
        project_path="/project/my_board",
        focused_agent="pcb-agent",
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


def _find_agents_dir() -> Optional[Path]:
    """Return the ``public/agents/`` directory by searching upwards, or ``None``."""
    candidate = _THIS_FILE
    for _ in range(10):
        candidate = candidate.parent
        agents_dir = candidate / "public" / "agents"
        if agents_dir.is_dir():
            return agents_dir
    return None


# Message used when a schematic is skipped because the AI already has current content.
_ALREADY_IN_CONTEXT_MSG = "already in context (unchanged)"

# Header used for the project-context section (used for truncation detection).
_PROJECT_CONTEXT_HEADER = "## Project Context"


class SystemPromptBuilder:
    """Builds the multi-layer KiAssist system prompt.

    Args:
        base_prompt_path: Explicit path to the base prompt Markdown file.
                          If ``None``, the builder first checks the
                          ``KIASSIST_BASE_PROMPT`` environment variable and
                          then searches upward for
                          ``public/agents/kicad-assistant.md``.
        focused_agent:    Name of a focused agent (e.g. ``"schematic-agent"``,
                          ``"pcb-agent"``) or an explicit :class:`~pathlib.Path`
                          to a Markdown file.  When set, the file is injected
                          as Layer 2 (between the base and the project context),
                          narrowing the assistant's tool awareness and guidelines
                          to the relevant domain.  ``None`` keeps the general
                          assistant behaviour.
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
        focused_agent: Optional[str | Path] = None,
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

        self._focused_agent: Optional[str | Path] = focused_agent
        self._cache_project_context = cache_project_context
        self._file_cache = file_cache
        # Maps project_dir → cached project-context string
        self._project_cache: Dict[str, str] = {}

    # ------------------------------------------------------------------
    # Class helpers
    # ------------------------------------------------------------------

    @classmethod
    def list_focused_agents(cls) -> List[str]:
        """Return the names of available focused agents (without ``.md``).

        Looks inside the ``public/agents/`` directory for Markdown files
        whose names end with ``-agent.md``.  Returns an empty list when
        the directory cannot be found.
        """
        agents_dir = _find_agents_dir()
        if agents_dir is None:
            return []
        return sorted(
            p.stem
            for p in agents_dir.glob("*-agent.md")
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    # Maximum character budget for the assembled system prompt.
    # Token-to-char ratio varies by content; KiCad context (paths,
    # component refs, footprints) tokenizes at ~1.7 chars/token.
    # Use 2 chars/token as a conservative estimate.
    # Gemma 4 models support 32768-token context windows.  We
    # allocate 55000 chars (~27500 tokens) for the system prompt,
    # leaving headroom for conversation history and the response.
    _MAX_PROMPT_CHARS = 55_000

    def build(
        self,
        project_path: Optional[str | Path] = None,
        dynamic_context: Optional[str] = None,
        focused_agent: Optional[str | Path] = None,
    ) -> str:
        """Assemble and return the complete system prompt.

        Args:
            project_path:    Path to the ``.kicad_pro`` file or project
                             directory.  If ``None`` only the base layer is
                             included.
            dynamic_context: Caller-supplied runtime state string appended as
                             the last layer.
            focused_agent:   Override the instance-level *focused_agent* for
                             this call only.  Pass an agent name (e.g.
                             ``"schematic-agent"``) or an explicit
                             :class:`~pathlib.Path`.  ``None`` falls back to
                             the value supplied at construction time.

        Returns:
            Complete system prompt as a single string.
        """
        sections: List[str] = []

        # --- Layer 1: Base prompt ---
        base = self._load_base_prompt()
        if base:
            sections.append(base.strip())

        # --- Layer 2: Focused agent (optional) ---
        agent_override = focused_agent if focused_agent is not None else self._focused_agent
        focused_text = self._load_focused_agent(agent_override)
        if focused_text:
            sections.append(focused_text.strip())

        # --- Layer 3: Project context ---
        if project_path is not None:
            project_ctx = self._get_project_context(project_path)
            if project_ctx:
                sections.append(project_ctx.strip())

        # --- Layer 4: Dynamic context ---
        if dynamic_context:
            sections.append(
                f"## Current Session Context\n\n{dynamic_context.strip()}"
            )

        prompt = "\n\n---\n\n".join(sections)

        # Guard: truncate the project-context layer when the assembled
        # prompt would overflow the character budget.
        if len(prompt) > self._MAX_PROMPT_CHARS and len(sections) > 1:
            original_len = len(prompt)
            budget = self._MAX_PROMPT_CHARS
            # Keep base, focused-agent, and dynamic layers in full; shrink project context.
            # Project context is always the second-to-last section when present.
            project_ctx_idx = None
            for i, sec in enumerate(sections):
                if sec.startswith(_PROJECT_CONTEXT_HEADER):
                    project_ctx_idx = i
                    break
            if project_ctx_idx is not None:
                other_lens = sum(len(s) for i, s in enumerate(sections) if i != project_ctx_idx)
                separator_overhead = (len(sections) - 1) * len("\n\n---\n\n")
                available = budget - other_lens - separator_overhead
                if available > 200:
                    truncated = sections[project_ctx_idx][:available].rsplit("\n", 1)[0]
                    truncated += "\n\n*(project context truncated to fit context window)*"
                    sections[project_ctx_idx] = truncated
                    prompt = "\n\n---\n\n".join(sections)
                    logger.warning(
                        "System prompt truncated from %d to %d chars to fit context window.",
                        original_len,
                        len(prompt),
                    )

        return prompt

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

    def _load_focused_agent(self, agent: Optional[str | Path]) -> str:
        """Return the focused-agent prompt text, or an empty string.

        *agent* may be:

        * ``None`` — no focused agent; returns ``""``
        * A :class:`~pathlib.Path` — read from that exact path.
        * A ``str`` that ends in ``.md`` — treated as a literal file path.
        * A bare name string **without** a ``.md`` suffix (e.g.
          ``"schematic-agent"``) — resolved to
          ``public/agents/<name>.md`` in the repo tree.

        Note: bare names must not include the ``.md`` extension; pass a
        :class:`~pathlib.Path` or a string ending in ``.md`` when an
        explicit path is intended.
        """
        if agent is None:
            return ""
        if isinstance(agent, Path):
            # Explicit Path: read from that exact location, never name-resolve.
            try:
                return agent.read_text(encoding="utf-8")
            except OSError:
                logger.warning("Focused agent file not found: %s", agent)
                return ""
        # String branch: distinguish bare name from explicit file path.
        if agent.endswith(".md"):
            path = Path(agent)
        else:
            # Bare name: resolve via the agents directory.
            agents_dir = _find_agents_dir()
            if agents_dir is None:
                logger.warning("focused_agent %r requested but public/agents/ not found.", agent)
                return ""
            path = agents_dir / f"{agent}.md"
        try:
            return path.read_text(encoding="utf-8")
        except OSError:
            logger.warning("Focused agent file not found: %s", path)
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

        lines: List[str] = [_PROJECT_CONTEXT_HEADER]

        lines.append(f"\n**Project directory:** `{project_dir}`")

        # --- Rich project context (hierarchy, BOM, netlist) ---
        try:
            from .project_context import get_raw_context
            rich_context = get_raw_context(project_dir)
            if rich_context:
                lines.append("\n" + rich_context)
        except Exception as exc:  # noqa: BLE001
            logger.debug("Rich project context failed, falling back: %s", exc)
            # Fallback: basic schematic component summary
            schematics = sorted(project_dir.rglob("*.kicad_sch"))
            if schematics:
                lines.append(f"\n**Schematics ({len(schematics)}):**")
                bom_rows: List[str] = []
                for sch_path in schematics:
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
                    except Exception as exc2:  # noqa: BLE001
                        logger.debug("Failed to parse schematic %s: %s", sch_path, exc2)
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
