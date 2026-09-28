"""Pure planning logic - see src.observability.watcher's own module
docstring. No real gh/LLM calls anywhere in this file.
"""

from __future__ import annotations

import json
from pathlib import Path

from src.observability.issue_tracker import TrackedIssue
from src.observability.watcher import (
    CommentAction,
    FileAction,
    SignatureState,
    WatcherState,
    group_by_signature,
    plan_actions,
    read_new_records,
)


def _out_of_range_record(target: str) -> dict[str, object]:
    return {
        "kind": "failed_intent",
        "reason": "rejected",
        "detail": f"{target} is 25ft away - out of range for Battleaxe (max 5ft)",
        "produced_action": {"verb": "attack", "item_or_spell": "battleaxe"},
        "timestamp": "2026-09-27T00:00:00+00:00",
    }


def _backend_error_record() -> dict[str, object]:
    return {
        "kind": "backend_error",
        "exc_type": "RuntimeError",
        "message": "boom",
        "traceback": 'File "src/api/ws/session.py", line 944, in x\nRuntimeError: boom',
        "timestamp": "2026-09-27T00:00:00+00:00",
    }


def test_read_new_records_returns_empty_for_a_missing_log(tmp_path: Path) -> None:
    records, new_line = read_new_records(tmp_path / "does-not-exist.jsonl", 0)
    assert records == []
    assert new_line == 0


def test_read_new_records_only_reads_past_the_checkpoint(tmp_path: Path) -> None:
    log_path = tmp_path / "events.jsonl"
    log_path.write_text(
        "\n".join(json.dumps(_out_of_range_record(f"kobold_{i}")) for i in range(3)) + "\n",
        encoding="utf-8",
    )
    records, new_line = read_new_records(log_path, 1)
    assert len(records) == 2
    assert new_line == 3


def test_non_trackable_records_are_filtered_out(tmp_path: Path) -> None:
    log_path = tmp_path / "events.jsonl"
    lines = [
        json.dumps({"kind": "log", "level": "WARNING", "message": "just a heads up"}),
        json.dumps({"kind": "log", "level": "ERROR", "message": "a real problem"}),
        json.dumps({"kind": "mystery"}),
        json.dumps(_out_of_range_record("kobold_1")),
    ]
    log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    records, _ = read_new_records(log_path, 0)
    kinds = [r["kind"] for r in records]
    assert kinds == ["log", "failed_intent"]


def test_group_by_signature_collapses_matching_records() -> None:
    records = [_out_of_range_record("kobold_1"), _out_of_range_record("kobold_3")]
    groups = group_by_signature(records)
    assert len(groups) == 1
    assert len(next(iter(groups.values()))) == 2


def test_plan_actions_below_threshold_files_nothing() -> None:
    groups = group_by_signature([_out_of_range_record("kobold_1")])  # only 1 occurrence
    state = WatcherState()
    actions = plan_actions(groups, state, tracked_issues=[])
    assert actions == []
    signature = next(iter(groups))
    assert state.signatures[signature].total_occurrences == 1


def test_plan_actions_files_once_the_threshold_is_met() -> None:
    groups = group_by_signature(
        [_out_of_range_record("kobold_1"), _out_of_range_record("kobold_3")]
    )
    state = WatcherState()
    actions = plan_actions(groups, state, tracked_issues=[])
    assert len(actions) == 1
    assert isinstance(actions[0], FileAction)
    assert actions[0].total_occurrences == 2
    assert actions[0].kind == "failed_intent"


def test_plan_actions_files_a_backend_error_on_first_occurrence() -> None:
    groups = group_by_signature([_backend_error_record()])
    state = WatcherState()
    actions = plan_actions(groups, state, tracked_issues=[])
    assert len(actions) == 1
    assert isinstance(actions[0], FileAction)
    assert actions[0].total_occurrences == 1


def test_plan_actions_comments_on_an_already_known_issue_instead_of_refiling() -> None:
    groups = group_by_signature([_out_of_range_record("kobold_1")])
    signature = next(iter(groups))
    state = WatcherState()
    state.signatures[signature] = SignatureState(total_occurrences=5, issue_number=66)

    actions = plan_actions(groups, state, tracked_issues=[])
    assert len(actions) == 1
    assert isinstance(actions[0], CommentAction)
    assert actions[0].issue_number == 66
    assert actions[0].new_occurrences == 1
    assert actions[0].total_occurrences == 6


def test_plan_actions_reconciles_against_a_real_github_lookup_not_just_local_cache() -> None:
    # No local record of this signature's issue at all, but it already
    # exists on GitHub (e.g. filed by a previous, since-wiped watcher
    # state) - found via the real lookup, not re-filed.
    groups = group_by_signature(
        [_out_of_range_record("kobold_1"), _out_of_range_record("kobold_2")]
    )
    signature = next(iter(groups))
    state = WatcherState()
    tracked = [TrackedIssue(number=99, title="Out of range battleaxe", signature=signature)]

    actions = plan_actions(groups, state, tracked_issues=tracked)
    assert len(actions) == 1
    assert isinstance(actions[0], CommentAction)
    assert actions[0].issue_number == 99
    assert state.signatures[signature].issue_number == 99


def test_watcher_state_round_trips_through_save_and_load(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    state = WatcherState(last_processed_line=42)
    state.signatures["sig-a"] = SignatureState(
        total_occurrences=3, issue_number=66, first_seen="t0", last_seen="t1"
    )
    state.save(path)

    loaded = WatcherState.load(path)
    assert loaded.last_processed_line == 42
    assert loaded.signatures["sig-a"].total_occurrences == 3
    assert loaded.signatures["sig-a"].issue_number == 66


def test_watcher_state_load_with_no_existing_file_returns_a_fresh_state(tmp_path: Path) -> None:
    state = WatcherState.load(tmp_path / "does-not-exist.json")
    assert state.last_processed_line == 0
    assert state.signatures == {}
