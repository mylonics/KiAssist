"""Tests for the structured context/requirements lifecycle system.

Covers:
* ContextState enum values and JSON serialisability
* ProjectRequirements serialisation round-trip (including file_changes and component_changes)
* ProjectRequirements convenience properties
* StateTransition serialisation
* FileChange serialisation
* ComponentChange serialisation
* RequirementsManager: load_or_create, save, transition enforcement
* RequirementsManager: file hashing and change detection
* RequirementsManager: compute_file_change_list and format_file_change_list
* RequirementsManager: compute_component_snapshot, compute_component_changes, format_component_changes
* RequirementsManager: diff generation
* RequirementsManager: high-level workflow helpers (set_raw_context captures component snapshot)
* Full lifecycle: fresh start → UP_TO_DATE
* Full lifecycle: UP_TO_DATE → PCB change → update → UP_TO_DATE
* Safeguard: user requirements not modified outside QUERYING_USER
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from kiassist_utils.context.requirements import (
    ComponentChange,
    ContextState,
    FileChange,
    ProjectRequirements,
    RequirementsManager,
    StateTransition,
    _VALID_TRANSITIONS,
)


# ===========================================================================
# ContextState
# ===========================================================================


class TestContextState:
    def test_all_states_defined(self):
        expected = {
            "getting_raw_context",
            "generating_requirements",
            "querying_user",
            "refining_user_responses",
            "up_to_date",
            "pcb_changed",
            "updating_context",
        }
        assert {s.value for s in ContextState} == expected

    def test_json_serialisable(self):
        """ContextState values must survive a JSON round-trip (str mixin)."""
        data = {"state": ContextState.UP_TO_DATE}
        serialised = json.dumps(data)
        loaded = json.loads(serialised)
        assert loaded["state"] == "up_to_date"

    def test_valid_transitions_cover_all_states(self):
        """Every state must appear as a key in the transition graph."""
        assert set(_VALID_TRANSITIONS.keys()) == set(ContextState)


# ===========================================================================
# StateTransition
# ===========================================================================


class TestStateTransition:
    def test_to_dict_round_trip(self):
        t = StateTransition(
            from_state="up_to_date",
            to_state="pcb_changed",
            timestamp="2024-01-01T00:00:00+00:00",
            reason="file changed",
        )
        d = t.to_dict()
        t2 = StateTransition.from_dict(d)
        assert t2.from_state == t.from_state
        assert t2.to_state == t.to_state
        assert t2.timestamp == t.timestamp
        assert t2.reason == t.reason

    def test_from_dict_missing_reason_defaults_empty(self):
        d = {
            "from_state": "a",
            "to_state": "b",
            "timestamp": "ts",
        }
        t = StateTransition.from_dict(d)
        assert t.reason == ""


# ===========================================================================
# FileChange
# ===========================================================================


class TestFileChange:
    def test_to_dict_round_trip_added(self):
        fc = FileChange(path="board.kicad_pcb", change_type="added", new_hash="abc")
        d = fc.to_dict()
        fc2 = FileChange.from_dict(d)
        assert fc2.path == "board.kicad_pcb"
        assert fc2.change_type == "added"
        assert fc2.old_hash is None
        assert fc2.new_hash == "abc"

    def test_to_dict_round_trip_modified(self):
        fc = FileChange(
            path="schema.kicad_sch",
            change_type="modified",
            old_hash="old",
            new_hash="new",
        )
        d = fc.to_dict()
        fc2 = FileChange.from_dict(d)
        assert fc2.change_type == "modified"
        assert fc2.old_hash == "old"
        assert fc2.new_hash == "new"

    def test_to_dict_round_trip_removed(self):
        fc = FileChange(path="old.kicad_pcb", change_type="removed", old_hash="xyz")
        d = fc.to_dict()
        fc2 = FileChange.from_dict(d)
        assert fc2.change_type == "removed"
        assert fc2.old_hash == "xyz"
        assert fc2.new_hash is None


# ===========================================================================
# ComponentChange
# ===========================================================================


class TestComponentChange:
    def test_to_dict_round_trip_added(self):
        cc = ComponentChange(
            reference="R5",
            change_type="added",
            new_value="10k",
            new_footprint="Resistor_SMD:R_0402",
        )
        d = cc.to_dict()
        cc2 = ComponentChange.from_dict(d)
        assert cc2.reference == "R5"
        assert cc2.change_type == "added"
        assert cc2.old_value is None
        assert cc2.old_footprint is None
        assert cc2.new_value == "10k"
        assert cc2.new_footprint == "Resistor_SMD:R_0402"

    def test_to_dict_round_trip_removed(self):
        cc = ComponentChange(
            reference="C3",
            change_type="removed",
            old_value="100nF",
            old_footprint="C_0402",
        )
        d = cc.to_dict()
        cc2 = ComponentChange.from_dict(d)
        assert cc2.change_type == "removed"
        assert cc2.old_value == "100nF"
        assert cc2.new_value is None

    def test_to_dict_round_trip_modified(self):
        cc = ComponentChange(
            reference="R1",
            change_type="modified",
            old_value="1k",
            new_value="10k",
            old_footprint="R_0402",
            new_footprint="R_0402",
        )
        d = cc.to_dict()
        cc2 = ComponentChange.from_dict(d)
        assert cc2.change_type == "modified"
        assert cc2.old_value == "1k"
        assert cc2.new_value == "10k"


# ===========================================================================
# ProjectRequirements
# ===========================================================================


class TestProjectRequirements:
    def test_default_state_is_getting_raw_context(self):
        req = ProjectRequirements()
        assert req.state == ContextState.GETTING_RAW_CONTEXT

    def test_serialisation_round_trip(self):
        fc = FileChange(path="board.kicad_pcb", change_type="modified", old_hash="a", new_hash="b")
        cc = ComponentChange(
            reference="R1",
            change_type="modified",
            old_value="1k",
            new_value="10k",
            old_footprint="0402",
            new_footprint="0402",
        )
        req = ProjectRequirements(
            state=ContextState.UP_TO_DATE,
            user_requirements="# My requirements",
            auto_context="# Auto context",
            raw_context="# Raw",
            pending_questions=["Q1?", "Q2?"],
            pcb_file_hashes={"board.kicad_pcb": "abc123"},
            context_diff="@@ ...",
            file_changes=[fc],
            component_snapshot={"R1": {"value": "1k", "footprint": "0402"}},
            component_changes=[cc],
        )
        d = req.to_dict()
        req2 = ProjectRequirements.from_dict(d)
        assert req2.state == req.state
        assert req2.user_requirements == req.user_requirements
        assert req2.auto_context == req.auto_context
        assert req2.raw_context == req.raw_context
        assert req2.pending_questions == req.pending_questions
        assert req2.pcb_file_hashes == req.pcb_file_hashes
        assert req2.context_diff == req.context_diff
        assert len(req2.file_changes) == 1
        assert req2.file_changes[0].path == "board.kicad_pcb"
        assert req2.file_changes[0].change_type == "modified"
        assert req2.component_snapshot == {"R1": {"value": "1k", "footprint": "0402"}}
        assert len(req2.component_changes) == 1
        assert req2.component_changes[0].reference == "R1"
        assert req2.component_changes[0].change_type == "modified"

    def test_is_stable_only_when_up_to_date(self):
        for state in ContextState:
            req = ProjectRequirements(state=state)
            assert req.is_stable == (state == ContextState.UP_TO_DATE)

    def test_needs_user_input(self):
        for state in ContextState:
            req = ProjectRequirements(state=state)
            expected = state in (
                ContextState.QUERYING_USER,
                ContextState.REFINING_USER_RESPONSES,
            )
            assert req.needs_user_input == expected, state

    def test_is_stale(self):
        for state in ContextState:
            req = ProjectRequirements(state=state)
            expected = state in (
                ContextState.PCB_CHANGED,
                ContextState.UPDATING_CONTEXT,
            )
            assert req.is_stale == expected, state

    def test_state_history_preserved(self):
        t = StateTransition(
            from_state="getting_raw_context",
            to_state="generating_requirements",
            timestamp="ts",
        )
        req = ProjectRequirements(state_history=[t])
        d = req.to_dict()
        req2 = ProjectRequirements.from_dict(d)
        assert len(req2.state_history) == 1
        assert req2.state_history[0].from_state == "getting_raw_context"


# ===========================================================================
# RequirementsManager — persistence
# ===========================================================================


class TestRequirementsManagerPersistence:
    def test_load_or_create_returns_fresh_when_no_file(self, tmp_path: Path):
        manager = RequirementsManager(tmp_path)
        req = manager.load_or_create()
        assert req.state == ContextState.GETTING_RAW_CONTEXT
        assert req.user_requirements == ""

    def test_init_with_existing_kicad_pro_path(self, tmp_path: Path):
        """A .kicad_pro path that exists resolves to its parent directory."""
        pro_file = tmp_path / "board.kicad_pro"
        pro_file.write_text("", encoding="utf-8")
        manager = RequirementsManager(pro_file)
        assert manager.project_dir == tmp_path

    def test_init_with_nonexistent_kicad_pro_path(self, tmp_path: Path):
        """A .kicad_pro path that doesn't exist yet resolves to its parent directory."""
        pro_file = tmp_path / "board.kicad_pro"
        # File does NOT exist — suffix-based detection must still work
        manager = RequirementsManager(pro_file)
        assert manager.project_dir == tmp_path

    def test_init_with_directory(self, tmp_path: Path):
        """A plain directory path is used as the project directory directly."""
        manager = RequirementsManager(tmp_path)
        assert manager.project_dir == tmp_path

    def test_save_and_reload(self, tmp_path: Path):
        manager = RequirementsManager(tmp_path)
        req = ProjectRequirements(
            state=ContextState.UP_TO_DATE,
            user_requirements="# Requirements",
        )
        manager.save(req)
        reloaded = manager.load_or_create()
        assert reloaded.state == ContextState.UP_TO_DATE
        assert reloaded.user_requirements == "# Requirements"

    def test_save_creates_state_dir(self, tmp_path: Path):
        project_dir = tmp_path / "my_project"
        project_dir.mkdir()
        manager = RequirementsManager(project_dir)
        req = ProjectRequirements()
        manager.save(req)
        assert manager.state_path.exists()

    def test_load_gracefully_handles_corrupt_file(self, tmp_path: Path):
        manager = RequirementsManager(tmp_path)
        state_dir = tmp_path / ".kiassist"
        state_dir.mkdir()
        (state_dir / "requirements_state.json").write_text(
            "not valid json", encoding="utf-8"
        )
        req = manager.load_or_create()
        assert req.state == ContextState.GETTING_RAW_CONTEXT

    def test_save_is_atomic(self, tmp_path: Path):
        """No .tmp file should remain after a successful save."""
        manager = RequirementsManager(tmp_path)
        req = ProjectRequirements()
        manager.save(req)
        tmp_files = list(tmp_path.rglob("*.tmp"))
        assert tmp_files == []

    def test_updated_at_refreshed_on_save(self, tmp_path: Path):
        manager = RequirementsManager(tmp_path)
        req = ProjectRequirements()
        original_ts = req.updated_at
        time.sleep(0.01)
        manager.save(req)
        assert req.updated_at >= original_ts


