"""Project memory: read and write KIASSIST.md.

The KIASSIST.md file (analogous to Claude Code's CLAUDE.md) lives in the
project root and stores project-specific context that persists across sessions:
design decisions, preferred components, naming conventions, constraints, etc.

The AI can read this file at the start of a session to prime its context, and
write to it whenever the user makes a design decision worth remembering.

Example::

    memory = ProjectMemory("/path/to/project")

    if not memory.exists():
        memory.write("# KiAssist Project Memory\\n\\nNo notes yet.\\n")

    print(memory.read())

    memory.append_section("Component Preferences", "Use 100nF 0402 decoupling caps.")
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional


class ProjectMemory:
    """Read and write the KIASSIST.md project memory file.

    Args:
        project_path: Path to the ``.kicad_pro`` project file or to the
                      project directory.  The memory file is always placed
                      directly in the project root directory.
    """

    FILENAME = "KIASSIST.md"

    def __init__(self, project_path: str | Path) -> None:
        p = Path(project_path)
        self._project_dir: Path = p.parent if p.is_file() else p
        self._memory_path: Path = self._project_dir / self.FILENAME

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def path(self) -> Path:
        """Absolute path to the KIASSIST.md file."""
        return self._memory_path

    @property
    def project_dir(self) -> Path:
        """Absolute path to the project directory."""
        return self._project_dir

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def exists(self) -> bool:
        """Return ``True`` if the KIASSIST.md file exists."""
        return self._memory_path.exists()

    def read(self) -> Optional[str]:
        """Read and return the full content of KIASSIST.md.

        Returns:
            String content, or ``None`` if the file does not exist.
        """
        if not self._memory_path.exists():
            return None
        return self._memory_path.read_text(encoding="utf-8")

    def write(self, content: str) -> None:
        """Overwrite KIASSIST.md with *content*.

        The project directory is created if it does not exist.

        Args:
            content: Full Markdown text to write.
        """
        self._project_dir.mkdir(parents=True, exist_ok=True)
        # Atomic write: write to a temp file then rename
        tmp_path = self._memory_path.with_suffix(".md.tmp")
        tmp_path.write_text(content, encoding="utf-8")
        os.replace(tmp_path, self._memory_path)

    def append_section(self, heading: str, content: str) -> None:
        """Append a new Markdown section to the memory file.

        If the file does not exist it is created with just the new section.

        Args:
            heading: Section heading text (without ``##`` prefix — that is
                     added automatically).
            content: Body text of the section.
        """
        new_section = f"\n## {heading}\n\n{content.rstrip()}\n"
        existing = self.read() or ""
        self.write(existing + new_section)

    def clear(self) -> None:
        """Delete the KIASSIST.md file if it exists."""
        if self._memory_path.exists():
            self._memory_path.unlink()
