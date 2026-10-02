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
from src.graph.nodes.intent_parser import _split_dual_wield_attacks


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