# ===========================================================================
# RequirementsManager — state transitions
# ===========================================================================


class TestRequirementsManagerTransitions:
    def test_valid_transition_recorded_in_history(self, tmp_path: Path):
        manager = RequirementsManager(tmp_path)
        req = ProjectRequirements(state=ContextState.GETTING_RAW_CONTEXT)
        manager.save(req)
        manager.transition(req, ContextState.GENERATING_REQUIREMENTS, reason="test")
        assert req.state == ContextState.GENERATING_REQUIREMENTS
        assert len(req.state_history) == 1
        assert req.state_history[0].reason == "test"

    def test_invalid_transition_raises_value_error(self, tmp_path: Path):
        manager = RequirementsManager(tmp_path)
        req = ProjectRequirements(state=ContextState.UP_TO_DATE)
        manager.save(req)
        with pytest.raises(ValueError, match="Cannot transition"):
            manager.transition(req, ContextState.GETTING_RAW_CONTEXT)

    def test_transition_persists_state(self, tmp_path: Path):
        manager = RequirementsManager(tmp_path)
        req = ProjectRequirements(state=ContextState.GETTING_RAW_CONTEXT)
        manager.save(req)
        manager.transition(req, ContextState.GENERATING_REQUIREMENTS)
        reloaded = manager.load_or_create()
        assert reloaded.state == ContextState.GENERATING_REQUIREMENTS

    @pytest.mark.parametrize(
        "from_state, to_state",
        [
            (ContextState.GETTING_RAW_CONTEXT, ContextState.GENERATING_REQUIREMENTS),
            (ContextState.GENERATING_REQUIREMENTS, ContextState.QUERYING_USER),
            (ContextState.GENERATING_REQUIREMENTS, ContextState.UP_TO_DATE),
            (ContextState.QUERYING_USER, ContextState.REFINING_USER_RESPONSES),
            (ContextState.REFINING_USER_RESPONSES, ContextState.UP_TO_DATE),
            (ContextState.REFINING_USER_RESPONSES, ContextState.QUERYING_USER),
            (ContextState.UP_TO_DATE, ContextState.PCB_CHANGED),
            (ContextState.PCB_CHANGED, ContextState.UPDATING_CONTEXT),
            (ContextState.UPDATING_CONTEXT, ContextState.GENERATING_REQUIREMENTS),
            (ContextState.UPDATING_CONTEXT, ContextState.QUERYING_USER),
            (ContextState.UPDATING_CONTEXT, ContextState.UP_TO_DATE),
        ],
    )
    def test_all_valid_transitions_succeed(
        self, tmp_path: Path, from_state: ContextState, to_state: ContextState
    ):
        manager = RequirementsManager(tmp_path)
        req = ProjectRequirements(state=from_state)
        manager.save(req)
        manager.transition(req, to_state)
        assert req.state == to_state


