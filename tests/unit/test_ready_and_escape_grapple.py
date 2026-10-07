"""Issue #100: two standard actions that were missing. Escape a grapple: Athletics or
Acrobatics (the better) contested by the grappler's Athletics; a win ends `grappled`,
a tie changes nothing. Ready: hold an attack until a hostile moves into reach, then
spend the reaction on it; it lasts until the readier's next turn."""

from __future__ import annotations

import pytest

from src.engine.actions import ParsedAction
from src.engine.character_creation import create_character
from src.engine.conditions import apply_condition, has_condition
from src.engine.encounter import monster_to_character
from src.engine.position import BattleMap, Position
from src.engine.srd_loader import load_srd
from src.engine.state import Character, Condition, GameState
from src.engine.turn_engine import TurnEngineError, resolve_action


class _FixedRandom:
    def __init__(self, values: list[int]) -> None:
        self._values = list(values)

    def randint(self, a: int, b: int) -> int:
        return self._values.pop(0)


def _fighter() -> Character:
    # STR 15 + human 1 = 16 (+3), Athletics proficient (+2): +5 to escape.
    return create_character(
        character_id="thorin",
        name="Thorin",
        race_index="human",
        class_index="fighter",
        background_index="acolyte",
        base_ability_scores={"STR": 15, "DEX": 12, "CON": 14, "INT": 10, "WIS": 13, "CHA": 8},
        chosen_skills=["skill-athletics", "skill-perception"],
        chosen_equipment=["longsword"],
        position=Position(x=0, y=0),
    )


def _goblin(char_id: str, x: int, y: int = 0) -> Character:
    goblin = monster_to_character(load_srd().monsters["goblin"], char_id, Position(x=x, y=y))
    goblin.hp = goblin.max_hp = 100
    return goblin


def _state(*chars: Character) -> GameState:
    return GameState(
        encounter_id="ready_test",
        characters={c.id: c for c in chars},
        turn_order=[c.id for c in chars],
        current_turn=0,
        round=1,
        battle_map=BattleMap(
            width=12, height=12, terrain=[["floor"] * 12 for _ in range(12)], spawn_points={}
        ),
    )


# --- escape a grapple ------------------------------------------------------


def _escape() -> ParsedAction:
    return ParsedAction(actor="thorin", verb="escape_grapple", raw_text="I break free")


def _grappled(grappler: Character | None = None) -> tuple[Character, GameState]:
    fighter = _fighter()
    goblin = grappler or _goblin("goblin_1", 1)
    apply_condition(fighter, Condition(name="grappled", source=goblin.id))
    return fighter, _state(fighter, goblin)


def test_winning_the_contest_breaks_the_grapple_and_uses_the_action() -> None:
    fighter, state = _grappled()
    # Thorin 15 + 5 = 20 vs the goblin's Athletics: 5 + (-1) = 4.
    resolve_action(state, _escape(), _FixedRandom([15, 5]))  # type: ignore[arg-type]
    assert not has_condition(fighter, "grappled")
    event = next(e for e in state.events if e.type == "grapple_escape")
    assert event.payload["success"] is True
    assert (event.payload["actor_total"], event.payload["grappler_total"]) == (20, 4)
    assert state.turn_order[state.current_turn] == "goblin_1"


def test_losing_or_tying_leaves_the_grapple_in_place() -> None:
    fighter, state = _grappled()
    resolve_action(state, _escape(), _FixedRandom([1, 20]))  # type: ignore[arg-type]
    assert has_condition(fighter, "grappled")
    assert next(e for e in state.events if e.type == "grapple_escape").payload["success"] is False

    fighter, state = _grappled()
    # 6 + 5 = 11 vs 12 + (-1) = 11: a tie changes nothing.
    resolve_action(state, _escape(), _FixedRandom([6, 12]))  # type: ignore[arg-type]
    assert has_condition(fighter, "grappled")


def test_a_dead_grappler_cannot_hold_on() -> None:
    goblin = _goblin("goblin_1", 1)
    goblin.is_dead = True
    fighter, state = _grappled(goblin)
    resolve_action(state, _escape(), _FixedRandom([]))  # type: ignore[arg-type]
    assert not has_condition(fighter, "grappled")


