"""compute_signature is the actual duplicate-detection decision the log
watcher makes - see src.observability.signatures' own module docstring
for why this is deterministic Python, not fuzzy LLM judgment.
"""

from __future__ import annotations

from src.observability.signatures import compute_signature


def test_two_out_of_range_failures_differing_only_by_actor_and_distance_share_a_signature() -> None:
    first = {
        "kind": "failed_intent",
        "reason": "rejected",
        "detail": "kobold_1 is 25ft away - out of range for Battleaxe (max 5ft)",
        "produced_action": {"verb": "attack", "item_or_spell": "battleaxe"},
    }
    second = {
        "kind": "failed_intent",
        "reason": "rejected",
        "detail": "kobold_3 is 40ft away - out of range for Battleaxe (max 5ft)",
        "produced_action": {"verb": "attack", "item_or_spell": "battleaxe"},
    }
    assert compute_signature(first) == compute_signature(second)


def test_a_different_rejection_reason_gets_a_different_signature() -> None:
    rejected = {
        "kind": "failed_intent",
        "reason": "rejected",
        "detail": "kobold_1 is 25ft away - out of range for Battleaxe (max 5ft)",
        "produced_action": {"verb": "attack", "item_or_spell": "battleaxe"},
    }
    unparseable = {
        "kind": "failed_intent",
        "reason": "unparseable",
        "detail": None,
        "produced_action": {"verb": "invalid"},
    }
    assert compute_signature(rejected) != compute_signature(unparseable)


def test_a_different_weapon_gets_a_different_signature() -> None:
    battleaxe = {
        "kind": "failed_intent",
        "reason": "rejected",
        "detail": "kobold_1 is 25ft away - out of range for Battleaxe (max 5ft)",
        "produced_action": {"verb": "attack", "item_or_spell": "battleaxe"},
    }
    longsword = {
        "kind": "failed_intent",
        "reason": "rejected",
        "detail": "kobold_1 is 25ft away - out of range for Longsword (max 5ft)",
        "produced_action": {"verb": "attack", "item_or_spell": "longsword"},
    }
    assert compute_signature(battleaxe) != compute_signature(longsword)


def _fake_traceback(own_line: int) -> str:
    # A realistic shape: a library frame (excluded - .venv/site-packages),
    # then this project's own frame where the exception was actually
    # raised (the one the signature is built from), then the exception
    # line itself.
    return (
        "Traceback (most recent call last):\n"
        '  File "C:\\Users\\yakra\\projects\\agentic-dm-engine\\.venv\\Lib\\'
        'site-packages\\starlette\\routing.py", line 44, in app\n'
        "    await func(session)\n"
        '  File "C:\\Users\\yakra\\projects\\agentic-dm-engine\\src\\api\\ws\\'
        f'session.py", line {own_line}, in _handle_client_message\n'
        "    actor = session.game_state.characters[action.actor]\n"
        "KeyError: 'companion_grom'\n"
    )


def test_backend_errors_at_the_same_code_frame_share_a_signature_despite_different_messages() -> (
    None
):
    # A KeyError's own message is just the missing key (no digit at all,
    # e.g. "companion_grom") - _normalize alone can't collapse that, which
    # is exactly why backend_error signatures are built from the
    # traceback's own deepest in-repo frame instead.
    first = {
        "kind": "backend_error",
        "exc_type": "KeyError",
        "message": "'companion_grom'",
        "traceback": _fake_traceback(944),
    }
    second = {
        "kind": "backend_error",
        "exc_type": "KeyError",
        "message": "'companion_silvana'",
        "traceback": _fake_traceback(944),
    }
    assert compute_signature(first) == compute_signature(second)


def test_backend_errors_at_a_different_code_frame_get_different_signatures() -> None:
    first = {
        "kind": "backend_error",
        "exc_type": "KeyError",
        "message": "boom",
        "traceback": _fake_traceback(944),
    }
    second = {
        "kind": "backend_error",
        "exc_type": "KeyError",
        "message": "boom",
        "traceback": _fake_traceback(1372),
    }
    assert compute_signature(first) != compute_signature(second)


def test_backend_error_falls_back_to_the_message_with_no_usable_traceback() -> None:
    first = {"kind": "backend_error", "exc_type": "RuntimeError", "message": "boom at kobold_1"}
    second = {"kind": "backend_error", "exc_type": "RuntimeError", "message": "boom at kobold_3"}
    assert compute_signature(first) == compute_signature(second)


def test_log_records_ignore_the_varying_traceback_tail() -> None:
    first = {
        "kind": "log",
        "logger": "src.audiogen.service",
        "level": "ERROR",
        "message": "Kokoro narration audio generation failed - skipping\nTraceback line 12",
    }
    second = {
        "kind": "log",
        "logger": "src.audiogen.service",
        "level": "ERROR",
        "message": "Kokoro narration audio generation failed - skipping\nTraceback line 98",
    }
    assert compute_signature(first) == compute_signature(second)


def test_an_unknown_kind_does_not_collide_with_a_known_one() -> None:
    assert compute_signature({"kind": "mystery"}) != compute_signature({"kind": "failed_intent"})