# ===========================================================================
# RequirementsManager — file hashing & change detection
# ===========================================================================


class TestRequirementsManagerHashing:
    def _make_kicad_files(self, project_dir: Path) -> None:
        (project_dir / "board.kicad_pcb").write_text("pcb data", encoding="utf-8")
        (project_dir / "schema.kicad_sch").write_text("sch data", encoding="utf-8")
        (project_dir / "project.kicad_pro").write_text("pro data", encoding="utf-8")

    def test_compute_file_hashes_returns_dict(self, tmp_path: Path):
        self._make_kicad_files(tmp_path)
        manager = RequirementsManager(tmp_path)
        hashes = manager.compute_file_hashes()
        assert "board.kicad_pcb" in hashes
        assert "schema.kicad_sch" in hashes
        assert "project.kicad_pro" in hashes

    def test_hashes_are_sha256_hex_strings(self, tmp_path: Path):
        self._make_kicad_files(tmp_path)
        manager = RequirementsManager(tmp_path)
        hashes = manager.compute_file_hashes()
        for val in hashes.values():
            assert len(val) == 64
            assert all(c in "0123456789abcdef" for c in val)

    def test_non_kicad_files_excluded(self, tmp_path: Path):
        (tmp_path / "README.md").write_text("readme")
        (tmp_path / "notes.txt").write_text("notes")
        manager = RequirementsManager(tmp_path)
        hashes = manager.compute_file_hashes()
        assert "README.md" not in hashes
        assert "notes.txt" not in hashes

    def test_check_for_pcb_changes_false_when_no_stored_hashes(self, tmp_path: Path):
        manager = RequirementsManager(tmp_path)
        req = ProjectRequirements()
        assert manager.check_for_pcb_changes(req) is False

    def test_check_for_pcb_changes_false_when_unchanged(self, tmp_path: Path):
        self._make_kicad_files(tmp_path)
        manager = RequirementsManager(tmp_path)
        req = ProjectRequirements()
        req.pcb_file_hashes = manager.compute_file_hashes()
        assert manager.check_for_pcb_changes(req) is False

    def test_check_for_pcb_changes_true_when_file_modified(self, tmp_path: Path):
        self._make_kicad_files(tmp_path)
        manager = RequirementsManager(tmp_path)
        req = ProjectRequirements()
        req.pcb_file_hashes = manager.compute_file_hashes()
        # Modify a file
        (tmp_path / "board.kicad_pcb").write_text("updated pcb data", encoding="utf-8")
        assert manager.check_for_pcb_changes(req) is True

    def test_check_for_pcb_changes_true_when_file_added(self, tmp_path: Path):
        self._make_kicad_files(tmp_path)
        manager = RequirementsManager(tmp_path)
        req = ProjectRequirements()
        req.pcb_file_hashes = manager.compute_file_hashes()
        (tmp_path / "new_board.kicad_pcb").write_text("new pcb data")
        assert manager.check_for_pcb_changes(req) is True

    def test_detect_and_handle_pcb_change_transitions_state(self, tmp_path: Path):
        self._make_kicad_files(tmp_path)
        manager = RequirementsManager(tmp_path)
        req = ProjectRequirements(state=ContextState.UP_TO_DATE)
        req.pcb_file_hashes = manager.compute_file_hashes()
        manager.save(req)
        # Modify a file
        (tmp_path / "board.kicad_pcb").write_text("changed", encoding="utf-8")
        changed = manager.detect_and_handle_pcb_change(req)
        assert changed is True
        assert req.state == ContextState.PCB_CHANGED

    def test_detect_and_handle_no_change_returns_false(self, tmp_path: Path):
        self._make_kicad_files(tmp_path)
        manager = RequirementsManager(tmp_path)
        req = ProjectRequirements(state=ContextState.UP_TO_DATE)
        req.pcb_file_hashes = manager.compute_file_hashes()
        manager.save(req)
        changed = manager.detect_and_handle_pcb_change(req)
        assert changed is False
        assert req.state == ContextState.UP_TO_DATE

    def test_detect_and_handle_ignores_non_up_to_date(self, tmp_path: Path):
        self._make_kicad_files(tmp_path)
        manager = RequirementsManager(tmp_path)
        req = ProjectRequirements(state=ContextState.GETTING_RAW_CONTEXT)
        req.pcb_file_hashes = manager.compute_file_hashes()
        manager.save(req)
        (tmp_path / "board.kicad_pcb").write_text("changed", encoding="utf-8")
        changed = manager.detect_and_handle_pcb_change(req)
        assert changed is False

    def test_detect_and_handle_populates_file_changes(self, tmp_path: Path):
        """file_changes must be populated when a PCB change is detected."""
        self._make_kicad_files(tmp_path)
        manager = RequirementsManager(tmp_path)
        req = ProjectRequirements(state=ContextState.UP_TO_DATE)
        req.pcb_file_hashes = manager.compute_file_hashes()
        manager.save(req)
        (tmp_path / "board.kicad_pcb").write_text("changed", encoding="utf-8")
        manager.detect_and_handle_pcb_change(req)
        assert len(req.file_changes) == 1
        assert req.file_changes[0].path == "board.kicad_pcb"
        assert req.file_changes[0].change_type == "modified"

    def test_detect_and_handle_file_changes_lists_added_file(self, tmp_path: Path):
        self._make_kicad_files(tmp_path)
        manager = RequirementsManager(tmp_path)
        req = ProjectRequirements(state=ContextState.UP_TO_DATE)
        req.pcb_file_hashes = manager.compute_file_hashes()
        manager.save(req)
        (tmp_path / "new_sheet.kicad_sch").write_text("new sheet data", encoding="utf-8")
        manager.detect_and_handle_pcb_change(req)
        added = [fc for fc in req.file_changes if fc.change_type == "added"]
        assert any(fc.path == "new_sheet.kicad_sch" for fc in added)

    def test_detect_and_handle_file_changes_lists_removed_file(self, tmp_path: Path):
        self._make_kicad_files(tmp_path)
        manager = RequirementsManager(tmp_path)
        req = ProjectRequirements(state=ContextState.UP_TO_DATE)
        req.pcb_file_hashes = manager.compute_file_hashes()
        manager.save(req)
        (tmp_path / "board.kicad_pcb").unlink()
        manager.detect_and_handle_pcb_change(req)
        removed = [fc for fc in req.file_changes if fc.change_type == "removed"]
        assert any(fc.path == "board.kicad_pcb" for fc in removed)

    def test_detect_and_handle_transition_reason_names_changed_files(self, tmp_path: Path):
        """The transition reason should name which file(s) changed."""
        self._make_kicad_files(tmp_path)
        manager = RequirementsManager(tmp_path)
        req = ProjectRequirements(state=ContextState.UP_TO_DATE)
        req.pcb_file_hashes = manager.compute_file_hashes()
        manager.save(req)
        (tmp_path / "board.kicad_pcb").write_text("changed", encoding="utf-8")
        manager.detect_and_handle_pcb_change(req)
        last_transition = req.state_history[-1]
        assert "board.kicad_pcb" in last_transition.reason


