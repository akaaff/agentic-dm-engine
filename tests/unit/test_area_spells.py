"""Area spells on a real map: the cast puts the area somewhere and catches whoever is inside it,
Entangle and Grease leave difficult terrain behind (priced into movement, drawn from the game
state, gone when concentration ends or the time runs out), and an area that can't reach what
was named is refused before a slot is spent.

Live request: "we need to understand how we define the exact impacted area while casting", and
"the impacted terrain needs to be shown on the minimap"."""

from __future__ import annotations

import pytest

from src.engine.actions import ParsedAction
from src.engine.character_creation import create_character
from src.engine.position import BattleMap, Position
from src.engine.srd_loader import load_srd
from src.engine.state import Character, GameState
from src.engine.turn_engine import (
    AttackOutOfRangeError,
    TurnEngineError,
    _end_concentration,
    _tick_effect_areas,
    resolve_action,
)


class _FixedRandom:
    def __init__(self, values: list[int]) -> None:
        self._values = list(values)

    def randint(self, a: int, b: int) -> int:
        return self._values.pop(0)


def _druid() -> Character:
    druid = create_character(
        character_id="sylva",
        name="Sylva",
        race_index="human",
        class_index="druid",
        background_index="acolyte",
        base_ability_scores={"STR": 8, "DEX": 13, "CON": 14, "INT": 10, "WIS": 15, "CHA": 12},
        chosen_skills=["skill-nature", "skill-perception"],
        chosen_prepared_spells=["entangle", "goodberry", "longstrider", "faerie-fire"],
        chosen_equipment=["quarterstaff"],
    )
    druid.position = Position(x=0, y=0)
    return druid


def _ally() -> Character:
    ally = create_character(
        character_id="thorin",
        name="Thorin",
        race_index="human",
        class_index="fighter",
        background_index="acolyte",
        base_ability_scores={"STR": 15, "DEX": 14, "CON": 13, "INT": 12, "WIS": 10, "CHA": 8},
        chosen_skills=["skill-athletics", "skill-perception"],
        chosen_equipment=["longsword"],
    )
    ally.position = Position(x=1, y=0)
    return ally


def _goblin(goblin_id: str, x: int, y: int) -> Character:
    return Character(
        id=goblin_id,
        name=goblin_id.replace("_", " ").title(),
        race="humanoid",
        class_="Monster",
        monster_index="goblin",
        background="",
        is_pc=False,
        hp=200,
        max_hp=200,
        ac=10,
        level=1,
        position=Position(x=x, y=y),
        stats={"STR": 8, "DEX": 14, "CON": 10, "INT": 10, "WIS": 8, "CHA": 8},
        speed=30,
        proficiency_bonus=2,
    )


def _state(*goblins: tuple[str, int, int], with_ally: bool = False, width: int = 14) -> GameState:
    characters = {"sylva": _druid()}
    if with_ally:
        characters["thorin"] = _ally()
    for goblin_id, x, y in goblins:
        characters[goblin_id] = _goblin(goblin_id, x, y)
    return GameState(
        encounter_id="area_spell_test",
        characters=characters,
        turn_order=list(characters),
        current_turn=0,
        round=1,
        battle_map=BattleMap(
            width=width,
            height=14,
            terrain=[["floor"] * width for _ in range(14)],
            spawn_points={},
        ),
    )


def _cast(spell: str, targets: list[str], **params: object) -> ParsedAction:
    return ParsedAction(
        actor="sylva",
        verb="cast_spell",
        item_or_spell=spell,
        target=targets[0] if targets else None,
        targets=targets,
        params=dict(params),
        raw_text="x",
    )


def _run(state: GameState, action: ParsedAction, dice: list[int]) -> None:
    resolve_action(state, action, _FixedRandom(dice))  # type: ignore[arg-type]


def test_entangle_catches_everyone_inside_its_square_not_just_who_was_named() -> None:
    state = _state(("goblin_1", 6, 6), ("goblin_2", 7, 7), ("goblin_3", 12, 12))
    # goblin_1 named; goblin_2 stands in the same 20 ft square; goblin_3 is far away
    _run(state, _cast("Entangle", ["goblin_1"]), [2, 2])
    area = next(e for e in state.events if e.type == "spell_area")
    assert sorted(area.payload["caught"]) == ["goblin_1", "goblin_2"]
    assert area.payload["shape"] == "cube" and area.payload["size_feet"] == 20
    assert len(area.payload["cells"]) == 16 and area.payload["terrain"] == "difficult"
    saved = [e.payload["target"] for e in state.events if e.type == "saving_throw"]
    assert saved == ["goblin_1", "goblin_2"]


