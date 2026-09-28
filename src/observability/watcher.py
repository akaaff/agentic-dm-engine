"""Pure planning logic for scripts/log_watcher.py - reading new log
records since the last checkpoint, grouping them by signature (see
signatures.py), and deciding what to do about each group: file a new
GitHub issue, comment on an already-known one (bumping its occurrence
count - the actual severity signal, surfaced for a human to weigh, never
auto-scored), or do nothing yet (still below the recurrence threshold).

Kept separate from the CLI script itself so every decision is testable
without a real `gh` call, a real log file, or a real LLM call -
scripts/log_watcher.py is a thin wrapper: load state, call these
functions, execute whatever they decide (gh subprocess calls, an LLM-
drafted issue body), save state. The same "extract the pure decision,
keep I/O in one thin wrapper" split this project applies everywhere else.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.observability.issue_tracker import TrackedIssue, find_existing
from src.observability.signatures import compute_signature

WATCHER_STATE_PATH = Path("data/logs/.watcher_state.json")

# A single occurrence of ordinary LLM sampling noise (an occasional
# misparse) isn't worth a filed issue on its own - this project's own
# long-standing stance (documented throughout CLAUDE.md) is that
# occasional noise isn't chased to zero, only a genuinely *recurring*
# pattern is actionable. A backend crash is always worth flagging, even
# once - it's a real bug, not sampling variance.
_MIN_OCCURRENCES_BEFORE_FILING = {"failed_intent": 2, "log": 2, "backend_error": 1}

# logging_setup only attaches its handler at WARNING+, but a plain
# WARNING is often just an accepted, already-documented degradation
# (e.g. "not enough VRAM, skipping narration audio") - not a bug worth
# tracking occurrences of the way an ERROR/CRITICAL is.
_TRACKED_LOG_LEVELS = {"ERROR", "CRITICAL"}


@dataclass
class SignatureState:
    total_occurrences: int = 0
    issue_number: int | None = None
    first_seen: str | None = None
    last_seen: str | None = None


@dataclass
class WatcherState:
    last_processed_line: int = 0
    signatures: dict[str, SignatureState] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path = WATCHER_STATE_PATH) -> WatcherState:
        if not path.exists():
            return cls()
        raw = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            last_processed_line=raw.get("last_processed_line", 0),
            signatures={
                sig: SignatureState(**fields) for sig, fields in raw.get("signatures", {}).items()
            },
        )

    def save(self, path: Path = WATCHER_STATE_PATH) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "last_processed_line": self.last_processed_line,
            "signatures": {sig: vars(s) for sig, s in self.signatures.items()},
        }
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _is_trackable(record: dict[str, Any]) -> bool:
    kind = record.get("kind")
    if kind in ("failed_intent", "backend_error"):
        return True
    if kind == "log":
        return record.get("level") in _TRACKED_LOG_LEVELS
    return False


def read_new_records(log_path: Path, last_processed_line: int) -> tuple[list[dict[str, Any]], int]:
    """Every trackable record appended since `last_processed_line`, plus
    the new line count to checkpoint at. A missing log file (the app has
    never logged anything yet) is a harmless no-op, not an error."""
    if not log_path.exists():
        return [], last_processed_line
    lines = log_path.read_text(encoding="utf-8").splitlines()
    new_lines = lines[last_processed_line:]
    records = [json.loads(line) for line in new_lines if line.strip()]
    trackable = [r for r in records if _is_trackable(r)]
    return trackable, len(lines)


def group_by_signature(records: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        groups.setdefault(compute_signature(record), []).append(record)
    return groups


@dataclass(frozen=True)
class FileAction:
    signature: str
    kind: str
    total_occurrences: int
    example: dict[str, Any]


@dataclass(frozen=True)
class CommentAction:
    signature: str
    issue_number: int
    new_occurrences: int
    total_occurrences: int
    example: dict[str, Any]


Action = FileAction | CommentAction


def plan_actions(
    groups: dict[str, list[dict[str, Any]]],
    state: WatcherState,
    tracked_issues: list[TrackedIssue],
) -> list[Action]:
    """For each signature seen this run: comment on its already-known
    issue (local cache first, falling back to a real GitHub lookup so a
    manually-closed/edited issue is reconciled rather than trusted
    blindly forever - see issue_tracker.find_existing), file a new one
    once its recurrence threshold is met, or do nothing yet (still
    tracked locally so the *next* run's count picks up where this one
    left off - the occurrence isn't lost, just not actionable alone).

    Mutates `state.signatures` in place with the updated counts - the
    caller decides whether to persist `state` afterward (a --dry-run
    should not, so repeated dry-run inspection stays consistent)."""
    actions: list[Action] = []
    for signature, new_records in groups.items():
        sig_state = state.signatures.setdefault(signature, SignatureState())
        sig_state.total_occurrences += len(new_records)
        example = new_records[-1]
        sig_state.last_seen = example.get("timestamp")
        if sig_state.first_seen is None:
            sig_state.first_seen = new_records[0].get("timestamp")

        issue_number = sig_state.issue_number or find_existing(signature, tracked_issues)
        if issue_number is not None:
            sig_state.issue_number = issue_number
            actions.append(
                CommentAction(
                    signature=signature,
                    issue_number=issue_number,
                    new_occurrences=len(new_records),
                    total_occurrences=sig_state.total_occurrences,
                    example=example,
                )
            )
            continue

        kind = example.get("kind", "unknown")
        threshold = _MIN_OCCURRENCES_BEFORE_FILING.get(kind, 2)
        if sig_state.total_occurrences >= threshold:
            actions.append(
                FileAction(
                    signature=signature,
                    kind=kind,
                    total_occurrences=sig_state.total_occurrences,
                    example=example,
                )
            )
        # else: below threshold - tracked locally, nothing actionable yet.
    return actions