# ===========================================================================
# RequirementsManager — compute_file_change_list & format_file_change_list
# ===========================================================================


class TestFileChangeList:
    def test_no_changes_returns_empty(self, tmp_path: Path):
        manager = RequirementsManager(tmp_path)
        hashes = {"a.kicad_pcb": "h1", "b.kicad_sch": "h2"}
        changes = manager.compute_file_change_list(hashes, hashes)
        assert changes == []

    def test_detects_added_file(self, tmp_path: Path):
        manager = RequirementsManager(tmp_path)
        old = {"a.kicad_pcb": "h1"}
        new = {"a.kicad_pcb": "h1", "b.kicad_sch": "h2"}
        changes = manager.compute_file_change_list(old, new)
        assert len(changes) == 1
        assert changes[0].path == "b.kicad_sch"
        assert changes[0].change_type == "added"
        assert changes[0].old_hash is None
        assert changes[0].new_hash == "h2"

    def test_detects_removed_file(self, tmp_path: Path):
        manager = RequirementsManager(tmp_path)
        old = {"a.kicad_pcb": "h1", "b.kicad_sch": "h2"}
        new = {"a.kicad_pcb": "h1"}
        changes = manager.compute_file_change_list(old, new)
        assert len(changes) == 1
        assert changes[0].path == "b.kicad_sch"
        assert changes[0].change_type == "removed"
        assert changes[0].old_hash == "h2"
        assert changes[0].new_hash is None

    def test_detects_modified_file(self, tmp_path: Path):
        manager = RequirementsManager(tmp_path)
        old = {"a.kicad_pcb": "old_hash"}
        new = {"a.kicad_pcb": "new_hash"}
        changes = manager.compute_file_change_list(old, new)
        assert len(changes) == 1
        assert changes[0].path == "a.kicad_pcb"
        assert changes[0].change_type == "modified"
        assert changes[0].old_hash == "old_hash"
        assert changes[0].new_hash == "new_hash"

    def test_detects_multiple_changes(self, tmp_path: Path):
        manager = RequirementsManager(tmp_path)
        old = {"a.kicad_pcb": "h1", "b.kicad_sch": "h2", "c.kicad_pro": "h3"}
        new = {"a.kicad_pcb": "h1_mod", "c.kicad_pro": "h3", "d.kicad_sym": "h4"}
        changes = manager.compute_file_change_list(old, new)
        change_map = {fc.path: fc.change_type for fc in changes}
        assert change_map["a.kicad_pcb"] == "modified"
        assert change_map["b.kicad_sch"] == "removed"
        assert change_map["d.kicad_sym"] == "added"
        assert "c.kicad_pro" not in change_map

    def test_results_sorted_by_path(self, tmp_path: Path):
        manager = RequirementsManager(tmp_path)
        old = {"z.kicad_pcb": "h1", "a.kicad_sch": "h2"}
        new = {"z.kicad_pcb": "h1_mod", "a.kicad_sch": "h2_mod"}
        changes = manager.compute_file_change_list(old, new)
        paths = [fc.path for fc in changes]
        assert paths == sorted(paths)

    def test_format_empty_returns_empty_string(self, tmp_path: Path):
        manager = RequirementsManager(tmp_path)
        assert manager.format_file_change_list([]) == ""

    def test_format_includes_added_file(self, tmp_path: Path):
        manager = RequirementsManager(tmp_path)
        changes = [FileChange(path="new.kicad_sch", change_type="added", new_hash="h")]
        output = manager.format_file_change_list(changes)
        assert "new.kicad_sch" in output
        assert "Added" in output or "added" in output.lower()

    def test_format_includes_removed_file(self, tmp_path: Path):
        manager = RequirementsManager(tmp_path)
        changes = [FileChange(path="old.kicad_pcb", change_type="removed", old_hash="h")]
        output = manager.format_file_change_list(changes)
        assert "old.kicad_pcb" in output
        assert "Removed" in output or "removed" in output.lower()

    def test_format_includes_modified_file(self, tmp_path: Path):
        manager = RequirementsManager(tmp_path)
        changes = [
            FileChange(
                path="board.kicad_pcb",
                change_type="modified",
                old_hash="a",
                new_hash="b",
            )
        ]
        output = manager.format_file_change_list(changes)
        assert "board.kicad_pcb" in output
        assert "Modified" in output or "modified" in output.lower()

    def test_format_change_list_round_trips_after_pcb_change(self, tmp_path: Path):
        """The change list produced after detect_and_handle_pcb_change should
        format without error and contain the modified file name."""
        (tmp_path / "board.kicad_pcb").write_text("original")
        manager = RequirementsManager(tmp_path)
        req = ProjectRequirements(state=ContextState.UP_TO_DATE)
        req.pcb_file_hashes = manager.compute_file_hashes()
        manager.save(req)
        (tmp_path / "board.kicad_pcb").write_text("updated")
        manager.detect_and_handle_pcb_change(req)
        formatted = manager.format_file_change_list(req.file_changes)
        assert "board.kicad_pcb" in formatted


