"""_promote_stray_target (graph/nodes/intent_parser.py) is a pure,
deterministic post-processing step - fully testable offline, unlike the
node's own LLM-calling behavior (see tests/llm/test_intent_parser.py's
golden-set case for the live round trip this exists to patch up).
"""

from __future__ import annotations

from src.engine.actions import ParsedAction
from src.graph.nodes.intent_parser import _promote_stray_target


def _cast(**overrides: object) -> ParsedAction:
    defaults: dict[str, object] = {
        "actor": "elrond",
        "verb": "cast_spell",
        "item_or_spell": "acid splash",
        "raw_text": "I cast acid splash at goblin_1",
    }
    defaults.update(overrides)
    return ParsedAction.model_validate(defaults)


def test_promotes_a_stray_params_target_to_the_top_level_field() -> None:
    action = _cast(params={"target": "goblin_1"})

    normalized = _promote_stray_target(action)

    assert normalized.target == "goblin_1"
    assert normalized.verb == "cast_spell"
    assert normalized.item_or_spell == "acid splash"


def test_promotes_a_stray_params_targets_list_to_the_top_level_field() -> None:
    action = _cast(params={"targets": ["goblin_1", "goblin_2"]})

    normalized = _promote_stray_target(action)

    assert normalized.targets == ["goblin_1", "goblin_2"]
    assert normalized.target is None


def test_leaves_an_already_correct_action_unchanged() -> None:
    action = _cast(target="goblin_1")

    normalized = _promote_stray_target(action)

    assert normalized is action


def test_does_not_touch_a_cast_with_no_target_at_all() -> None:
    # A spell with no target (or one the model genuinely didn't recognize)
    # should stay untouched, not have an empty/garbage value invented.
    action = _cast(params={})

    normalized = _promote_stray_target(action)

    assert normalized.target is None
    assert normalized.targets is None


def test_also_promotes_a_stray_params_target_for_a_different_verb() -> None:
    # Issue #49's own investigation found the same nesting quirk hitting
    # "move"/"attack" mid-sequence, not just cast_spell - deliberately
    # verb-agnostic now, since promoting a stray params entry is harmless
    # even for a verb that never reads it back out.
    action = ParsedAction(
        actor="thorin",
        verb="attack",
        raw_text="I attack",
        params={"target": "goblin_1"},
    )

    normalized = _promote_stray_target(action)

    assert normalized.target == "goblin_1"


def test_prefers_the_real_target_field_over_a_stray_params_one() -> None:
    # If the model somehow set both (correctly this time, with a stray
    # leftover in params too), the real field wins and params is untouched.
    action = _cast(target="goblin_1", params={"target": "goblin_2"})

    normalized = _promote_stray_target(action)

    assert normalized.target == "goblin_1"
    assert normalized is action
