"""Standalone log-watching service - reads the centralized structured log
(src.observability.log_event, data/logs/events.jsonl) since its last
checkpoint, and for each recurring failure pattern either files a new
GitHub issue or comments on the one it already filed for that exact
pattern (bumping its occurrence count, the real severity signal - never
auto-scored, always left for a human to weigh during review).

Lives under src/cli/ (not scripts/, which is reserved for standalone
tools with no src.* dependency - see scripts/download_srd.py) since this
imports the app's own modules and needs `-m` invocation for that to
resolve, matching every other CLI entry point in this project.
Deliberately still meant to run independent of any particular Claude Code
session or live game session, e.g. under Windows Task Scheduler
(`--once`, exits when done) or in a terminal the user starts and leaves
running (`--loop --interval-seconds N`).

Stops at filing/commenting - no auto-fix, no auto-PR, matching the user's
own explicit choice for this initiative. `--dry-run` (the default) prints
what it would do without ever calling `gh issue create`/`gh issue
comment` or persisting the checkpoint/occurrence state - filing a real,
visible issue against a real shared repo is exactly the kind of hard-to-
reverse, other-people-visible action that needs an explicit `--live` flag,
not a script's silent default.

Usage:
    uv run python -m src.cli.log_watcher --dry-run --once
    uv run python -m src.cli.log_watcher --live --once
    uv run python -m src.cli.log_watcher --live --loop --interval-seconds 600
"""

from __future__ import annotations

import argparse
import re
import subprocess
import time

from src.llm.providers import chat_english_only
from src.observability.issue_tracker import (
    SIGNATURE_MARKER_TEMPLATE,
    WATCHER_LABEL,
    list_tracked_issues,
)
from src.observability.log_event import EVENTS_LOG_PATH
from src.observability.watcher import (
    Action,
    CommentAction,
    FileAction,
    WatcherState,
    group_by_signature,
    plan_actions,
    read_new_records,
)

_ISSUE_URL_NUMBER = re.compile(r"/issues/(\d+)\s*$")


def _draft_issue_body(action: FileAction) -> str:
    """The LLM's only job here is turning already-extracted, deterministic
    facts into readable prose - not deciding what's a duplicate (that's
    compute_signature/find_existing, both pure Python) or how severe
    something is (occurrence count, surfaced as-is). Same "give the model
    pre-computed facts, let it narrate" split this project's own narrator
    node already uses."""
    example = action.example
    facts = "\n".join(f"- {k}: {v}" for k, v in example.items() if k not in ("timestamp", "kind"))
    prompt = (
        "Write a short, clear GitHub issue body (plain markdown, a couple "
        "of paragraphs at most) describing a recurring problem detected in "
        "an automated log-analysis pass of a D&D game engine's live play "
        "sessions. Do not invent details beyond what's given below - just "
        "explain what's happening and, if it's obvious from the facts, a "
        "likely cause. Do not include a title line.\n\n"
        f"Record kind: {action.kind}\n"
        f"Occurrences seen so far: {action.total_occurrences}\n"
        f"A representative example:\n{facts}"
    )
    body = chat_english_only(messages=[{"role": "user", "content": prompt}], temperature=0.4)
    marker = SIGNATURE_MARKER_TEMPLATE.format(signature=action.signature)
    return f"{body.strip()}\n\n---\nOccurrences: {action.total_occurrences}\n{marker}\n"


def _issue_title(action: FileAction) -> str:
    example = action.example
    if action.kind == "backend_error":
        return f"[auto-detected] {example.get('exc_type', 'Error')} in live play"
    if action.kind == "failed_intent":
        produced = example.get("produced_action") or {}
        verb = produced.get("verb", "action")
        return f"[auto-detected] Recurring failed intent: {verb} ({example.get('reason', '')})"
    return f"[auto-detected] Recurring {action.kind} in the log"


def _comment_body(action: CommentAction) -> str:
    example = action.example
    detail = example.get("detail") or example.get("message") or ""
    return (
        f"+{action.new_occurrences} more occurrence(s) since last check "
        f"(total: {action.total_occurrences}).\n\nMost recent example: {detail}"
    )


def _execute(action: Action, live: bool) -> int | None:
    """Returns the real or would-be issue number for a FileAction (used to
    update local state), or None for a CommentAction."""
    if isinstance(action, FileAction):
        title = _issue_title(action)
        if not live:
            print(f"[dry-run] would file issue: {title!r} (occurrences={action.total_occurrences})")
            return None
        body = _draft_issue_body(action)
        result = subprocess.run(
            ["gh", "issue", "create", "--title", title, "--body", body, "--label", WATCHER_LABEL],
            capture_output=True,
            text=True,
            check=True,
        )
        url = result.stdout.strip()
        match = _ISSUE_URL_NUMBER.search(url)
        number = int(match.group(1)) if match else None
        print(f"Filed {url}")
        return number

    if not live:
        print(
            f"[dry-run] would comment on #{action.issue_number}: "
            f"+{action.new_occurrences} occurrence(s) (total {action.total_occurrences})"
        )
        return None
    subprocess.run(
        ["gh", "issue", "comment", str(action.issue_number), "--body", _comment_body(action)],
        capture_output=True,
        text=True,
        check=True,
    )
    print(f"Commented on #{action.issue_number}")
    return None


def run_once(live: bool) -> None:
    state = WatcherState.load()
    records, new_last_line = read_new_records(EVENTS_LOG_PATH, state.last_processed_line)
    if not records:
        print("No new trackable log records since last check.")
        return

    groups = group_by_signature(records)
    tracked_issues = list_tracked_issues()
    actions = plan_actions(groups, state, tracked_issues)

    print(
        f"{len(records)} new record(s) across {len(groups)} signature(s), {len(actions)} action(s)."
    )
    for action in actions:
        number = _execute(action, live)
        if isinstance(action, FileAction) and number is not None:
            state.signatures[action.signature].issue_number = number

    state.last_processed_line = new_last_line
    if live:
        state.save()
    else:
        print(f"[dry-run] would checkpoint at line {new_last_line} (state not saved)")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--live",
        dest="live",
        action="store_true",
        help="Actually call gh issue create/comment and persist checkpoint/occurrence state.",
    )
    parser.add_argument(
        "--dry-run",
        dest="live",
        action="store_false",
        help="Print what would happen without touching GitHub or saved state (the default).",
    )
    parser.set_defaults(live=False)
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run a single pass and exit - the default whenever --loop isn't given.",
    )
    parser.add_argument(
        "--loop",
        action="store_true",
        help="Run forever, sleeping --interval-seconds between passes.",
    )
    parser.add_argument("--interval-seconds", type=int, default=600)
    args = parser.parse_args()

    if args.loop:
        while True:
            run_once(live=args.live)
            time.sleep(args.interval_seconds)
    else:
        run_once(live=args.live)


if __name__ == "__main__":
    main()