# ===========================================================================
# RequirementsManager — compute_component_changes & format_component_changes
# ===========================================================================


class TestComponentChangeList:
    """Tests for compute_component_changes and format_component_changes.

    compute_component_snapshot() requires real .kicad_sch files parseable by
    the kicad_parser; those end-to-end tests live in TestComponentSnapshotE2E.
    Here we test the diffing and formatting logic in isolation.
    """

    def test_no_changes_returns_empty(self, tmp_path: Path):
        manager = RequirementsManager(tmp_path)
        snap = {"R1": {"value": "10k", "footprint": "R_0402"}}
        changes = manager.compute_component_changes(snap, snap)
        assert changes == []

    def test_detects_added_component(self, tmp_path: Path):
        manager = RequirementsManager(tmp_path)
        old = {"R1": {"value": "10k", "footprint": "R_0402"}}
        new = {
            "R1": {"value": "10k", "footprint": "R_0402"},
            "R2": {"value": "1k", "footprint": "R_0603"},
        }
        changes = manager.compute_component_changes(old, new)
        assert len(changes) == 1
        assert changes[0].reference == "R2"
        assert changes[0].change_type == "added"
        assert changes[0].old_value is None
        assert changes[0].new_value == "1k"
        assert changes[0].new_footprint == "R_0603"

    def test_detects_removed_component(self, tmp_path: Path):
        manager = RequirementsManager(tmp_path)
        old = {
            "R1": {"value": "10k", "footprint": "R_0402"},
            "C1": {"value": "100nF", "footprint": "C_0402"},
        }
        new = {"R1": {"value": "10k", "footprint": "R_0402"}}
        changes = manager.compute_component_changes(old, new)
        assert len(changes) == 1
        assert changes[0].reference == "C1"
        assert changes[0].change_type == "removed"
        assert changes[0].old_value == "100nF"
        assert changes[0].new_value is None

    def test_detects_modified_value(self, tmp_path: Path):
        manager = RequirementsManager(tmp_path)
        old = {"R1": {"value": "1k", "footprint": "R_0402"}}
        new = {"R1": {"value": "10k", "footprint": "R_0402"}}
        changes = manager.compute_component_changes(old, new)
        assert len(changes) == 1
        assert changes[0].reference == "R1"
        assert changes[0].change_type == "modified"
        assert changes[0].old_value == "1k"
        assert changes[0].new_value == "10k"

    def test_detects_modified_footprint(self, tmp_path: Path):
        manager = RequirementsManager(tmp_path)
        old = {"C1": {"value": "100nF", "footprint": "C_0402"}}
        new = {"C1": {"value": "100nF", "footprint": "C_0603"}}
        changes = manager.compute_component_changes(old, new)
        assert len(changes) == 1
        assert changes[0].change_type == "modified"
        assert changes[0].old_footprint == "C_0402"
        assert changes[0].new_footprint == "C_0603"

    def test_results_sorted_by_reference(self, tmp_path: Path):
        manager = RequirementsManager(tmp_path)
        old = {"Z1": {"value": "v", "footprint": ""}, "A1": {"value": "v", "footprint": ""}}
        new = {"Z1": {"value": "v2", "footprint": ""}, "A1": {"value": "v2", "footprint": ""}}
        changes = manager.compute_component_changes(old, new)
        refs = [cc.reference for cc in changes]
        assert refs == sorted(refs)

    def test_unchanged_components_omitted(self, tmp_path: Path):
        manager = RequirementsManager(tmp_path)
        snap = {
            "R1": {"value": "10k", "footprint": "R_0402"},
            "C1": {"value": "100nF", "footprint": "C_0402"},
        }
        new = dict(snap)
        new["R2"] = {"value": "1k", "footprint": "R_0402"}
        changes = manager.compute_component_changes(snap, new)
        refs = [cc.reference for cc in changes]
        assert "R1" not in refs
        assert "C1" not in refs
        assert "R2" in refs

    def test_format_empty_returns_empty_string(self, tmp_path: Path):
        manager = RequirementsManager(tmp_path)
        assert manager.format_component_changes([]) == ""

    def test_format_added_component(self, tmp_path: Path):
        manager = RequirementsManager(tmp_path)
        changes = [
            ComponentChange(
                reference="R5",
                change_type="added",
                new_value="10k",
                new_footprint="Resistor_SMD:R_0402",
            )
        ]
        output = manager.format_component_changes(changes)
        assert "R5" in output
        assert "10k" in output
        assert "Added" in output or "added" in output.lower()

    def test_format_removed_component(self, tmp_path: Path):
        manager = RequirementsManager(tmp_path)
        changes = [
            ComponentChange(
                reference="C3",
                change_type="removed",
                old_value="100nF",
                old_footprint="C_0402",
            )
        ]
        output = manager.format_component_changes(changes)
        assert "C3" in output
        assert "Removed" in output or "removed" in output.lower()

    def test_format_modified_component_value(self, tmp_path: Path):
        manager = RequirementsManager(tmp_path)
        changes = [
            ComponentChange(
                reference="R1",
                change_type="modified",
                old_value="1k",
                new_value="10k",
                old_footprint="R_0402",
                new_footprint="R_0402",
            )
        ]
        output = manager.format_component_changes(changes)
        assert "R1" in output
        assert "1k" in output
        assert "10k" in output

    def test_detect_and_handle_populates_component_changes(self, tmp_path: Path):
        """detect_and_handle_pcb_change must populate component_changes."""
        # When no .kicad_sch files exist the snapshot is empty; adding a file
        # that changes the hash triggers the transition and leaves component_changes
        # as an empty list (no schematic to parse), which is fine — we just
        # verify the field is set (not None) and the transition happened.
        (tmp_path / "board.kicad_pcb").write_text("original", encoding="utf-8")
        manager = RequirementsManager(tmp_path)
        req = ProjectRequirements(state=ContextState.UP_TO_DATE)
        req.pcb_file_hashes = manager.compute_file_hashes()
        manager.save(req)
        (tmp_path / "board.kicad_pcb").write_text("updated", encoding="utf-8")
        manager.detect_and_handle_pcb_change(req)
        assert isinstance(req.component_changes, list)
        assert req.state == ContextState.PCB_CHANGED

    def test_set_raw_context_captures_component_snapshot(self, tmp_path: Path):
        """set_raw_context must capture the component snapshot."""
        # No .kicad_sch files → empty snapshot, but the attribute must be set.
        manager = RequirementsManager(tmp_path)
        req = manager.start_raw_context_generation()
        manager.set_raw_context(req, "# Raw context")
        assert isinstance(req.component_snapshot, dict)


