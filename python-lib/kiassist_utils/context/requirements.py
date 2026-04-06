"""Structured context and requirements lifecycle management for PCB development.

Provides a stateful entity (:class:`ProjectRequirements`) and a manager
(:class:`RequirementsManager`) that tracks the complete lifecycle of PCB
project context and user-defined requirements.

Lifecycle states (:class:`ContextState`)::

    GETTING_RAW_CONTEXT       — Parsing PCB files, extracting netlists/symbols
    GENERATING_REQUIREMENTS   — Converting raw context to structured context
    QUERYING_USER             — Asking for missing/ambiguous information
    REFINING_USER_RESPONSES   — Incorporating user answers, validating inputs
    UP_TO_DATE                — Context + requirements complete and approved
    PCB_CHANGED               — PCB file(s) changed; context is stale
    UPDATING_CONTEXT          — Re-running raw context, diffing and merging

Valid transitions (the manager enforces these)::

    GETTING_RAW_CONTEXT   → GENERATING_REQUIREMENTS
    GENERATING_REQUIREMENTS → QUERYING_USER | UP_TO_DATE
    QUERYING_USER         → REFINING_USER_RESPONSES
    REFINING_USER_RESPONSES → UP_TO_DATE | QUERYING_USER
    UP_TO_DATE            → PCB_CHANGED
    PCB_CHANGED           → UPDATING_CONTEXT
    UPDATING_CONTEXT      → GENERATING_REQUIREMENTS | QUERYING_USER | UP_TO_DATE

Example::

    manager = RequirementsManager("/path/to/project")
    req = manager.load_or_create()

    req = manager.start_raw_context_generation()
    manager.set_raw_context(req, raw_context_string)
    manager.set_auto_context(req, synthesized_context, pending_questions=[])
    manager.mark_up_to_date(req)

    # Later — check for PCB changes
    if manager.detect_and_handle_pcb_change(req):
        # state is now PCB_CHANGED
        manager.start_context_update(req)
        manager.set_raw_context(req, new_raw_context)
        manager.set_auto_context(req, new_context, pending_questions=["Q?"])
        # state is now QUERYING_USER
        manager.submit_user_answers(req, approved_requirements)
        manager.mark_up_to_date(req)
"""

from __future__ import annotations

import difflib
import hashlib
import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# State enum
# ---------------------------------------------------------------------------


class ContextState(str, Enum):
    """Lifecycle state of the project context / requirements object.

    Using ``str`` as a mixin makes the enum JSON-serialisable without a
    custom encoder.
    """

    GETTING_RAW_CONTEXT = "getting_raw_context"
    GENERATING_REQUIREMENTS = "generating_requirements"
    QUERYING_USER = "querying_user"
    REFINING_USER_RESPONSES = "refining_user_responses"
    UP_TO_DATE = "up_to_date"
    PCB_CHANGED = "pcb_changed"
    UPDATING_CONTEXT = "updating_context"


# Allowed state transitions.  Keys are current state; values are the set of
# states that may be reached from that state.
_VALID_TRANSITIONS: Dict[ContextState, frozenset[ContextState]] = {
    ContextState.GETTING_RAW_CONTEXT: frozenset(
        {ContextState.GENERATING_REQUIREMENTS}
    ),
    ContextState.GENERATING_REQUIREMENTS: frozenset(
        {ContextState.QUERYING_USER, ContextState.UP_TO_DATE}
    ),
    ContextState.QUERYING_USER: frozenset(
        {ContextState.REFINING_USER_RESPONSES}
    ),
    ContextState.REFINING_USER_RESPONSES: frozenset(
        {ContextState.UP_TO_DATE, ContextState.QUERYING_USER}
    ),
    ContextState.UP_TO_DATE: frozenset(
        {ContextState.PCB_CHANGED}
    ),
    ContextState.PCB_CHANGED: frozenset(
        {ContextState.UPDATING_CONTEXT}
    ),
    ContextState.UPDATING_CONTEXT: frozenset(
        {ContextState.GENERATING_REQUIREMENTS, ContextState.QUERYING_USER, ContextState.UP_TO_DATE}
    ),
}


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class StateTransition:
    """Record of a single state transition."""

    from_state: str
    to_state: str
    timestamp: str
    reason: str = ""

    def to_dict(self) -> Dict:
        return {
            "from_state": self.from_state,
            "to_state": self.to_state,
            "timestamp": self.timestamp,
            "reason": self.reason,
        }

    @classmethod
    def from_dict(cls, d: Dict) -> "StateTransition":
        return cls(
            from_state=d["from_state"],
            to_state=d["to_state"],
            timestamp=d["timestamp"],
            reason=d.get("reason", ""),
        )


