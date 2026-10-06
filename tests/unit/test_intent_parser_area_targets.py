"""Issue #78 (class playtest): "I cast sleep on the goblins" came back with one
`target` (one goblin of three fell asleep) and "burning hands on the goblins"
sometimes as three separate casts. _expand_area_spell_targets collapses
repeated casts of an area spell and expands plural wording to every living
hostile of that kind in reach."""

from __future__ import annotations

from src.engine.actions import ParsedAction
from src.engine.encounter import monster_to_character
from src.engine.position import Position
from src.engine.srd_loader import load_srd
from src.engine.state import Character, GameState
from src.graph.nodes.intent_parser import _expand_area_spell_targets


def _caster() -> Character:
    from src.engine.character_creation import create_character

    return create_character(
        character_id="elara",
        name="Elara",
        race_index="elf",
        class_index="wizard",
        background_index="acolyte",
        base_ability_scores={"STR": 8, "DEX": 14, "CON": 12, "INT": 15, "WIS": 13, "CHA": 10},
        chosen_skills=["skill-arcana", "skill-history"],
        chosen_prepared_spells=["magic-missile", "sleep", "burning-hands"],
        chosen_equipment=["dagger"],
        position=Position(x=0, y=0),
    )


def _state(*monsters: tuple[str, str, int, int]) -> GameState:
    srd = load_srd()
    chars = {"elara": _caster()}
    for index, cid, x, y in monsters:
        chars[cid] = monster_to_character(srd.monsters[index], cid, Position(x=x, y=y))
    return GameState(
        encounter_id="t",
        characters=chars,
        turn_order=list(chars),
        current_turn=0,
        round=1,
    )


def _cast(spell: str, target: str | None, targets: list[str] | None = None) -> ParsedAction:
    return ParsedAction(
        actor="elara",
        verb="cast_spell",
        target=target,
        targets=targets,
        item_or_spell=spell,
        raw_text="x",
    )


PACK = (("goblin", "goblin_1", 6, 1), ("goblin", "goblin_2", 6, 2), ("goblin", "goblin_3", 6, 3))


def test_sleep_on_the_goblins_targets_every_goblin() -> None:
    state = _state(*PACK, ("wolf", "wolf_1", 6, 4))
    (out,) = _expand_area_spell_targets(
        [_cast("sleep", "goblin_3")], state, "elara", "I cast sleep on the goblins"
    )
    assert out.targets is not None
    assert out.target == "goblin_3"  # the model's own pick stays first
    assert set(out.targets) == {"goblin_1", "goblin_2", "goblin_3"}  # not the wolf


def test_all_of_them_includes_every_hostile_in_reach() -> None:
    state = _state(*PACK, ("wolf", "wolf_1", 6, 4))
    (out,) = _expand_area_spell_targets(
        [_cast("sleep", "goblin_1")], state, "elara", "I cast sleep on all of them"
    )
    assert out.targets is not None
    assert set(out.targets) == {"goblin_1", "goblin_2", "goblin_3", "wolf_1"}


def test_irregular_plurals_work() -> None:
    state = _state(("wolf", "wolf_1", 6, 1), ("wolf", "wolf_2", 6, 2), ("goblin", "goblin_1", 6, 3))
    (out,) = _expand_area_spell_targets(
        [_cast("sleep", "wolf_1")], state, "elara", "I cast sleep on the wolves"
    )
    assert out.targets is not None and set(out.targets) == {"wolf_1", "wolf_2"}


def test_the_dead_and_the_out_of_reach_are_not_included() -> None:
    state = _state(*PACK)
    state.characters["goblin_2"].is_dead = True
    state.characters["goblin_2"].hp = 0
    state.characters["goblin_3"].position = Position(x=40, y=40)  # far beyond Sleep's 90ft
    # Only goblin_1 is left in reach: fewer than 2, so the action is left alone.
    action = _cast("sleep", "goblin_1")
    (out,) = _expand_area_spell_targets([action], state, "elara", "I cast sleep on the goblins")
    assert out == action


def test_separate_casts_of_the_same_area_spell_collapse_into_one() -> None:
    state = _state(*PACK)
    out = _expand_area_spell_targets(
        [
            _cast("burning hands", "goblin_1"),
            _cast("burning hands", "goblin_2"),
            _cast("burning hands", "goblin_3"),
        ],
        state,
        "elara",
        "I cast burning hands",
    )
    assert len(out) == 1
    assert out[0].targets == ["goblin_1", "goblin_2", "goblin_3"]
    assert out[0].target == "goblin_1"


def test_a_singular_target_is_left_alone() -> None:
    state = _state(*PACK)
    action = _cast("sleep", "goblin_1")
    (out,) = _expand_area_spell_targets([action], state, "elara", "I cast sleep on the goblin")
    assert out == action


def test_magic_missile_and_single_target_spells_are_never_expanded() -> None:
    state = _state(*PACK)
    mm = _cast("magic missile", "goblin_1")
    vm = _cast("vicious mockery", "goblin_1")
    out = _expand_area_spell_targets([mm, vm], state, "elara", "I cast it on the goblins")
    assert out == [mm, vm]


def test_two_different_spells_do_not_merge() -> None:
    state = _state(*PACK)
    out = _expand_area_spell_targets(
        [_cast("sleep", "goblin_1"), _cast("burning hands", "goblin_2")], state, "elara", "x"
    )
    assert len(out) == 2