# ===========================================================================
# RequirementsManager — diff generation
# ===========================================================================


class TestRequirementsManagerDiff:
    def test_diff_returns_empty_when_no_change(self, tmp_path: Path):
        manager = RequirementsManager(tmp_path)
        diff = manager.generate_diff("same content\n", "same content\n")
        assert diff == ""

    def test_diff_contains_added_lines(self, tmp_path: Path):
        manager = RequirementsManager(tmp_path)
        old = "line1\nline2\n"
        new = "line1\nline2\nnew_line\n"
        diff = manager.generate_diff(old, new)
        assert "+new_line" in diff

    def test_diff_contains_removed_lines(self, tmp_path: Path):
        manager = RequirementsManager(tmp_path)
        old = "line1\nline2\n"
        new = "line1\n"
        diff = manager.generate_diff(old, new)
        assert "-line2" in diff

    def test_diff_labels_old_and_new(self, tmp_path: Path):
        manager = RequirementsManager(tmp_path)
        diff = manager.generate_diff("old\n", "new\n")
        assert "previous_context" in diff
        assert "updated_context" in diff


# ===========================================================================
# RequirementsManager — high-level workflow helpers
# ===========================================================================


class TestRequirementsManagerWorkflow:
    def test_start_raw_context_generation_returns_fresh_req(self, tmp_path: Path):
        manager = RequirementsManager(tmp_path)
        req = manager.start_raw_context_generation()
        assert req.state == ContextState.GETTING_RAW_CONTEXT
        assert manager.state_path.exists()

    def test_set_raw_context_advances_state(self, tmp_path: Path):
        manager = RequirementsManager(tmp_path)
        req = manager.start_raw_context_generation()
        manager.set_raw_context(req, "# Raw Context\n\nNetlist...")
        assert req.state == ContextState.GENERATING_REQUIREMENTS
        assert req.raw_context == "# Raw Context\n\nNetlist..."

    def test_set_raw_context_snapshots_file_hashes(self, tmp_path: Path):
        (tmp_path / "board.kicad_pcb").write_text("pcb")
        manager = RequirementsManager(tmp_path)
        req = manager.start_raw_context_generation()
        manager.set_raw_context(req, "context")
        assert "board.kicad_pcb" in req.pcb_file_hashes

    def test_set_raw_context_wrong_state_raises(self, tmp_path: Path):
        manager = RequirementsManager(tmp_path)
        req = ProjectRequirements(state=ContextState.UP_TO_DATE)
        with pytest.raises(ValueError):
            manager.set_raw_context(req, "context")

    def test_set_auto_context_no_questions_goes_up_to_date(self, tmp_path: Path):
        manager = RequirementsManager(tmp_path)
        req = ProjectRequirements(state=ContextState.GENERATING_REQUIREMENTS)
        manager.save(req)
        manager.set_auto_context(req, "# Context", pending_questions=[])
        assert req.state == ContextState.UP_TO_DATE

    def test_set_auto_context_with_questions_goes_querying_user(self, tmp_path: Path):
        manager = RequirementsManager(tmp_path)
        req = ProjectRequirements(state=ContextState.GENERATING_REQUIREMENTS)
        manager.save(req)
        manager.set_auto_context(
            req, "# Context", pending_questions=["What voltage?"]
        )
        assert req.state == ContextState.QUERYING_USER
        assert "What voltage?" in req.pending_questions

    def test_set_auto_context_computes_diff_when_prev_context(self, tmp_path: Path):
        manager = RequirementsManager(tmp_path)
        req = ProjectRequirements(state=ContextState.GENERATING_REQUIREMENTS)
        req.auto_context = "old context\n"
        manager.save(req)
        manager.set_auto_context(req, "new context\n", pending_questions=[])
        assert req.context_diff != ""

    def test_set_auto_context_wrong_state_raises(self, tmp_path: Path):
        manager = RequirementsManager(tmp_path)
        req = ProjectRequirements(state=ContextState.UP_TO_DATE)
        with pytest.raises(ValueError):
            manager.set_auto_context(req, "context")

    def test_submit_user_answers_advances_state(self, tmp_path: Path):
        manager = RequirementsManager(tmp_path)
        req = ProjectRequirements(
            state=ContextState.QUERYING_USER,
            pending_questions=["Q?"],
        )
        manager.save(req)
        manager.submit_user_answers(req, "# User Requirements")
        assert req.state == ContextState.REFINING_USER_RESPONSES
        assert req.user_requirements == "# User Requirements"
        assert req.pending_questions == []

    def test_submit_user_answers_wrong_state_raises(self, tmp_path: Path):
        manager = RequirementsManager(tmp_path)
        req = ProjectRequirements(state=ContextState.UP_TO_DATE)
        with pytest.raises(ValueError):
            manager.submit_user_answers(req, "requirements")

    def test_mark_up_to_date_from_refining(self, tmp_path: Path):
        manager = RequirementsManager(tmp_path)
        req = ProjectRequirements(state=ContextState.REFINING_USER_RESPONSES)
        manager.save(req)
        manager.mark_up_to_date(req)
        assert req.state == ContextState.UP_TO_DATE

    def test_mark_up_to_date_wrong_state_raises(self, tmp_path: Path):
        manager = RequirementsManager(tmp_path)
        req = ProjectRequirements(state=ContextState.QUERYING_USER)
        with pytest.raises(ValueError):
            manager.mark_up_to_date(req)

    def test_start_context_update_advances_state(self, tmp_path: Path):
        manager = RequirementsManager(tmp_path)
        req = ProjectRequirements(state=ContextState.PCB_CHANGED)
        manager.save(req)
        manager.start_context_update(req)
        assert req.state == ContextState.UPDATING_CONTEXT

    def test_start_context_update_wrong_state_raises(self, tmp_path: Path):
        manager = RequirementsManager(tmp_path)
        req = ProjectRequirements(state=ContextState.UP_TO_DATE)
        with pytest.raises(ValueError):
            manager.start_context_update(req)