@dataclass
class ProjectRequirements:
    """Stateful container for project context and user-defined requirements.

    Attributes:
        state:               Current lifecycle state.
        user_requirements:   User-defined constraints.  Immutable unless the
                             user explicitly approves a change.
        auto_context:        Auto-generated context derived from PCB files.
        raw_context:         Raw extraction from PCB/schematic files (pre-LLM).
        pending_questions:   Questions that need user answers before the
                             context can be marked up-to-date.
        pcb_file_hashes:     SHA-256 hash of each tracked KiCad file, keyed
                             by file path relative to the project directory.
        context_diff:        Diff of context changes on a PCB update (if any).
        state_history:       Ordered log of all state transitions.
        created_at:          ISO-8601 timestamp of first creation.
        updated_at:          ISO-8601 timestamp of most recent modification.
    """

    state: ContextState = ContextState.GETTING_RAW_CONTEXT
    user_requirements: str = ""
    auto_context: str = ""
    raw_context: str = ""
    pending_questions: List[str] = field(default_factory=list)
    pcb_file_hashes: Dict[str, str] = field(default_factory=dict)
    context_diff: str = ""
    state_history: List[StateTransition] = field(default_factory=list)
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    updated_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> Dict:
        """Serialise to a plain dict suitable for JSON storage."""
        return {
            "state": self.state.value,
            "user_requirements": self.user_requirements,
            "auto_context": self.auto_context,
            "raw_context": self.raw_context,
            "pending_questions": list(self.pending_questions),
            "pcb_file_hashes": dict(self.pcb_file_hashes),
            "context_diff": self.context_diff,
            "state_history": [t.to_dict() for t in self.state_history],
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, d: Dict) -> "ProjectRequirements":
        """Deserialise from a plain dict (as stored in JSON)."""
        history = [StateTransition.from_dict(t) for t in d.get("state_history", [])]
        return cls(
            state=ContextState(d.get("state", ContextState.GETTING_RAW_CONTEXT)),
            user_requirements=d.get("user_requirements", ""),
            auto_context=d.get("auto_context", ""),
            raw_context=d.get("raw_context", ""),
            pending_questions=list(d.get("pending_questions", [])),
            pcb_file_hashes=dict(d.get("pcb_file_hashes", {})),
            context_diff=d.get("context_diff", ""),
            state_history=history,
            created_at=d.get("created_at", datetime.now(timezone.utc).isoformat()),
            updated_at=d.get("updated_at", datetime.now(timezone.utc).isoformat()),
        )

    # ------------------------------------------------------------------
    # Convenience queries
    # ------------------------------------------------------------------

    @property
    def is_stable(self) -> bool:
        """Return ``True`` when context is safe to use in downstream prompts."""
        return self.state == ContextState.UP_TO_DATE

    @property
    def needs_user_input(self) -> bool:
        """Return ``True`` when user input is required to proceed."""
        return self.state in (
            ContextState.QUERYING_USER,
            ContextState.REFINING_USER_RESPONSES,
        )

    @property
    def is_stale(self) -> bool:
        """Return ``True`` when the context is known to be outdated."""
        return self.state in (
            ContextState.PCB_CHANGED,
            ContextState.UPDATING_CONTEXT,
        )


# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------


