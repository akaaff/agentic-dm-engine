"""Issue #76 (class playtest): "attack, then use second wind / cast healing
word / rage / kick" parsed correctly but the bonus action never ran - the main
action ends the turn, so the sequencing loop stopped before it. The parser now
moves every bonus action that follows the first turn-ending action in front of
it (the engine doesn't enforce "after the Attack action")."""

from __future__ import annotations

from src.engine.actions import ParsedAction
from src.graph.nodes.intent_parser import _bonus_actions_first


def _a(verb: str, item: str | None = None, target: str | None = "goblin_1") -> ParsedAction:
    return ParsedAction(
        actor="x",
        verb=verb,  # type: ignore[arg-type]
        item_or_spell=item,
        target=target,
        raw_text="x",
    )


def _verbs(actions: list[ParsedAction]) -> list[str]:
    return [a.verb for a in actions]


def test_a_bonus_action_after_the_attack_moves_in_front_of_it() -> None:
    for bonus in (
        "second_wind",
        "rage",
        "martial_arts_strike",
        "flurry_of_blows",
        "bardic_inspiration",
    ):
        out = _bonus_actions_first([_a("attack"), _a(bonus)])
        assert _verbs(out) == [bonus, "attack"], bonus


def test_a_bonus_action_spell_after_the_attack_moves_but_an_action_spell_does_not() -> None:
    healing_word = _a("cast_spell", "healing word", "buddy")
    assert _verbs(_bonus_actions_first([_a("attack"), healing_word])) == ["cast_spell", "attack"]
    out = _bonus_actions_first([_a("attack"), healing_word])
    assert out[0].item_or_spell == "healing word"

    # Cure Wounds is a full action: stays after the attack (and, like before,
    # never resolves - only one main action per turn).
    cure = _a("cast_spell", "cure wounds", "buddy")
    assert _bonus_actions_first([_a("attack"), cure]) == [_a("attack"), cure]


def test_already_ordered_sequences_are_untouched() -> None:
    seq = [_a("second_wind"), _a("attack")]
    assert _bonus_actions_first(seq) == seq
    only = [_a("attack")]
    assert _bonus_actions_first(only) == only
    assert _bonus_actions_first([]) == []


def test_movement_and_equip_keep_their_place() -> None:
    # "move, attack, rage" -> the move stays first, rage slides before the attack.
    out = _bonus_actions_first([_a("move"), _a("attack"), _a("rage")])
    assert _verbs(out) == ["move", "rage", "attack"]
    # equip is a free interaction and must NOT jump ahead of an attack that
    # depends on the old weapon: "attack with the dagger, then draw the sword".
    out = _bonus_actions_first([_a("attack"), _a("equip")])
    assert _verbs(out) == ["attack", "equip"]


def test_two_late_bonus_actions_keep_their_relative_order() -> None:
    out = _bonus_actions_first([_a("attack"), _a("rage"), _a("second_wind")])
    assert _verbs(out) == ["rage", "second_wind", "attack"]


def test_a_sequence_of_only_bonus_actions_is_untouched() -> None:
    seq = [_a("rage"), _a("second_wind")]
    assert _bonus_actions_first(seq) == seq
