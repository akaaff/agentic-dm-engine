"""Issue #77 (class playtest): "I punch the goblin" / "unarmed strike" resolved
as whatever weapon the character had equipped - a Monk's punch became their
Dart - because none of those words is an SRD item and a plain `attack` falls
back to the equipped weapon."""

from __future__ import annotations

from src.engine.actions import ParsedAction
from src.engine.character_creation import create_character
from src.engine.encounter import monster_to_character
from src.engine.position import BattleMap, Position
from src.engine.rules import is_unarmed_phrase
from src.engine.srd_loader import load_srd
from src.engine.state import Character, GameState
from src.engine.turn_engine import resolve_action
from src.graph.nodes.intent_parser import _normalize_unarmed_attack


class _FixedRandom:
    def __init__(self, values: list[int]) -> None:
        self._values = list(values)

    def randint(self, a: int, b: int) -> int:
        return self._values.pop(0)


def _monk() -> Character:
    monk = create_character(
        character_id="wen",
        name="Wen",
        race_index="human",
        class_index="monk",
        background_index="acolyte",
        base_ability_scores={"STR": 10, "DEX": 15, "CON": 13, "INT": 8, "WIS": 14, "CHA": 12},
        chosen_skills=["skill-acrobatics", "skill-stealth"],
        position=Position(x=0, y=0),
    )
    assert monk.equipped_weapons == ["dart"]  # the starting kit that caused the bug
    return monk


def _state(actor: Character) -> GameState:
    ogre = monster_to_character(load_srd().monsters["ogre"], "ogre_1", Position(x=1, y=0))
    return GameState(
        encounter_id="unarmed_test",
        characters={actor.id: actor, ogre.id: ogre},
        turn_order=[actor.id, ogre.id],
        current_turn=0,
        round=1,
        battle_map=BattleMap(
            width=5, height=5, terrain=[["floor"] * 5 for _ in range(5)], spawn_points={}
        ),
    )


def _attack(actor: Character, item: str | None) -> list[dict[str, object]]:
    state = _state(actor)
    resolve_action(
        state,
        ParsedAction(
            actor=actor.id, verb="attack", target="ogre_1", item_or_spell=item, raw_text="x"
        ),
        _FixedRandom([14, 3]),  # type: ignore[arg-type]
    )
    return [e.payload for e in state.events if e.type == "attack_roll"]


def test_a_monk_who_punches_throws_an_unarmed_strike_not_the_dart() -> None:
    for phrase in ("punch", "unarmed strike", "kick", "fists"):
        (roll,) = _attack(_monk(), phrase)
        assert roll["source"] == "unarmed strike", phrase


def test_naming_the_equipped_weapon_still_uses_it() -> None:
    (roll,) = _attack(_monk(), "dart")
    assert roll["source"] == "Dart"


def test_a_plain_attack_with_no_weapon_named_still_uses_the_equipped_weapon() -> None:
    (roll,) = _attack(_monk(), None)
    assert roll["source"] == "Dart"


def test_is_unarmed_phrase() -> None:
    for yes in (
        "I punch the goblin",
        "unarmed strike",
        "bare-handed",
        "with my bare hands",
        "KICK",
    ):
        assert is_unarmed_phrase(yes), yes
    for no in ("I attack with my longsword", "a dagger", "", None):
        assert not is_unarmed_phrase(no), no


def _action(item: str | None, raw: str = "x") -> ParsedAction:
    return ParsedAction(
        actor="wen", verb="attack", target="ogre_1", item_or_spell=item, raw_text=raw
    )


def test_parser_step_makes_a_punch_explicit() -> None:
    monk = _monk()
    state = _state(monk)
    out = _normalize_unarmed_attack(_action(None), state, "I punch the ogre")
    assert out.item_or_spell == "unarmed strike"
    out = _normalize_unarmed_attack(_action("punch"), state, "I punch the ogre")
    assert out.item_or_spell == "unarmed strike"


def test_parser_step_leaves_a_named_held_weapon_and_other_verbs_alone() -> None:
    monk = _monk()
    state = _state(monk)
    held = _normalize_unarmed_attack(_action("dart"), state, "I kick the ogre, then throw a dart")
    assert held.item_or_spell == "dart"
    other = ParsedAction(actor="wen", verb="dodge", raw_text="x")
    assert _normalize_unarmed_attack(other, state, "I punch the air") is other
    plain = _normalize_unarmed_attack(_action("dart"), state, "I throw a dart at the ogre")
    assert plain.item_or_spell == "dart"
