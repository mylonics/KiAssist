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
    # state is now UP_TO_DATE (no questions → set_auto_context transitions directly)

    # Later — check for PCB changes
    if manager.detect_and_handle_pcb_change(req):
        # state is now PCB_CHANGED
        # req.file_changes     — which files were added/modified/removed
        # req.component_changes — which components were added/removed/modified
        # Pass both summaries alongside req.auto_context to the context generator
        # so it can produce a targeted update describing only what changed.
        change_summary = (
            manager.format_file_change_list(req.file_changes)
            + "\n\n"
            + manager.format_component_changes(req.component_changes)
        )
        manager.start_context_update(req)
        manager.set_raw_context(req, new_raw_context)  # also snapshots component list
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
class FileChange:
    """Record of a single file-level change detected between two snapshots.

    Attributes:
        path:       POSIX path relative to the project directory.
        change_type: ``"added"``, ``"modified"``, or ``"removed"``.
        old_hash:   SHA-256 digest before the change (``None`` for new files).
        new_hash:   SHA-256 digest after the change (``None`` for removed files).
    """

    path: str
    change_type: str  # "added" | "modified" | "removed"
    old_hash: Optional[str] = None
    new_hash: Optional[str] = None

    def to_dict(self) -> Dict:
        return {
            "path": self.path,
            "change_type": self.change_type,
            "old_hash": self.old_hash,
            "new_hash": self.new_hash,
        }

    @classmethod
    def from_dict(cls, d: Dict) -> "FileChange":
        return cls(
            path=d["path"],
            change_type=d["change_type"],
            old_hash=d.get("old_hash"),
            new_hash=d.get("new_hash"),
        )


