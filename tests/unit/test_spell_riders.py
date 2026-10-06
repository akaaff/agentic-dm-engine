"""Issue #97: spells whose damage landed but whose defining secondary effect
didn't - Vicious Mockery's disadvantage, Ray of Frost's slow, Guiding Bolt's
advantage, Thunderwave's push. Each is a one-round condition (or a position
change) reusing the engine's existing advantage/speed machinery. The caster is a
level-1 Elf Wizard (INT 15, spell save DC 12, spell attack +4); the goblin's WIS
save is -1 and its AC 15."""

from __future__ import annotations

from src.engine.actions import ParsedAction
from src.engine.character_creation import create_character
from src.engine.conditions import has_condition
from src.engine.encounter import monster_to_character
from src.engine.position import BattleMap, Position, TerrainType
from src.engine.rules import effective_speed
from src.engine.srd_loader import load_srd
from src.engine.state import Character, GameState
from src.engine.turn_engine import resolve_action


class _FixedRandom:
    def __init__(self, values: list[int]) -> None:
        self._values = list(values)

    def randint(self, a: int, b: int) -> int:
        return self._values.pop(0)


def _wizard() -> Character:
    wizard = create_character(
        character_id="elara",
        name="Elara",
        race_index="elf",
        class_index="wizard",
        background_index="acolyte",
        base_ability_scores={"STR": 8, "DEX": 14, "CON": 12, "INT": 15, "WIS": 13, "CHA": 10},
        chosen_skills=["skill-arcana", "skill-history"],
        chosen_prepared_spells=["thunderwave", "sleep", "magic-missile"],
        chosen_equipment=["dagger"],
        position=Position(x=0, y=0),
    )
    wizard.prepared_spells.append("guiding-bolt")
    wizard.spell_slots[1] = 4
    return wizard


def _state(
    wizard: Character, goblin_at: Position | None = None, walls: list[tuple[int, int]] | None = None
) -> GameState:
    goblin = monster_to_character(
        load_srd().monsters["goblin"], "goblin_1", goblin_at or Position(x=1, y=0)
    )
    goblin.hp = goblin.max_hp = 100
    terrain: list[list[TerrainType]] = [["floor"] * 10 for _ in range(10)]
    for x, y in walls or []:
        terrain[y][x] = "wall"
    return GameState(
        encounter_id="riders_test",
        characters={wizard.id: wizard, goblin.id: goblin},
        turn_order=[wizard.id, goblin.id],
        current_turn=0,
        round=1,
        battle_map=BattleMap(width=10, height=10, terrain=terrain, spawn_points={}),
    )


def _cast(spell: str) -> ParsedAction:
    return ParsedAction(
        actor="elara",
        verb="cast_spell",
        target="goblin_1",
        targets=["goblin_1"],
        item_or_spell=spell,
        raw_text=f"I cast {spell}",
    )


def _goblin_attacks() -> ParsedAction:
    return ParsedAction(
        actor="goblin_1",
        verb="attack",
        target="elara",
        item_or_spell="Scimitar",
        raw_text="the goblin slashes",
    )


# --- Vicious Mockery ------------------------------------------------------


def test_a_failed_save_leaves_the_target_mocked_and_its_next_attack_has_disadvantage() -> None:
    wizard = _wizard()
    state = _state(wizard)
    resolve_action(state, _cast("vicious mockery"), _FixedRandom([1, 3]))  # type: ignore[arg-type]
    goblin = state.characters["goblin_1"]
    assert has_condition(goblin, "mocked")

    # Disadvantage: [18, 3] keeps the 3, and the condition is spent by the attack.
    resolve_action(state, _goblin_attacks(), _FixedRandom([18, 3]))  # type: ignore[arg-type]
    attack = next(e for e in state.events if e.type == "attack_roll")
    assert attack.payload["natural"] == 3
    assert not has_condition(goblin, "mocked")


def test_a_passed_save_leaves_the_target_unmocked() -> None:
    wizard = _wizard()
    state = _state(wizard)
    resolve_action(state, _cast("vicious mockery"), _FixedRandom([20, 3]))  # type: ignore[arg-type]
    assert not has_condition(state.characters["goblin_1"], "mocked")