def test_entangle_leaves_difficult_terrain_that_costs_double_to_cross() -> None:
    state = _state(("goblin_1", 6, 6))
    _run(state, _cast("Entangle", ["goblin_1"]), [2])
    [area] = state.effect_areas
    assert area.spell == "Entangle" and area.caster_id == "sylva" and area.concentration is True
    assert area.duration_rounds == 10 and len(area.cells) == 16
    overlay = state.movement_terrain()
    inside = area.cells[0]
    assert overlay[inside.y][inside.x] == "difficult"
    assert state.battle_map is not None
    assert state.battle_map.terrain[inside.y][inside.x] == "floor"  # the authored map is untouched
    outside = overlay[0][13]
    assert outside == "floor"


def test_a_wall_keeps_its_own_terrain_under_an_area() -> None:
    state = _state(("goblin_1", 6, 6))
    assert state.battle_map is not None
    state.battle_map.terrain[6][5] = "wall"
    _run(state, _cast("Entangle", ["goblin_1"]), [2])
    [area] = state.effect_areas
    assert Position(x=5, y=6) not in area.cells  # an area doesn't cover a wall
    assert state.movement_terrain()[6][5] == "wall"


def test_the_area_ends_with_the_casters_concentration() -> None:
    state = _state(("goblin_1", 6, 6))
    _run(state, _cast("Entangle", ["goblin_1"]), [2])
    _end_concentration(state, state.characters["sylva"], load_srd())
    assert state.effect_areas == []
    ended = next(e for e in state.events if e.type == "effect_area_ended")
    assert ended.payload == {"spell": "Entangle", "reason": "concentration ended"}


def test_the_area_expires_after_its_rounds() -> None:
    state = _state(("goblin_1", 6, 6))
    _run(state, _cast("Entangle", ["goblin_1"]), [2])
    for _ in range(9):
        _tick_effect_areas(state)
    assert len(state.effect_areas) == 1 and state.effect_areas[0].duration_rounds == 1
    _tick_effect_areas(state)
    assert state.effect_areas == []
    assert next(e for e in state.events if e.type == "effect_area_ended").payload["reason"] == (
        "expired"
    )


def test_grease_leaves_terrain_but_does_not_need_concentration() -> None:
    state = _state(("goblin_1", 6, 6))
    state.characters["sylva"].prepared_spells.append("grease")
    _run(state, _cast("Grease", ["goblin_1"]), [2])
    [area] = state.effect_areas
    assert area.spell == "Grease" and area.concentration is False and len(area.cells) == 4
    assert state.characters["sylva"].concentrating_on is None


def test_casting_it_again_moves_the_patch_instead_of_adding_another() -> None:
    state = _state(("goblin_1", 6, 6), ("goblin_2", 11, 11))
    state.characters["sylva"].spell_slots[1] = 3
    _run(state, _cast("Entangle", ["goblin_1"]), [2])
    first = {(c.x, c.y) for c in state.effect_areas[0].cells}
    state.current_turn = 0  # the cast ended Sylva's turn; it is hers again
    _run(state, _cast("Entangle", ["goblin_2"]), [2])
    assert len(state.effect_areas) == 1
    assert {(c.x, c.y) for c in state.effect_areas[0].cells} != first


def test_entangle_is_placed_to_spare_an_ally_standing_beside_the_target() -> None:
    state = _state(("goblin_1", 3, 2), with_ally=True)
    state.characters["thorin"].position = Position(x=4, y=2)  # right next to the goblin
    _run(state, _cast("Entangle", ["goblin_1"]), [2])
    area = next(e for e in state.events if e.type == "spell_area")
    assert "thorin" not in area.payload["caught"] and area.payload["allies_caught"] == []
    assert "goblin_1" in area.payload["caught"]


