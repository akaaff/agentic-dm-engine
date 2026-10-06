"""Issue #82 (class playtest): nothing ever removed `prone`, so a shoved
creature had disadvantage on every attack - and granted advantage to every
melee attacker - for the rest of the fight. A prone creature now stands at the
start of its turn for half its speed."""

from src.cli.play import build_demo_encounter
from src.engine.actions import ParsedAction
from src.engine.character_creation import create_character
from src.engine.conditions import apply_condition, has_condition
from src.engine.encounter import build_encounter_state
from src.engine.state import Condition, GameState
from src.engine.turn_engine import resolve_action


class _FixedRandom:
    def __init__(self, values: list[int]) -> None:
        self._values = list(values)

    def randint(self, a: int, b: int) -> int:
        return self._values.pop(0)


def _state() -> GameState:
    thorin = create_character(
        character_id="thorin",
        name="Thorin",
        race_index="human",
        class_index="fighter",
        background_index="acolyte",
        base_ability_scores={"STR": 15, "DEX": 14, "CON": 13, "INT": 12, "WIS": 10, "CHA": 8},
        chosen_skills=["skill-athletics", "skill-perception"],
        chosen_equipment=["longsword"],
    )
    elrond = create_character(
        character_id="elrond",
        name="Elrond",
        race_index="elf",
        class_index="wizard",
        background_index="acolyte",
        base_ability_scores={"STR": 8, "DEX": 14, "CON": 12, "INT": 15, "WIS": 13, "CHA": 10},
        chosen_skills=["skill-arcana", "skill-history"],
        chosen_prepared_spells=["magic-missile", "burning-hands", "mage-armor"],
        chosen_equipment=["dagger"],
    )
    # turn order: thorin, elrond, goblin_1, goblin_2
    return build_encounter_state(
        build_demo_encounter(),
        [thorin, elrond],
        _FixedRandom([18, 10, 8, 3]),  # type: ignore[arg-type]
    )


def _end_turn(state: GameState, actor_id: str) -> None:
    resolve_action(
        state,
        ParsedAction(actor=actor_id, verb="end_turn", raw_text="pass"),
        _FixedRandom([]),  # type: ignore[arg-type]
    )


def test_a_prone_monster_stands_up_at_the_start_of_its_turn_for_half_its_speed() -> None:
    state = _state()
    goblin = state.characters["goblin_1"]
    apply_condition(goblin, Condition(name="prone", source="thorin"))

    _end_turn(state, "thorin")
    assert has_condition(goblin, "prone")  # not its turn yet
    _end_turn(state, "elrond")  # -> goblin_1's turn begins

    assert state.turn_order[state.current_turn] == "goblin_1"
    assert not has_condition(goblin, "prone")
    assert goblin.movement_used_feet == goblin.speed // 2
    event = next(e for e in state.events if e.type == "condition_removed")
    assert event.actor == "goblin_1"
    assert event.payload["condition"] == "prone"
    assert event.payload["reason"] == "stood up"


def test_a_prone_player_character_stands_up_on_their_own_turn_too() -> None:
    state = _state()
    thorin = state.characters["thorin"]
    apply_condition(thorin, Condition(name="prone", source="goblin_1"))

    for actor in ("thorin", "elrond", "goblin_1", "goblin_2"):
        _end_turn(state, actor)

    assert state.round == 2
    assert not has_condition(thorin, "prone")
    assert thorin.movement_used_feet == thorin.speed // 2


def test_a_grappled_creature_cannot_stand_and_stays_prone() -> None:
    state = _state()
    goblin = state.characters["goblin_1"]
    apply_condition(goblin, Condition(name="prone", source="thorin"))
    apply_condition(goblin, Condition(name="grappled", source="thorin"))

    _end_turn(state, "thorin")
    _end_turn(state, "elrond")

    assert has_condition(goblin, "prone")
    assert goblin.movement_used_feet == 0
    assert not any(e.type == "condition_removed" for e in state.events)


def test_a_creature_that_is_not_prone_is_unaffected() -> None:
    state = _state()
    _end_turn(state, "thorin")
    _end_turn(state, "elrond")
    assert state.characters["goblin_1"].movement_used_feet == 0
    assert not any(e.type == "condition_removed" for e in state.events)
