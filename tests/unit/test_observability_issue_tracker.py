"""Pure-logic tests for src.observability.issue_tracker - find_existing/
extract_signature never touch a subprocess, so these stay fully offline.
list_tracked_issues (the real `gh issue list` call) is live-verification
only, matching this project's stance on real external calls.
"""

from __future__ import annotations

from src.observability.issue_tracker import (
    SIGNATURE_MARKER_TEMPLATE,
    TrackedIssue,
    extract_signature,
    find_existing,
)


def test_extract_signature_finds_the_hidden_marker() -> None:
    body = f"Some description.\n\n{SIGNATURE_MARKER_TEMPLATE.format(signature='foo:bar')}\n"
    assert extract_signature(body) == "foo:bar"


def test_extract_signature_returns_none_without_a_marker() -> None:
    assert extract_signature("A perfectly ordinary hand-filed issue, no marker at all.") is None


def test_extract_signature_returns_none_for_an_empty_body() -> None:
    assert extract_signature(None) is None
    assert extract_signature("") is None


def test_find_existing_matches_by_exact_signature() -> None:
    tracked = [
        TrackedIssue(number=66, title="Out of range battleaxe", signature="failed_intent:a"),
        TrackedIssue(number=67, title="Some other pattern", signature="failed_intent:b"),
    ]
    assert find_existing("failed_intent:a", tracked) == 66
    assert find_existing("failed_intent:b", tracked) == 67


def test_find_existing_returns_none_when_nothing_matches() -> None:
    tracked = [TrackedIssue(number=66, title="Out of range battleaxe", signature="failed_intent:a")]
    assert find_existing("failed_intent:zzz", tracked) is None


def test_find_existing_against_an_empty_tracked_list() -> None:
    assert find_existing("anything", []) is None