def test_a_chosen_square_is_used_as_given_and_friendly_fire_is_real() -> None:
    state = _state(("goblin_1", 6, 6), with_ally=True)
    state.characters["thorin"].position = Position(x=7, y=6)
    _run(state, _cast("Entangle", [], origin={"x": 6, "y": 6}), [2, 2])
    area = next(e for e in state.events if e.type == "spell_area")
    assert area.payload["origin"] == {"x": 6, "y": 6}
    assert sorted(area.payload["caught"]) == ["goblin_1", "thorin"]
    assert area.payload["allies_caught"] == ["thorin"]
    assert {e.payload["target"] for e in state.events if e.type == "saving_throw"} == {
        "goblin_1",
        "thorin",
    }


def test_centered_on_a_creature_puts_the_area_on_it() -> None:
    state = _state(("goblin_1", 9, 9))
    _run(state, _cast("Entangle", [], centered_on="goblin_1"), [2])
    area = next(e for e in state.events if e.type == "spell_area")
    assert area.payload["origin"] == {"x": 9, "y": 9}


def test_a_square_out_of_range_is_refused_before_a_slot_is_spent() -> None:
    state = _state(("goblin_1", 6, 6))
    slots = state.characters["sylva"].spell_slots[1]
    # Faerie Fire reaches 60 ft (12 squares); (13, 13) is 65 ft away
    with pytest.raises(TurnEngineError, match="out of range"):
        _run(state, _cast("Faerie Fire", [], origin={"x": 13, "y": 13}), [2])
    assert state.characters["sylva"].spell_slots[1] == slots
    assert state.effect_areas == []


def test_a_target_beyond_reach_plus_the_areas_own_size_gets_the_out_of_range_error() -> None:
    # Faerie Fire reaches 60 ft; a 20 ft cube at the edge of that can still catch a creature
    # a couple of squares past it, but not one 25 squares away.
    state = _state(("goblin_1", 25, 0), width=30)
    with pytest.raises(AttackOutOfRangeError):
        _run(state, _cast("Faerie Fire", ["goblin_1"]), [2])


def test_an_area_at_the_edge_of_range_can_still_catch_a_creature_just_past_it() -> None:
    state = _state(("goblin_1", 13, 0))  # 65 ft: past 60, but inside a cube placed 60 ft out
    _run(state, _cast("Faerie Fire", ["goblin_1"]), [2])
    area = next(e for e in state.events if e.type == "spell_area")
    assert area.payload["caught"] == ["goblin_1"]


def test_an_area_spell_needs_a_target_or_a_square() -> None:
    state = _state(("goblin_1", 6, 6))
    with pytest.raises(TurnEngineError, match="requires a target"):
        _run(state, _cast("Entangle", []), [2])


def test_the_area_survives_a_save_and_restore_of_the_game_state() -> None:
    # Sessions are snapshotted as JSON and rebuilt after a restart: the patch must come back.
    state = _state(("goblin_1", 6, 6))
    _run(state, _cast("Entangle", ["goblin_1"]), [2])
    restored = GameState.model_validate(state.model_dump(mode="json"))
    assert restored.effect_areas == state.effect_areas
    assert restored.movement_terrain() == state.movement_terrain()


def test_sleep_uses_the_same_geometry_and_catches_creatures_in_its_sphere() -> None:
    wizard = create_character(
        character_id="sylva",
        name="Sylva",
        race_index="elf",
        class_index="wizard",
        background_index="acolyte",
        base_ability_scores={"STR": 8, "DEX": 14, "CON": 12, "INT": 15, "WIS": 13, "CHA": 10},
        chosen_skills=["skill-arcana", "skill-history"],
        chosen_prepared_spells=["magic-missile", "mage-armor", "sleep"],
        chosen_equipment=["dagger"],
    )
    wizard.position = Position(x=0, y=0)
    state = _state(("goblin_1", 6, 6), ("goblin_2", 8, 6), ("goblin_3", 13, 0))
    state.characters["sylva"] = wizard
    for goblin_id in ("goblin_1", "goblin_2", "goblin_3"):
        state.characters[goblin_id].hp = state.characters[goblin_id].max_hp = 5
    # goblin_1 named; goblin_2 is inside the same 20 ft sphere; goblin_3 is across the map
    _run(state, _cast("Sleep", ["goblin_1"]), [4, 4, 4, 4, 4])
    area = next(e for e in state.events if e.type == "spell_area")
    assert sorted(area.payload["caught"]) == ["goblin_1", "goblin_2"]
    asleep = {
        c.id
        for c in state.characters.values()
        if any(cond.name == "unconscious" for cond in c.conditions)
    }
    assert asleep == {"goblin_1", "goblin_2"}