@dataclass
class ComponentChange:
    """Record of a single component-level change between two project snapshots.

    Attributes:
        reference:     Component reference designator (e.g., ``"R1"``).
        change_type:   ``"added"``, ``"removed"``, or ``"modified"``.
        old_value:     Component value before the change (``None`` for new
                       components).
        new_value:     Component value after the change (``None`` for removed
                       components).
        old_footprint: Footprint assignment before the change.
        new_footprint: Footprint assignment after the change.
    """

    reference: str
    change_type: str  # "added" | "modified" | "removed"
    old_value: Optional[str] = None
    new_value: Optional[str] = None
    old_footprint: Optional[str] = None
    new_footprint: Optional[str] = None

    def to_dict(self) -> Dict:
        return {
            "reference": self.reference,
            "change_type": self.change_type,
            "old_value": self.old_value,
            "new_value": self.new_value,
            "old_footprint": self.old_footprint,
            "new_footprint": self.new_footprint,
        }

    @classmethod
    def from_dict(cls, d: Dict) -> "ComponentChange":
        return cls(
            reference=d["reference"],
            change_type=d["change_type"],
            old_value=d.get("old_value"),
            new_value=d.get("new_value"),
            old_footprint=d.get("old_footprint"),
            new_footprint=d.get("new_footprint"),
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
        file_changes:        Structured list of file-level changes detected on
                             the most recent PCB change event (added/modified/
                             removed files).  Populated by
                             :meth:`~RequirementsManager.detect_and_handle_pcb_change`
                             and available throughout the
                             ``UPDATING_CONTEXT`` phase so the context generator
                             can produce a targeted delta rather than a full
                             re-parse.
        component_snapshot:  Snapshot of all placed schematic components at the
                             time the last raw context was captured, as a
                             mapping of ``reference → {"value": …, "footprint": …}``.
                             Populated by :meth:`~RequirementsManager.set_raw_context`.
        component_changes:   Semantic list of component-level changes (added/
                             removed/modified individual components) detected on
                             the most recent PCB change event.  Populated
                             alongside :attr:`file_changes` by
                             :meth:`~RequirementsManager.detect_and_handle_pcb_change`.
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
    file_changes: List[FileChange] = field(default_factory=list)
    component_snapshot: Dict[str, Dict[str, str]] = field(default_factory=dict)
    component_changes: List[ComponentChange] = field(default_factory=list)
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
            "file_changes": [fc.to_dict() for fc in self.file_changes],
            "component_snapshot": {
                ref: dict(info) for ref, info in self.component_snapshot.items()
            },
            "component_changes": [cc.to_dict() for cc in self.component_changes],
            "state_history": [t.to_dict() for t in self.state_history],
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, d: Dict) -> "ProjectRequirements":
        """Deserialise from a plain dict (as stored in JSON)."""
        history = [StateTransition.from_dict(t) for t in d.get("state_history", [])]
        file_changes = [FileChange.from_dict(fc) for fc in d.get("file_changes", [])]
        component_changes = [
            ComponentChange.from_dict(cc) for cc in d.get("component_changes", [])
        ]
        return cls(
            state=ContextState(d.get("state", ContextState.GETTING_RAW_CONTEXT)),
            user_requirements=d.get("user_requirements", ""),
            auto_context=d.get("auto_context", ""),
            raw_context=d.get("raw_context", ""),
            pending_questions=list(d.get("pending_questions", [])),
            pcb_file_hashes=dict(d.get("pcb_file_hashes", {})),
            context_diff=d.get("context_diff", ""),
            file_changes=file_changes,
            component_snapshot=dict(d.get("component_snapshot", {})),
            component_changes=component_changes,
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
        is_project_file = p.suffix == ".kicad_pro" or (p.exists() and p.is_file())
        self._project_dir: Path = p.parent if is_project_file else p
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

    def compute_file_change_list(
        self,
        old_hashes: Dict[str, str],
        new_hashes: Dict[str, str],
    ) -> List[FileChange]:
        """Compare two hash snapshots and return a list of :class:`FileChange` records.

        Each entry describes one file that was added, modified, or removed
        between the *old_hashes* and *new_hashes* snapshots.  Files that are
        identical in both snapshots are omitted.

        Args:
            old_hashes: Hash snapshot taken before the change (from
                        :attr:`~ProjectRequirements.pcb_file_hashes`).
            new_hashes: Current hash snapshot produced by
                        :meth:`compute_file_hashes`.

        Returns:
            Ordered list of :class:`FileChange` records (sorted by path).
        """
        changes: List[FileChange] = []
        all_paths = sorted(set(old_hashes) | set(new_hashes))
        for path in all_paths:
            old_h = old_hashes.get(path)
            new_h = new_hashes.get(path)
            if old_h is None:
                changes.append(
                    FileChange(path=path, change_type="added", new_hash=new_h)
                )
            elif new_h is None:
                changes.append(
                    FileChange(path=path, change_type="removed", old_hash=old_h)
                )
            elif old_h != new_h:
                changes.append(
                    FileChange(
                        path=path,
                        change_type="modified",
                        old_hash=old_h,
                        new_hash=new_h,
                    )
                )
        return changes

    def format_file_change_list(self, file_changes: List[FileChange]) -> str:
        """Format *file_changes* as a human-readable Markdown summary.

        The resulting string is intended to be passed alongside the previous
        :attr:`~ProjectRequirements.auto_context` to the context generator so
        it can produce a targeted update describing only what changed rather
        than a full re-parse.

        Args:
            file_changes: List of :class:`FileChange` records (typically
                          ``req.file_changes`` after a PCB-change event).

        Returns:
            A Markdown-formatted change summary, or an empty string when
            *file_changes* is empty.
        """
        if not file_changes:
            return ""
        lines: List[str] = ["## PCB File Changes\n"]
        for fc in file_changes:
            if fc.change_type == "added":
                lines.append(f"- **Added**: `{fc.path}`")
            elif fc.change_type == "removed":
                lines.append(f"- **Removed**: `{fc.path}`")
            else:
                lines.append(f"- **Modified**: `{fc.path}`")
        return "\n".join(lines)

    def compute_component_snapshot(self) -> Dict[str, Dict[str, str]]:
        """Parse all schematic files and return a component snapshot.

        Each entry maps a component reference to a dict containing its
        ``"value"`` and ``"footprint"`` strings.  Only fully-assigned
        references (i.e. the reference does **not** end with ``"?"``) are
        included; power symbols and other non-component symbols that carry
        a trailing ``"?"`` are excluded.

        The snapshot is used as a baseline for
        :meth:`compute_component_changes` — differences between the stored
        snapshot and a freshly computed one reveal which components were
        added, removed, or modified.

        Returns:
            Mapping of ``{reference: {"value": …, "footprint": …}}``.
        """
        snapshot: Dict[str, Dict[str, str]] = {}
        try:
            from ..kicad_parser.schematic import Schematic  # lazy import
        except ImportError:
            logger.debug(
                "kicad_parser.schematic not available; skipping component snapshot"
            )
            return snapshot

        for sch_path in sorted(self._project_dir.rglob("*.kicad_sch")):
            try:
                sch = Schematic.load(sch_path)
                for sym in sch.symbols:
                    ref = sym.reference
                    if not ref or ref.endswith("?"):
                        continue
                    snapshot[ref] = {
                        "value": sym.value or "",
                        "footprint": sym.footprint or "",
                    }
            except Exception as exc:  # noqa: BLE001
                logger.debug(
                    "Failed to parse schematic %s for component snapshot: %s",
                    sch_path,
                    exc,
                )
        return snapshot

    def compute_component_changes(
        self,
        old_snapshot: Dict[str, Dict[str, str]],
        new_snapshot: Dict[str, Dict[str, str]],
    ) -> List[ComponentChange]:
        """Compare two component snapshots and return a list of changes.

        Each entry describes one component that was added, removed, or had its
        value or footprint modified between *old_snapshot* and *new_snapshot*.
        Unchanged components are omitted.

        Args:
            old_snapshot: Component snapshot from before the change (from
                          :attr:`~ProjectRequirements.component_snapshot`).
            new_snapshot: Current snapshot produced by
                          :meth:`compute_component_snapshot`.

        Returns:
            Ordered list of :class:`ComponentChange` records (sorted by
            reference designator).
        """
        changes: List[ComponentChange] = []
        all_refs = sorted(set(old_snapshot) | set(new_snapshot))
        for ref in all_refs:
            old_info = old_snapshot.get(ref)
            new_info = new_snapshot.get(ref)
            if old_info is None:
                changes.append(
                    ComponentChange(
                        reference=ref,
                        change_type="added",
                        new_value=new_info.get("value", ""),
                        new_footprint=new_info.get("footprint", ""),
                    )
                )
            elif new_info is None:
                changes.append(
                    ComponentChange(
                        reference=ref,
                        change_type="removed",
                        old_value=old_info.get("value", ""),
                        old_footprint=old_info.get("footprint", ""),
                    )
                )
            elif old_info != new_info:
                changes.append(
                    ComponentChange(
                        reference=ref,
                        change_type="modified",
                        old_value=old_info.get("value", ""),
                        new_value=new_info.get("value", ""),
                        old_footprint=old_info.get("footprint", ""),
                        new_footprint=new_info.get("footprint", ""),
                    )
                )
        return changes

    def format_component_changes(
        self, component_changes: List[ComponentChange]
    ) -> str:
        """Format *component_changes* as a human-readable Markdown summary.

        The resulting string is intended to be passed alongside
        :meth:`format_file_change_list` output and the previous
        :attr:`~ProjectRequirements.auto_context` to the context generator so
        it can generate a focused update for individual component changes
        rather than re-analysing the full project.

        Args:
            component_changes: List of :class:`ComponentChange` records
                               (typically ``req.component_changes`` after a
                               PCB-change event).

        Returns:
            A Markdown-formatted component change summary, or an empty string
            when *component_changes* is empty.
        """
        if not component_changes:
            return ""
        lines: List[str] = ["## Component Changes\n"]
        for cc in component_changes:
            if cc.change_type == "added":
                detail = f"{cc.new_value or '—'}"
                if cc.new_footprint:
                    detail += f" [{cc.new_footprint}]"
                lines.append(f"- **Added** `{cc.reference}`: {detail}")
            elif cc.change_type == "removed":
                detail = f"{cc.old_value or '—'}"
                if cc.old_footprint:
                    detail += f" [{cc.old_footprint}]"
                lines.append(f"- **Removed** `{cc.reference}`: {detail}")
            else:
                parts: List[str] = []
                if cc.old_value != cc.new_value:
                    parts.append(f"value {cc.old_value!r} → {cc.new_value!r}")
                if cc.old_footprint != cc.new_footprint:
                    parts.append(
                        f"footprint {cc.old_footprint!r} → {cc.new_footprint!r}"
                    )
                lines.append(
                    f"- **Modified** `{cc.reference}`: " + ", ".join(parts)
                )
        return "\n".join(lines)

    def detect_and_handle_pcb_change(self, req: ProjectRequirements) -> bool:
        """Check for PCB changes and, if found, transition to :attr:`~ContextState.PCB_CHANGED`.

        When a change is detected this method:

        1. Computes the current file hashes.
        2. Builds a :class:`FileChange` list (added/modified/removed files) and
           stores it on :attr:`~ProjectRequirements.file_changes`.
        3. Computes the current component snapshot and diffs it against the
           stored :attr:`~ProjectRequirements.component_snapshot` to produce a
           :class:`ComponentChange` list stored on
           :attr:`~ProjectRequirements.component_changes`.
        4. Transitions the state to ``PCB_CHANGED``.

        Both :attr:`~ProjectRequirements.file_changes` and
        :attr:`~ProjectRequirements.component_changes` remain available
        throughout the ``UPDATING_CONTEXT`` phase.  Use
        :meth:`format_file_change_list` and :meth:`format_component_changes` to
        render Markdown summaries suitable for passing alongside the previous
        :attr:`~ProjectRequirements.auto_context` to the context generator.

        This method is safe to call only when *req* is in
        :attr:`~ContextState.UP_TO_DATE` state.

        Args:
            req: The current requirements state (must be ``UP_TO_DATE``).

        Returns:
            ``True`` if a change was detected and the state was updated.
        """
        if req.state != ContextState.UP_TO_DATE:
            return False
        if not req.pcb_file_hashes:
            return False
        current_hashes = self.compute_file_hashes()
        if current_hashes == req.pcb_file_hashes:
            return False
        req.file_changes = self.compute_file_change_list(
            req.pcb_file_hashes, current_hashes
        )
        # Component-level semantic diff
        new_snapshot = self.compute_component_snapshot()
        req.component_changes = self.compute_component_changes(
            req.component_snapshot, new_snapshot
        )
        self.transition(
            req,
            ContextState.PCB_CHANGED,
            reason=(
                f"{len(req.file_changes)} tracked KiCad file(s) changed: "
                + ", ".join(fc.path for fc in req.file_changes)
            ),
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

        Also snapshots the current file hashes and component list so that
        future change detection has a baseline.

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
        req.component_snapshot = self.compute_component_snapshot()
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
