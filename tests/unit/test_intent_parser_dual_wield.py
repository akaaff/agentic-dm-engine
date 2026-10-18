"""_split_dual_wield_attacks (graph/nodes/intent_parser.py): "I attack wolf 3
with handaxe and scimitar" parsed into two plain `attack` actions, and the
first ends the turn - so the second weapon never swung. A pair of attacks that
names exactly the actor's two equipped weapons is rewritten into the engine's
real shape: an `offhand_attack` (bonus action, doesn't end the turn) placed
before the main `attack`. Pure and deterministic, so tested offline; the real
model's output for the sentence is live-verified separately.
"""

from __future__ import annotations

from src.engine.actions import ParsedAction
from src.engine.position import Position
from src.engine.state import Character, GameState
from src.graph.nodes.intent_parser import _lone_offhand_attack, _split_dual_wield_attacks


def _barbarian(**overrides: object) -> Character:
    defaults: dict[str, object] = {
        "id": "qaf",
        "name": "Qaf",
        "race": "Human",
        "class_": "Barbarian",
        "class_index": "barbarian",
        "background": "Acolyte",
        "is_pc": True,
        "hp": 15,
        "max_hp": 15,
        "ac": 13,
        "position": Position(x=0, y=0),
        "stats": {"STR": 14, "DEX": 12, "CON": 15, "INT": 8, "WIS": 10, "CHA": 8},
        "speed": 30,
        "proficiency_bonus": 2,
        "equipped_weapons": ["handaxe", "scimitar"],
    }
    defaults.update(overrides)
    return Character.model_validate(defaults)


def _state(actor: Character) -> GameState:
    return GameState(
        encounter_id="dual_wield_test",
        characters={actor.id: actor},
        turn_order=[actor.id],
        current_turn=0,
        round=1,
    )


def _attack(weapon: str | None, target: str = "wolf_3") -> ParsedAction:
    return ParsedAction(
        actor="qaf", verb="attack", target=target, item_or_spell=weapon, raw_text="attack"
    )


def test_two_attacks_naming_both_equipped_weapons_become_offhand_then_attack() -> None:
    actor = _barbarian()
    out = _split_dual_wield_attacks([_attack("handaxe"), _attack("scimitar")], _state(actor), "qaf")

    assert [(a.verb, a.item_or_spell, a.target) for a in out] == [
        ("offhand_attack", None, "wolf_3"),
        ("attack", "handaxe", "wolf_3"),
    ]


def test_weapons_named_in_the_other_order_still_put_equipped_slot_1_in_the_offhand() -> None:
    actor = _barbarian()
    out = _split_dual_wield_attacks([_attack("scimitar"), _attack("handaxe")], _state(actor), "qaf")

    assert [(a.verb, a.item_or_spell) for a in out] == [
        ("offhand_attack", None),
        ("attack", "handaxe"),
    ]


def test_each_swing_keeps_its_own_target() -> None:
    actor = _barbarian()
    out = _split_dual_wield_attacks(
        [_attack("handaxe", "wolf_1"), _attack("scimitar", "wolf_2")], _state(actor), "qaf"
    )

    assert [(a.verb, a.target) for a in out] == [("offhand_attack", "wolf_2"), ("attack", "wolf_1")]


def test_surrounding_actions_are_left_in_place() -> None:
    actor = _barbarian()
    rage = ParsedAction(actor="qaf", verb="rage", raw_text="rage")
    out = _split_dual_wield_attacks(
        [rage, _attack("handaxe"), _attack("scimitar")], _state(actor), "qaf"
    )

    assert [a.verb for a in out] == ["rage", "offhand_attack", "attack"]


def test_not_rewritten_when_the_names_arent_exactly_the_two_equipped_weapons() -> None:
    actor = _barbarian()
    # Two swings with the same weapon, or a weapon that isn't equipped: not a
    # dual-wield pair, so the engine's own handling (and errors) apply.
    same = [_attack("handaxe"), _attack("handaxe")]
    other = [_attack("handaxe"), _attack("longsword")]
    assert _split_dual_wield_attacks(same, _state(actor), "qaf") == same
    assert _split_dual_wield_attacks(other, _state(actor), "qaf") == other


def test_not_rewritten_without_exactly_two_equipped_weapons_or_with_the_bonus_action_spent() -> (
    None
):
    pair = [_attack("handaxe"), _attack("scimitar")]
    one_weapon = _barbarian(equipped_weapons=["handaxe"])
    spent = _barbarian(bonus_action_used=True)

    assert _split_dual_wield_attacks(pair, _state(one_weapon), "qaf") == pair
    assert _split_dual_wield_attacks(pair, _state(spent), "qaf") == pair


def test_a_single_attack_is_untouched() -> None:
    actor = _barbarian()
    single = [_attack("handaxe")]
    assert _split_dual_wield_attacks(single, _state(actor), "qaf") == single


# --- a lone off-hand swing (live-found: "I attack with my second hand" ended the turn) ---


def test_an_attack_naming_only_the_off_hand_weapon_becomes_an_offhand_attack() -> None:
    actor = _barbarian()
    out = _lone_offhand_attack([_attack("scimitar")], _state(actor), "qaf", "I swing my scimitar")
    assert [(a.verb, a.item_or_spell, a.target) for a in out] == [
        ("offhand_attack", None, "wolf_3")
    ]


def test_an_attack_naming_the_main_weapon_is_left_alone() -> None:
    actor = _barbarian()
    out = _lone_offhand_attack([_attack("handaxe")], _state(actor), "qaf", "I swing my handaxe")
    assert out[0].verb == "attack"


def test_the_players_words_alone_can_say_off_hand() -> None:
    actor = _barbarian()
    for words in ("I attack with my second hand", "attack with the off-hand", "other hand!"):
        out = _lone_offhand_attack([_attack(None)], _state(actor), "qaf", words)
        assert out[0].verb == "offhand_attack", words


def test_off_hand_words_do_not_override_a_named_main_weapon() -> None:
    actor = _barbarian()
    out = _lone_offhand_attack(
        [_attack("handaxe")], _state(actor), "qaf", "attack with handaxe, not my off hand"
    )
    assert out[0].verb == "attack"


def test_only_the_first_such_attack_is_converted_and_only_once() -> None:
    actor = _barbarian()
    two = [_attack("scimitar"), _attack("scimitar", target="wolf_2")]
    out = _lone_offhand_attack(two, _state(actor), "qaf", "x")
    assert [a.verb for a in out] == ["offhand_attack", "attack"]
    again = _lone_offhand_attack(out, _state(actor), "qaf", "x")
    assert [a.verb for a in again] == ["offhand_attack", "attack"]


def test_nothing_changes_without_two_weapons_or_with_the_bonus_action_spent() -> None:
    one = _barbarian(equipped_weapons=["handaxe"])
    assert _lone_offhand_attack([_attack("scimitar")], _state(one), "qaf", "off hand")[0].verb == (
        "attack"
    )
    spent = _barbarian(bonus_action_used=True)
    assert _lone_offhand_attack([_attack("scimitar")], _state(spent), "qaf", "x")[0].verb == (
        "attack"
    )


def test_two_identical_weapons_are_not_guessed_at_from_the_name_alone() -> None:
    actor = _barbarian(equipped_weapons=["dagger", "dagger"])
    out = _lone_offhand_attack([_attack("dagger")], _state(actor), "qaf", "I stab")
    assert out[0].verb == "attack"
    said = _lone_offhand_attack([_attack(None)], _state(actor), "qaf", "stab with my off hand")
    assert said[0].verb == "offhand_attack"