class RequirementsManager:
    """Manage the lifecycle of :class:`ProjectRequirements` for a KiCad project.

    The manager is responsible for:

    * Loading and persisting the :class:`ProjectRequirements` JSON state file.
    * Enforcing valid state transitions.
    * Computing SHA-256 file hashes for KiCad source files.
    * Detecting PCB/schematic changes and marking context as stale.
    * Generating human-readable diffs of context changes.
    * Protecting user requirements from silent auto-modification.

    Args:
        project_path: Path to the ``.kicad_pro`` file or project directory.
    """

    KIASSIST_DIR = ".kiassist"
    STATE_FILE = "requirements_state.json"

    #: Extensions of KiCad source files that are monitored for changes.
    TRACKED_EXTENSIONS = frozenset(
        {".kicad_sch", ".kicad_pcb", ".kicad_pro", ".kicad_sym"}
    )

    def __init__(self, project_path: str | Path) -> None:
        p = Path(project_path)
        self._project_dir: Path = p.parent if p.is_file() else p
        self._state_dir: Path = self._project_dir / self.KIASSIST_DIR
        self._state_path: Path = self._state_dir / self.STATE_FILE

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def project_dir(self) -> Path:
        """Absolute path to the project directory."""
        return self._project_dir

    @property
    def state_path(self) -> Path:
        """Absolute path to the persisted JSON state file."""
        return self._state_path

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def load_or_create(self) -> ProjectRequirements:
        """Load existing requirements state or create a fresh one.

        Returns:
            The current :class:`ProjectRequirements` for this project.
        """
        if self._state_path.exists():
            try:
                data = json.loads(self._state_path.read_text(encoding="utf-8"))
                return ProjectRequirements.from_dict(data)
            except Exception as exc:
                logger.warning(
                    "Could not load requirements state from %s (%s); "
                    "starting fresh.",
                    self._state_path,
                    exc,
                )
        return ProjectRequirements()

    def save(self, req: ProjectRequirements) -> None:
        """Atomically persist *req* to disk.

        Uses write-to-temp-then-rename so the file is never left in a
        partial state.

        Args:
            req: The :class:`ProjectRequirements` to persist.
        """
        self._state_dir.mkdir(parents=True, exist_ok=True)
        req.updated_at = datetime.now(timezone.utc).isoformat()
        tmp_path = self._state_path.with_suffix(".json.tmp")
        tmp_path.write_text(
            json.dumps(req.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        os.replace(tmp_path, self._state_path)

    # ------------------------------------------------------------------
    # State transitions
    # ------------------------------------------------------------------

    def transition(
        self,
        req: ProjectRequirements,
        new_state: ContextState,
        reason: str = "",
    ) -> None:
        """Transition *req* to *new_state*, enforcing the allowed graph.

        The transition is recorded in :attr:`ProjectRequirements.state_history`
        and the object is saved to disk.

        Args:
            req:       The requirements object to update.
            new_state: The target state.
            reason:    Human-readable explanation for the transition.

        Raises:
            ValueError: If *new_state* is not reachable from the current state.
        """
        allowed = _VALID_TRANSITIONS.get(req.state, frozenset())
        if new_state not in allowed:
            raise ValueError(
                f"Cannot transition from {req.state!r} to {new_state!r}. "
                f"Allowed: {sorted(s.value for s in allowed)}"
            )
        transition = StateTransition(
            from_state=req.state.value,
            to_state=new_state.value,
            timestamp=datetime.now(timezone.utc).isoformat(),
            reason=reason,
        )
        req.state_history.append(transition)
        req.state = new_state
        self.save(req)
        logger.debug(
            "Requirements state: %s → %s%s",
            transition.from_state,
            transition.to_state,
            f" ({reason})" if reason else "",
        )

    # ------------------------------------------------------------------
    # File hashing & change detection
    # ------------------------------------------------------------------

    def compute_file_hashes(self) -> Dict[str, str]:
        """Compute SHA-256 hashes for all tracked KiCad files.

        Only files with extensions in :attr:`TRACKED_EXTENSIONS` are included.
        Paths are stored as POSIX strings relative to the project directory.

        Returns:
            Mapping of ``{relative_posix_path: sha256_hex}``.
        """
        hashes: Dict[str, str] = {}
        for ext in self.TRACKED_EXTENSIONS:
            for file_path in sorted(self._project_dir.rglob(f"*{ext}")):
                try:
                    sha256 = hashlib.sha256(
                        file_path.read_bytes()
                    ).hexdigest()
                    rel = file_path.relative_to(self._project_dir).as_posix()
                    hashes[rel] = sha256
                except OSError as exc:
                    logger.debug(
                        "Could not hash %s: %s", file_path, exc
                    )
        return hashes

    def check_for_pcb_changes(self, req: ProjectRequirements) -> bool:
        """Return ``True`` if any tracked KiCad file has changed.

        Compares current file hashes against the hashes recorded in *req*.
        Returns ``False`` when no hashes have been recorded yet (i.e., initial
        state).

        Args:
            req: The current requirements state.

        Returns:
            ``True`` if at least one file was added, removed, or modified.
        """
        if not req.pcb_file_hashes:
            return False
        current = self.compute_file_hashes()
        return current != req.pcb_file_hashes

    def detect_and_handle_pcb_change(self, req: ProjectRequirements) -> bool:
        """Check for PCB changes and, if found, transition to :attr:`~ContextState.PCB_CHANGED`.

        This method is safe to call only when *req* is in
        :attr:`~ContextState.UP_TO_DATE` state.  If any tracked file has
        changed, it records the new hashes for diff purposes and transitions
        the state.

        Args:
            req: The current requirements state (must be ``UP_TO_DATE``).

        Returns:
            ``True`` if a change was detected and the state was updated.
        """
        if req.state != ContextState.UP_TO_DATE:
            return False
        if not self.check_for_pcb_changes(req):
            return False
        self.transition(
            req,
            ContextState.PCB_CHANGED,
            reason="Tracked KiCad file hash changed",
        )
        return True

    # ------------------------------------------------------------------
    # Diff generation
    # ------------------------------------------------------------------

    def generate_diff(self, old_context: str, new_context: str) -> str:
        """Generate a human-readable unified diff of two context strings.

        Args:
            old_context: The previous auto-generated context text.
            new_context: The updated auto-generated context text.

        Returns:
            A unified diff string, or an empty string when there are no
            differences.
        """
        old_lines = old_context.splitlines(keepends=True)
        new_lines = new_context.splitlines(keepends=True)
        diff_lines = list(
            difflib.unified_diff(
                old_lines,
                new_lines,
                fromfile="previous_context",
                tofile="updated_context",
            )
        )
        return "".join(diff_lines)

    # ------------------------------------------------------------------
    # High-level workflow helpers
    # ------------------------------------------------------------------

    def start_raw_context_generation(self) -> ProjectRequirements:
        """Begin a fresh context lifecycle.

        Creates (or resets) the :class:`ProjectRequirements` and transitions
        to :attr:`~ContextState.GETTING_RAW_CONTEXT`.  The existing state file
        is overwritten.

        Returns:
            A new :class:`ProjectRequirements` in ``GETTING_RAW_CONTEXT``
            state.
        """
        req = ProjectRequirements(state=ContextState.GETTING_RAW_CONTEXT)
        self.save(req)
        logger.debug("Started new raw context generation for %s", self._project_dir)
        return req

    def set_raw_context(
        self, req: ProjectRequirements, raw_context: str
    ) -> None:
        """Store *raw_context* and advance to :attr:`~ContextState.GENERATING_REQUIREMENTS`.

        Also snapshots the current file hashes so that future change
        detection has a baseline.

        Args:
            req:         The requirements object (must be in
                         ``GETTING_RAW_CONTEXT`` or ``UPDATING_CONTEXT``).
            raw_context: The raw extraction text produced by the context
                         generator.
        """
        if req.state not in (
            ContextState.GETTING_RAW_CONTEXT,
            ContextState.UPDATING_CONTEXT,
        ):
            raise ValueError(
                f"set_raw_context requires state GETTING_RAW_CONTEXT or "
                f"UPDATING_CONTEXT, got {req.state!r}"
            )
        req.raw_context = raw_context
        req.pcb_file_hashes = self.compute_file_hashes()
        self.transition(
            req,
            ContextState.GENERATING_REQUIREMENTS,
            reason="Raw context extracted from KiCad files",
        )

    def set_auto_context(
        self,
        req: ProjectRequirements,
        auto_context: str,
        pending_questions: Optional[List[str]] = None,
    ) -> None:
        """Store the synthesized *auto_context* and advance state.

        If *pending_questions* is non-empty the system transitions to
        :attr:`~ContextState.QUERYING_USER`; otherwise directly to
        :attr:`~ContextState.UP_TO_DATE`.

        When there is a previous context (PCB update path) a diff is
        computed and stored on *req*.

        Args:
            req:               The requirements object (must be in
                               ``GENERATING_REQUIREMENTS``).
            auto_context:      The synthesized/structured context text.
            pending_questions: Questions that the user must answer.  Pass
                               ``None`` or an empty list when no user input
                               is needed.
        """
        if req.state != ContextState.GENERATING_REQUIREMENTS:
            raise ValueError(
                f"set_auto_context requires GENERATING_REQUIREMENTS state, "
                f"got {req.state!r}"
            )
        # Compute diff if we already have a previous context
        if req.auto_context:
            req.context_diff = self.generate_diff(req.auto_context, auto_context)
        req.auto_context = auto_context

        questions = list(pending_questions or [])
        req.pending_questions = questions

        if questions:
            self.transition(
                req,
                ContextState.QUERYING_USER,
                reason=f"{len(questions)} question(s) require user input",
            )
        else:
            self.transition(
                req,
                ContextState.UP_TO_DATE,
                reason="Auto-context generated; no user input required",
            )

    def submit_user_answers(
        self,
        req: ProjectRequirements,
        user_requirements: str,
    ) -> None:
        """Record user-supplied requirements and advance to
        :attr:`~ContextState.REFINING_USER_RESPONSES`.

        User requirements are **never** silently overwritten — this is the
        only method that modifies :attr:`~ProjectRequirements.user_requirements`,
        and it requires the state to be ``QUERYING_USER``.

        Args:
            req:               The requirements object (must be in
                               ``QUERYING_USER``).
            user_requirements: The user-approved requirements text.
        """
        if req.state != ContextState.QUERYING_USER:
            raise ValueError(
                f"submit_user_answers requires QUERYING_USER state, "
                f"got {req.state!r}"
            )
        req.user_requirements = user_requirements
        req.pending_questions = []
        self.transition(
            req,
            ContextState.REFINING_USER_RESPONSES,
            reason="User answers received and stored",
        )

    def mark_up_to_date(self, req: ProjectRequirements) -> None:
        """Mark context + requirements as complete and approved.

        Allowed from :attr:`~ContextState.REFINING_USER_RESPONSES` or
        :attr:`~ContextState.GENERATING_REQUIREMENTS` (when no questions
        are needed).

        Args:
            req: The requirements object.
        """
        allowed_sources = {
            ContextState.REFINING_USER_RESPONSES,
            ContextState.GENERATING_REQUIREMENTS,
        }
        if req.state not in allowed_sources:
            raise ValueError(
                f"mark_up_to_date requires one of "
                f"{sorted(s.value for s in allowed_sources)}, "
                f"got {req.state!r}"
            )
        self.transition(
            req,
            ContextState.UP_TO_DATE,
            reason="Context and requirements approved",
        )

    def start_context_update(self, req: ProjectRequirements) -> None:
        """Begin re-generating context after a PCB change.

        Transitions from :attr:`~ContextState.PCB_CHANGED` to
        :attr:`~ContextState.UPDATING_CONTEXT`.

        Args:
            req: The requirements object (must be in ``PCB_CHANGED``).
        """
        if req.state != ContextState.PCB_CHANGED:
            raise ValueError(
                f"start_context_update requires PCB_CHANGED state, "
                f"got {req.state!r}"
            )
        self.transition(
            req,
            ContextState.UPDATING_CONTEXT,
            reason="Re-running raw context generator after PCB change",
        )