# ===========================================================================
# Full lifecycle: fresh start → UP_TO_DATE (no questions)
# ===========================================================================


class TestFullLifecycleFreshStart:
    def test_no_questions_path(self, tmp_path: Path):
        """
        GETTING_RAW_CONTEXT
          → GENERATING_REQUIREMENTS
            → UP_TO_DATE
        """
        manager = RequirementsManager(tmp_path)
        req = manager.start_raw_context_generation()
        assert req.state == ContextState.GETTING_RAW_CONTEXT

        manager.set_raw_context(req, "# Raw context")
        assert req.state == ContextState.GENERATING_REQUIREMENTS

        manager.set_auto_context(req, "# Auto context", pending_questions=[])
        assert req.state == ContextState.UP_TO_DATE
        assert req.is_stable

    def test_with_questions_path(self, tmp_path: Path):
        """
        GETTING_RAW_CONTEXT
          → GENERATING_REQUIREMENTS
            → QUERYING_USER
              → REFINING_USER_RESPONSES
                → UP_TO_DATE
        """
        manager = RequirementsManager(tmp_path)
        req = manager.start_raw_context_generation()

        manager.set_raw_context(req, "# Raw context")

        manager.set_auto_context(
            req,
            "# Auto context",
            pending_questions=["What is the board size?"],
        )
        assert req.state == ContextState.QUERYING_USER
        assert req.needs_user_input

        manager.submit_user_answers(req, "# Requirements\n\nBoard: 50x50mm")
        assert req.state == ContextState.REFINING_USER_RESPONSES

        manager.mark_up_to_date(req)
        assert req.state == ContextState.UP_TO_DATE
        assert req.user_requirements == "# Requirements\n\nBoard: 50x50mm"
        assert req.is_stable