def test_escaping_when_not_grappled_is_rejected() -> None:
    state = _state(_fighter(), _goblin("goblin_1", 5))
    with pytest.raises(TurnEngineError, match="isn't grappled"):
        resolve_action(state, _escape(), _FixedRandom([]))  # type: ignore[arg-type]


# --- ready -----------------------------------------------------------------


def _ready(target: str | None = None) -> ParsedAction:
    return ParsedAction(
        actor="thorin", verb="ready", target=target, item_or_spell="longsword", raw_text="I ready"
    )


def _goblin_moves(to_x: int, start_x: int, char_id: str = "goblin_1") -> ParsedAction:
    xs = range(start_x - 1, to_x - 1, -1)
    return ParsedAction(
        actor=char_id,
        verb="move",
        params={"path": [{"x": x, "y": 0} for x in xs]},
        raw_text="the goblin closes in",
    )


def test_a_readied_attack_fires_when_a_hostile_moves_into_reach() -> None:
    fighter = _fighter()
    goblin = _goblin("goblin_1", 3)
    state = _state(fighter, goblin)
    resolve_action(state, _ready(), _FixedRandom([]))  # type: ignore[arg-type]
    assert fighter.readied_attack == {"target": None, "weapon": "longsword"}

    # The goblin walks from 3 to 1 (adjacent): the readied swing happens.
    resolve_action(state, _goblin_moves(1, 3), _FixedRandom([15, 5]))  # type: ignore[arg-type]
    triggered = next(e for e in state.events if e.type == "readied_attack")
    assert triggered.actor == "thorin"
    attack = next(e for e in state.events if e.type == "attack_roll")
    assert attack.actor == "thorin" and attack.payload["target"] == "goblin_1"
    assert fighter.readied_attack is None
    assert fighter.reaction_used_this_round is True


def test_it_does_not_fire_for_a_creature_that_was_already_in_reach() -> None:
    fighter = _fighter()
    goblin = _goblin("goblin_1", 1)
    state = _state(fighter, goblin)
    resolve_action(state, _ready(), _FixedRandom([]))  # type: ignore[arg-type]
    shuffle = ParsedAction(
        actor="goblin_1", verb="move", params={"path": [{"x": 1, "y": 1}]}, raw_text="shuffles"
    )
    resolve_action(state, shuffle, _FixedRandom([]))  # type: ignore[arg-type]
    assert not any(e.type == "readied_attack" for e in state.events)
    assert fighter.readied_attack is not None  # still waiting


def test_a_named_target_only_triggers_on_that_creature() -> None:
    fighter = _fighter()
    state = _state(fighter, _goblin("goblin_1", 3), _goblin("goblin_2", 6))
    resolve_action(state, _ready("goblin_2"), _FixedRandom([]))  # type: ignore[arg-type]
    resolve_action(state, _goblin_moves(1, 3), _FixedRandom([]))  # type: ignore[arg-type]
    assert not any(e.type == "readied_attack" for e in state.events)


def test_a_spent_reaction_means_no_readied_attack() -> None:
    fighter = _fighter()
    state = _state(fighter, _goblin("goblin_1", 3))
    resolve_action(state, _ready(), _FixedRandom([]))  # type: ignore[arg-type]
    fighter.reaction_used_this_round = True
    resolve_action(state, _goblin_moves(1, 3), _FixedRandom([]))  # type: ignore[arg-type]
    assert not any(e.type == "readied_attack" for e in state.events)


def test_a_readied_attack_lapses_at_the_readiers_next_turn() -> None:
    fighter = _fighter()
    state = _state(fighter, _goblin("goblin_1", 9))
    resolve_action(state, _ready(), _FixedRandom([]))  # type: ignore[arg-type]
    resolve_action(
        state,
        ParsedAction(actor="goblin_1", verb="end_turn", raw_text="waits"),
        _FixedRandom([]),  # type: ignore[arg-type]
    )
    assert state.turn_order[state.current_turn] == "thorin"
    assert fighter.readied_attack is None


def test_you_cannot_ready_an_attack_on_an_ally() -> None:
    fighter = _fighter()
    ally = _fighter()
    ally.id = "grom"
    state = _state(fighter, _goblin("goblin_1", 5), ally)
    with pytest.raises(TurnEngineError, match="living enemy"):
        resolve_action(state, _ready("grom"), _FixedRandom([]))  # type: ignore[arg-type]
