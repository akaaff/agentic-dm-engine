"""Issue #86 (class playtest): the Paladin's level-1 Lay on Hands - a pool of
5 healing points per paladin level, spent by touch - was missing entirely
("I lay hands on Buddy" parsed as use_item and was rejected)."""

from __future__ import annotations

import pytest

from src.engine.actions import ParsedAction
from src.engine.character_creation import create_character, level_up
from src.engine.conditions import apply_condition, has_condition
from src.engine.encounter import monster_to_character
from src.engine.position import BattleMap, Position
from src.engine.resting import apply_long_rest, apply_short_rest
from src.engine.srd_loader import load_srd
from src.engine.state import Character, Condition, GameState
from src.engine.turn_engine import TurnEngineError, resolve_action


class _FixedRandom:
    def __init__(self, values: list[int]) -> None:
        self._values = list(values)

    def randint(self, a: int, b: int) -> int:
        return self._values.pop(0)


def _paladin(x: int = 0) -> Character:
    return create_character(
        character_id="aurelio",
        name="Aurelio",
        race_index="human",
        class_index="paladin",
        background_index="acolyte",
        base_ability_scores={"STR": 15, "DEX": 8, "CON": 13, "INT": 10, "WIS": 12, "CHA": 14},
        chosen_skills=["skill-athletics", "skill-religion"],
        chosen_equipment=["longsword", "shield", "chain-mail"],
        position=Position(x=x, y=0),
    )


def _fighter(x: int = 1) -> Character:
    return create_character(
        character_id="brannock",
        name="Brannock",
        race_index="human",
        class_index="fighter",
        background_index="acolyte",
        base_ability_scores={"STR": 15, "DEX": 13, "CON": 14, "INT": 8, "WIS": 12, "CHA": 10},
        chosen_skills=["skill-athletics", "skill-perception"],
        chosen_equipment=["longsword"],
        position=Position(x=x, y=0),
    )


def _state(*chars: Character) -> GameState:
    # A living monster keeps the encounter in progress (with none, the engine
    # declares an instant victory and stops advancing turns).
    kobold = monster_to_character(load_srd().monsters["kobold"], "kobold_1", Position(x=7, y=7))
    return GameState(
        encounter_id="t",
        characters={c.id: c for c in (*chars, kobold)},
        turn_order=[c.id for c in (*chars, kobold)],
        current_turn=0,
        round=1,
        battle_map=BattleMap(
            width=8, height=8, terrain=[["floor"] * 8 for _ in range(8)], spawn_points={}
        ),
    )


def _lay(state: GameState, target: str | None, **params: object) -> None:
    resolve_action(
        state,
        ParsedAction(
            actor="aurelio", verb="lay_on_hands", target=target, params=params, raw_text="x"
        ),
        _FixedRandom([]),  # type: ignore[arg-type]
    )


def test_a_level_1_paladin_starts_with_a_pool_of_five() -> None:
    assert _paladin().class_resources == {"lay_on_hands": 5}


def test_the_pool_grows_five_per_level() -> None:
    paladin = _paladin()
    level_up(paladin, load_srd())
    assert paladin.class_resources["lay_on_hands"] == 10


def test_lay_on_hands_heals_up_to_the_pool_and_spends_it() -> None:
    paladin, ally = _paladin(), _fighter()
    ally.hp = 2
    state = _state(paladin, ally)
    max_missing = ally.max_hp - ally.hp
    assert max_missing > 5  # the pool, not the wound, is the limit here

    _lay(state, "brannock")

    assert ally.hp == 7
    assert paladin.class_resources["lay_on_hands"] == 0
    event = next(e for e in state.events if e.type == "hp_change")
    assert event.payload["source"] == "Lay on Hands"
    assert event.payload["amount"] == 5
    assert event.payload["pool_remaining"] == 0
    assert state.turn_order[state.current_turn] == "brannock"  # an action: the turn ended


def test_it_only_spends_what_the_wound_needs() -> None:
    paladin, ally = _paladin(), _fighter()
    ally.hp = ally.max_hp - 3
    state = _state(paladin, ally)

    _lay(state, "brannock")

    assert ally.hp == ally.max_hp
    assert paladin.class_resources["lay_on_hands"] == 2


def test_an_explicit_amount_is_honoured_and_capped_by_the_pool() -> None:
    paladin, ally = _paladin(), _fighter()
    ally.hp = 1
    state = _state(paladin, ally)

    _lay(state, "brannock", amount=2)
    assert ally.hp == 3 and paladin.class_resources["lay_on_hands"] == 3

    state.current_turn = 0
    _lay(state, "brannock", amount=99)
    assert ally.hp == 6 and paladin.class_resources["lay_on_hands"] == 0


def test_no_target_means_the_paladin_themself() -> None:
    paladin = _paladin()
    paladin.hp = paladin.max_hp - 4
    state = _state(paladin, _fighter())

    _lay(state, None)

    assert paladin.hp == paladin.max_hp
    assert paladin.class_resources["lay_on_hands"] == 1


def test_it_brings_a_downed_ally_back_up() -> None:
    paladin, ally = _paladin(), _fighter()
    ally.hp = 0
    apply_condition(ally, Condition(name="unconscious", source="0 HP"))
    ally.death_save_failures = 2
    state = _state(paladin, ally)

    _lay(state, "brannock")

    assert ally.hp == 5
    assert not has_condition(ally, "unconscious")
    assert ally.death_save_failures == 0


@pytest.mark.parametrize(
    ("setup", "message"),
    [
        (lambda p, a, s: setattr(a, "hp", a.max_hp), "full hit points"),
        (lambda p, a, s: setattr(a, "is_pc", False), "ally or yourself"),
        (lambda p, a, s: setattr(a, "position", Position(x=4, y=0)), "within 5ft"),
        (lambda p, a, s: p.class_resources.update(lay_on_hands=0), "no Lay on Hands points"),
        (lambda p, a, s: setattr(a, "is_dead", True), "already dead"),
    ],
)
def test_illegal_uses_are_rejected_and_spend_nothing(setup, message: str) -> None:  # type: ignore[no-untyped-def]
    paladin, ally = _paladin(), _fighter()
    ally.hp = 2
    state = _state(paladin, ally)
    setup(paladin, ally, state)
    pool_before = paladin.class_resources["lay_on_hands"]

    with pytest.raises(TurnEngineError, match=message):
        _lay(state, "brannock")

    assert paladin.class_resources["lay_on_hands"] == pool_before
    assert state.turn_order[state.current_turn] == "aurelio"


def test_a_character_without_the_feature_cannot_use_it() -> None:
    fighter = _fighter()
    paladin = _paladin(x=1)
    state = _state(fighter, paladin)
    with pytest.raises(TurnEngineError, match="no Lay on Hands"):
        resolve_action(
            state,
            ParsedAction(actor="brannock", verb="lay_on_hands", target="aurelio", raw_text="x"),
            _FixedRandom([]),  # type: ignore[arg-type]
        )


def test_a_long_rest_refills_the_pool_but_a_short_rest_does_not() -> None:
    paladin = _paladin()
    paladin.class_resources["lay_on_hands"] = 0

    apply_short_rest([paladin], _FixedRandom([5]))  # type: ignore[arg-type]
    assert paladin.class_resources["lay_on_hands"] == 0

    apply_long_rest([paladin])
    assert paladin.class_resources["lay_on_hands"] == 5

    level_up(paladin, load_srd())
    paladin.class_resources["lay_on_hands"] = 0
    apply_long_rest([paladin])
    assert paladin.class_resources["lay_on_hands"] == 10