# --- Ray of Frost ---------------------------------------------------------


def test_a_ray_of_frost_hit_slows_the_target_by_ten_feet() -> None:
    wizard = _wizard()
    state = _state(wizard, goblin_at=Position(x=4, y=0))
    goblin = state.characters["goblin_1"]
    assert effective_speed(goblin) == 30
    resolve_action(state, _cast("ray of frost"), _FixedRandom([15, 4]))  # type: ignore[arg-type]
    assert has_condition(goblin, "chilled")
    assert effective_speed(goblin) == 20


def test_a_ray_of_frost_miss_does_not_slow() -> None:
    wizard = _wizard()
    state = _state(wizard, goblin_at=Position(x=4, y=0))
    resolve_action(state, _cast("ray of frost"), _FixedRandom([2]))  # type: ignore[arg-type]
    assert not has_condition(state.characters["goblin_1"], "chilled")


# --- Guiding Bolt ---------------------------------------------------------


def test_a_guiding_bolt_hit_gives_the_next_attack_against_the_target_advantage() -> None:
    wizard = _wizard()
    state = _state(wizard, goblin_at=Position(x=4, y=0))
    goblin = state.characters["goblin_1"]
    resolve_action(state, _cast("guiding bolt"), _FixedRandom([15, 3, 4, 5, 6]))  # type: ignore[arg-type]
    assert has_condition(goblin, "guided")

    # The caster's next turn: a dagger throw with advantage rolls two dice.
    state.current_turn = 0
    wizard.position = Position(x=3, y=0)
    attack = ParsedAction(
        actor="elara",
        verb="attack",
        target="goblin_1",
        item_or_spell="dagger",
        raw_text="I stab it",
    )
    resolve_action(state, attack, _FixedRandom([3, 17, 2]))  # type: ignore[arg-type]
    roll = [e for e in state.events if e.type == "attack_roll"][-1]
    assert roll.payload["natural"] == 17  # advantage keeps the higher of [3, 17]
    assert not has_condition(goblin, "guided")  # spent by that attack


# --- Thunderwave ----------------------------------------------------------


def test_thunderwave_pushes_a_target_that_fails_its_save_ten_feet_away() -> None:
    wizard = _wizard()
    state = _state(wizard)  # the goblin is adjacent at (1, 0)
    resolve_action(state, _cast("thunderwave"), _FixedRandom([1, 3, 4]))  # type: ignore[arg-type]
    goblin = state.characters["goblin_1"]
    assert (goblin.position.x, goblin.position.y) == (3, 0)
    moved = next(e for e in state.events if e.type == "move")
    assert moved.payload["from"] == {"x": 1, "y": 0}
    assert moved.payload["to"] == {"x": 3, "y": 0}
    assert moved.payload["pushed_by"] == "elara"


def test_a_passed_save_is_not_pushed() -> None:
    wizard = _wizard()
    state = _state(wizard)
    resolve_action(state, _cast("thunderwave"), _FixedRandom([20, 3, 4]))  # type: ignore[arg-type]
    assert state.characters["goblin_1"].position == Position(x=1, y=0)


def test_the_push_stops_at_a_wall() -> None:
    wizard = _wizard()
    state = _state(wizard, walls=[(3, 0)])
    resolve_action(state, _cast("thunderwave"), _FixedRandom([1, 3, 4]))  # type: ignore[arg-type]
    assert state.characters["goblin_1"].position == Position(x=2, y=0)


def test_the_push_stops_at_another_creature() -> None:
    wizard = _wizard()
    state = _state(wizard)
    blocker = monster_to_character(load_srd().monsters["goblin"], "goblin_2", Position(x=3, y=0))
    state.characters[blocker.id] = blocker
    resolve_action(state, _cast("thunderwave"), _FixedRandom([1, 3, 4]))  # type: ignore[arg-type]
    assert state.characters["goblin_1"].position == Position(x=2, y=0)
