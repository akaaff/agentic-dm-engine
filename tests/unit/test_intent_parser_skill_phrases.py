"""Issue #101: in-character skill phrasing the model maps to the wrong skill or to a
plain move. A deterministic pass in the intent parser, so tested offline."""

from __future__ import annotations

import pytest

from src.engine.actions import ParsedAction
from src.engine.rules import normalize_skill_name
from src.graph.nodes.intent_parser import _normalize_skill_phrase


def _check(skill: str) -> ParsedAction:
    return ParsedAction(actor="a", verb="skill_check", params={"skill": skill}, raw_text="x")


def _move() -> ParsedAction:
    return ParsedAction(
        actor="a", verb="move", target="goblin_1", params={"path": [{"x": 1, "y": 0}]}, raw_text="x"
    )


@pytest.mark.parametrize(
    ("utterance", "model_skill", "expected"),
    [
        ("I study the ground to track where the goblins went", "perception", "survival"),
        ("I try to forage for food", "nature", "survival"),
        ("I try to pick the goblin's pocket", "stealth", "sleight-of-hand"),
        ("I palm the key off the table", "stealth", "sleight-of-hand"),
        ("I tumble past the goblin", "athletics", "acrobatics"),
        ("I keep my balance on the beam", "athletics", "acrobatics"),
        ("I make a stealth check to sneak past the goblin", "perception", "stealth"),
    ],
)
def test_a_skill_check_is_corrected_to_the_skill_the_words_name(
    utterance: str, model_skill: str, expected: str
) -> None:
    fixed = _normalize_skill_phrase(_check(model_skill), utterance)
    assert fixed.verb == "skill_check"
    assert fixed.params["skill"] == expected


def test_a_correct_skill_check_is_left_alone() -> None:
    action = _check("Survival")
    assert _normalize_skill_phrase(action, "I track the goblins") == action


@pytest.mark.parametrize(
    ("utterance", "expected"),
    [
        ("I tumble past the goblin", "acrobatics"),
        ("I sneak past the goblin", "stealth"),
        ("I creep quietly past the sleeping guard", "stealth"),
        ("I make a stealth check to sneak past the goblin", "stealth"),
    ],
)
def test_a_move_that_was_really_a_sneak_or_tumble_past_becomes_the_check(
    utterance: str, expected: str
) -> None:
    fixed = _normalize_skill_phrase(_move(), utterance)
    assert fixed.verb == "skill_check"
    assert fixed.params == {"skill": expected}  # the invented path is gone
    assert fixed.target is None


@pytest.mark.parametrize(
    "utterance",
    [
        "I move toward the goblin",
        "I sneak up on the goblin and stab it",  # sneaking TO something is movement
        "I track the goblins",  # a move is never a track/pickpocket
        "I pick the goblin's pocket",
    ],
)
def test_ordinary_moves_are_not_turned_into_skill_checks(utterance: str) -> None:
    move = _move()
    assert _normalize_skill_phrase(move, utterance) == move


def test_other_verbs_are_untouched() -> None:
    attack = ParsedAction(actor="a", verb="attack", target="goblin_1", raw_text="x")
    assert _normalize_skill_phrase(attack, "I tumble past and strike") == attack


def test_skill_names_with_underscores_normalize() -> None:
    # The model sometimes writes sleight_of_hand, which then failed the lookup.
    assert normalize_skill_name("sleight_of_hand") == "sleight-of-hand"
    assert normalize_skill_name("Sleight of Hand") == "sleight-of-hand"
    assert normalize_skill_name("skill-perception") == "perception"


def test_a_pickpocket_parsed_as_using_an_item_becomes_a_sleight_of_hand_check() -> None:
    item = ParsedAction(actor="a", verb="use_item", item_or_spell="pocket", raw_text="x")
    fixed = _normalize_skill_phrase(item, "I try to pick the goblin's pocket")
    assert fixed.verb == "skill_check"
    assert fixed.params == {"skill": "sleight-of-hand"}
    assert fixed.item_or_spell is None
    # ...but drinking a potion is still just using an item.
    potion = ParsedAction(actor="a", verb="use_item", item_or_spell="potion", raw_text="x")
    assert _normalize_skill_phrase(potion, "I drink my potion") == potion