# ===========================================================================
# Full lifecycle: UP_TO_DATE → PCB change → update → UP_TO_DATE
# ===========================================================================


class TestFullLifecyclePcbChange:
    def test_pcb_change_update_path(self, tmp_path: Path):
        """
        UP_TO_DATE
          → PCB_CHANGED
            → UPDATING_CONTEXT
              → GENERATING_REQUIREMENTS
                → UP_TO_DATE
        """
        pcb_file = tmp_path / "board.kicad_pcb"
        pcb_file.write_text("original pcb data", encoding="utf-8")

        manager = RequirementsManager(tmp_path)
        req = manager.start_raw_context_generation()
        manager.set_raw_context(req, "# Raw v1")
        manager.set_auto_context(req, "# Context v1", pending_questions=[])
        assert req.state == ContextState.UP_TO_DATE

        # Simulate PCB file change
        pcb_file.write_text("updated pcb data", encoding="utf-8")
        changed = manager.detect_and_handle_pcb_change(req)
        assert changed is True
        assert req.state == ContextState.PCB_CHANGED
        assert req.is_stale

        manager.start_context_update(req)
        assert req.state == ContextState.UPDATING_CONTEXT

        # set_raw_context is also valid from UPDATING_CONTEXT
        manager.set_raw_context(req, "# Raw v2")
        assert req.state == ContextState.GENERATING_REQUIREMENTS

        manager.set_auto_context(req, "# Context v2", pending_questions=[])
        assert req.state == ContextState.UP_TO_DATE
        # Diff should have been computed
        assert req.context_diff != ""

    def test_pcb_change_with_conflict_queries_user(self, tmp_path: Path):
        """
        UP_TO_DATE
          → PCB_CHANGED
            → UPDATING_CONTEXT
              → GENERATING_REQUIREMENTS
                → QUERYING_USER (conflict / missing info)
                  → REFINING_USER_RESPONSES
                    → UP_TO_DATE
        """
        pcb_file = tmp_path / "board.kicad_pcb"
        pcb_file.write_text("v1", encoding="utf-8")

        manager = RequirementsManager(tmp_path)
        req = manager.start_raw_context_generation()
        manager.set_raw_context(req, "# Raw v1")
        manager.set_auto_context(req, "# Context v1", pending_questions=[])

        # Simulate change
        pcb_file.write_text("v2", encoding="utf-8")
        manager.detect_and_handle_pcb_change(req)
        manager.start_context_update(req)
        manager.set_raw_context(req, "# Raw v2")

        # Conflict detected
        manager.set_auto_context(
            req,
            "# Context v2",
            pending_questions=["New component added — confirm power budget?"],
        )
        assert req.state == ContextState.QUERYING_USER

        manager.submit_user_answers(req, "# Approved requirements")
        manager.mark_up_to_date(req)
        assert req.state == ContextState.UP_TO_DATE
        assert req.user_requirements == "# Approved requirements"


# ===========================================================================
# Safeguards
# ===========================================================================


class TestUserRequirementsSafeguards:
    def test_user_requirements_not_modified_by_set_auto_context(self, tmp_path: Path):
        """set_auto_context must never touch user_requirements."""
        manager = RequirementsManager(tmp_path)
        req = ProjectRequirements(
            state=ContextState.GENERATING_REQUIREMENTS,
            user_requirements="# Approved by user",
        )
        manager.save(req)
        manager.set_auto_context(req, "# New auto context", pending_questions=[])
        assert req.user_requirements == "# Approved by user"

    def test_user_requirements_not_modified_by_set_raw_context(self, tmp_path: Path):
        """set_raw_context must never touch user_requirements."""
        manager = RequirementsManager(tmp_path)
        req = ProjectRequirements(
            state=ContextState.GETTING_RAW_CONTEXT,
            user_requirements="# Approved by user",
        )
        manager.save(req)
        manager.set_raw_context(req, "raw context")
        assert req.user_requirements == "# Approved by user"

    def test_submit_user_answers_only_from_querying_user(self, tmp_path: Path):
        """submit_user_answers must only work from QUERYING_USER."""
        manager = RequirementsManager(tmp_path)
        for state in ContextState:
            if state == ContextState.QUERYING_USER:
                continue
            req = ProjectRequirements(state=state)
            with pytest.raises(ValueError):
                manager.submit_user_answers(req, "requirements")


# ===========================================================================
# context package __init__ exports
# ===========================================================================


class TestContextPackageExports:
    def test_new_classes_importable_from_context(self):
        from kiassist_utils.context import (
            ComponentChange,
            ContextState,
            FileChange,
            ProjectRequirements,
            RequirementsManager,
        )
        assert ContextState.UP_TO_DATE.value == "up_to_date"
        assert ProjectRequirements().state == ContextState.GETTING_RAW_CONTEXT
        assert callable(RequirementsManager)
        fc = FileChange(path="x.kicad_pcb", change_type="added", new_hash="h")
        assert fc.change_type == "added"
        cc = ComponentChange(reference="R1", change_type="added", new_value="10k")
        assert cc.change_type == "added"
