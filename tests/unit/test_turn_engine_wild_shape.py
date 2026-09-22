"""Issue #24 part 2: Druid Wild Shape, end-to-end through resolve_action's
wild_shape/revert_wild_shape dispatch and the forced-revert hook in
_apply_damage_and_handle_downing. Real SRD monster data throughout (wolf's
own stat block/Bite action - no synthetic fixtures needed, matching this
project's hand-computed-fixture discipline)."""

from __future__ import annotations

import pytest

from src.engine.actions import ParsedAction
from src.engine.character_creation import create_character
from src.engine.conditions import has_condition
from src.engine.encounter import monster_to_character
from src.engine.position import Position
from src.engine.srd_loader import load_srd
from src.engine.state import Character, GameState
from src.engine.turn_engine import (
    TurnEngineError,
    _apply_damage_and_handle_downing,
    resolve_action,
)


class _FixedRandom:
    def __init__(self, values: list[int]) -> None:
        self._values = list(values)

    def randint(self, a: int, b: int) -> int:
        return self._values.pop(0)


def _druid(position: Position | None = None) -> Character:
    # STR11(mod0), DEX15(mod+2), CON14(mod+2), WIS16(mod+3) after Human's
    # +1-to-every-ability bonus. hp 10, ac 13 at creation.
    druid = create_character(
        character_id="rowan",
        name="Rowan",
        race_index="human",
        class_index="druid",
        background_index="acolyte",
        base_ability_scores={"STR": 10, "DEX": 14, "CON": 13, "INT": 8, "WIS": 15, "CHA": 12},
        chosen_skills=["skill-nature", "skill-survival"],
        # WIS15 -> mod3 after Human's +1; issue #30's follow-up phase
        # requires exactly prepared_spell_count("druid", 1, 3) == 4 real
        # level-1 Druid spells.
        chosen_prepared_spells=["goodberry", "entangle", "faerie-fire", "cure-wounds"],
        position=position or Position(x=0, y=0),
    )
    druid.level = 2  # Wild Shape doesn't exist below level 2
    druid.class_resources["wild_shape"] = 2
    return druid


def _goblin(char_id: str, position: Position) -> Character:
    srd = load_srd()
    goblin = monster_to_character(srd.monsters["goblin"], char_id, position)
    goblin.hp = goblin.max_hp = 100
    return goblin


def _make_state(*characters: Character) -> GameState:
    return GameState(
        encounter_id="wild_shape_test",
        characters={c.id: c for c in characters},
        turn_order=[c.id for c in characters],
        current_turn=0,
        round=1,
    )


def test_wild_shape_swaps_stats_and_the_druid_can_then_bite_as_the_wolf() -> None:
    rowan = _druid()
    goblin = _goblin("goblin_1", Position(x=1, y=0))  # 5ft - within the wolf's Bite reach
    state = _make_state(rowan, goblin)
    action = ParsedAction(
        actor="rowan",
        verb="wild_shape",
        params={"beast_index": "wolf"},
        raw_text="I wild shape into a wolf",
    )
    resolve_action(state, action, _FixedRandom([]))  # type: ignore[arg-type]

    assert rowan.wild_shape_beast_index == "wolf"
    assert rowan.monster_index == "wolf"
    assert rowan.hp == rowan.max_hp == 11  # the wolf's own hp
    assert rowan.ac == 13  # the wolf's own AC
    assert rowan.equipped_weapons == []
    assert rowan.class_resources["wild_shape"] == 1
    wild_shape_event = next(e for e in state.events if e.type == "wild_shape")
    assert wild_shape_event.payload["beast"] == "Wolf"
    assert state.turn_order[state.current_turn] == "goblin_1"  # ended rowan's turn

    # Force it back to rowan's turn to prove the wolf's own Bite action
    # resolves through the real monster attack path (attack_bonus 4,
    # 2d4+2 piercing) - not a parallel PC-style resolver.
    state.current_turn = state.turn_order.index("rowan")
    action2 = ParsedAction(
        actor="rowan", verb="attack", target="goblin_1", raw_text="I bite the goblin"
    )
    # Natural 12 -> total 16 >= goblin AC 15 -> hit, not a crit. Damage dice
    # [3, 2] + notation bonus 2 = 7.
    resolve_action(state, action2, _FixedRandom([12, 3, 2]))  # type: ignore[arg-type]

    attack_event = next(e for e in state.events if e.type == "attack_roll")
    assert attack_event.payload["roll_total"] == 16
    assert attack_event.payload["hit"] is True
    assert attack_event.payload["source"] == "Bite"
    damage_event = next(e for e in state.events if e.type == "damage_dealt")
    assert damage_event.payload["amount"] == 7
    assert damage_event.payload["damage_type"] == "piercing"


def test_reverting_restores_the_druids_own_stats_exactly_without_ending_the_turn() -> None:
    rowan = _druid()
    state = _make_state(rowan, _goblin("goblin_1", Position(x=9, y=9)))
    resolve_action(
        state,
        ParsedAction(
            actor="rowan",
            verb="wild_shape",
            params={"beast_index": "wolf"},
            raw_text="I wild shape into a wolf",
        ),
        _FixedRandom([]),  # type: ignore[arg-type]
    )
    state.current_turn = state.turn_order.index("rowan")

    resolve_action(
        state,
        ParsedAction(actor="rowan", verb="revert_wild_shape", raw_text="I revert to myself"),
        _FixedRandom([]),  # type: ignore[arg-type]
    )

    assert rowan.wild_shape_beast_index is None
    assert rowan.monster_index is None
    assert rowan.pre_wild_shape_snapshot is None
    assert rowan.hp == 10  # exactly restored, not affected by anything in beast form
    assert rowan.max_hp == 10
    assert rowan.ac == 13
    assert rowan.stats["WIS"] == 16
    assert rowan.bonus_action_used is True
    ended_event = next(e for e in state.events if e.type == "wild_shape_ended")
    assert ended_event.payload["forced"] is False
    assert state.turn_order[state.current_turn] == "rowan"  # still his turn - a bonus action


def test_wild_shape_rejected_below_level_2() -> None:
    rowan = _druid()
    rowan.level = 1
    rowan.class_resources.pop("wild_shape", None)
    state = _make_state(rowan, _goblin("goblin_1", Position(x=9, y=9)))
    action = ParsedAction(
        actor="rowan", verb="wild_shape", params={"beast_index": "wolf"}, raw_text="I try"
    )
    with pytest.raises(TurnEngineError, match="doesn't have Wild Shape"):
        resolve_action(state, action, _FixedRandom([]))  # type: ignore[arg-type]


def test_wild_shape_rejected_when_already_shapeshifted() -> None:
    rowan = _druid()
    state = _make_state(rowan, _goblin("goblin_1", Position(x=9, y=9)))
    resolve_action(
        state,
        ParsedAction(
            actor="rowan", verb="wild_shape", params={"beast_index": "wolf"}, raw_text="I shift"
        ),
        _FixedRandom([]),  # type: ignore[arg-type]
    )
    state.current_turn = state.turn_order.index("rowan")
    action = ParsedAction(
        actor="rowan", verb="wild_shape", params={"beast_index": "wolf"}, raw_text="I shift again"
    )
    with pytest.raises(TurnEngineError, match="already wild-shaped"):
        resolve_action(state, action, _FixedRandom([]))  # type: ignore[arg-type]


def test_wild_shape_rejects_a_beast_over_the_cr_cap() -> None:
    rowan = _druid()  # level 2 -> max CR 0.25
    state = _make_state(rowan, _goblin("goblin_1", Position(x=9, y=9)))
    action = ParsedAction(
        actor="rowan",
        verb="wild_shape",
        params={"beast_index": "black-bear"},  # CR 0.5
        raw_text="I try to become a bear",
    )
    with pytest.raises(TurnEngineError, match="cannot Wild Shape into"):
        resolve_action(state, action, _FixedRandom([]))  # type: ignore[arg-type]


def test_wild_shape_rejects_a_swimming_beast_below_level_4() -> None:
    rowan = _druid()  # level 2
    state = _make_state(rowan, _goblin("goblin_1", Position(x=9, y=9)))
    action = ParsedAction(
        actor="rowan",
        verb="wild_shape",
        params={"beast_index": "giant-poisonous-snake"},
        raw_text="I try to become a snake",
    )
    with pytest.raises(TurnEngineError, match="cannot Wild Shape into"):
        resolve_action(state, action, _FixedRandom([]))  # type: ignore[arg-type]


def test_wild_shape_rejects_a_flying_beast_at_any_level_in_scope() -> None:
    rowan = _druid()
    rowan.level = 5
    rowan.class_resources["wild_shape"] = 2
    state = _make_state(rowan, _goblin("goblin_1", Position(x=9, y=9)))
    action = ParsedAction(
        actor="rowan",
        verb="wild_shape",
        params={"beast_index": "giant-owl"},
        raw_text="I try to become an owl",
    )
    with pytest.raises(TurnEngineError, match="cannot Wild Shape into"):
        resolve_action(state, action, _FixedRandom([]))  # type: ignore[arg-type]


def test_wild_shape_rejects_an_unknown_beast_index() -> None:
    rowan = _druid()
    state = _make_state(rowan, _goblin("goblin_1", Position(x=9, y=9)))
    action = ParsedAction(
        actor="rowan",
        verb="wild_shape",
        params={"beast_index": "not-a-real-monster"},
        raw_text="I try to become something",
    )
    with pytest.raises(TurnEngineError, match="Unknown monster"):
        resolve_action(state, action, _FixedRandom([]))  # type: ignore[arg-type]


def test_wild_shape_uses_deplete_and_then_reject() -> None:
    rowan = _druid()
    rowan.class_resources["wild_shape"] = 1
    state = _make_state(rowan, _goblin("goblin_1", Position(x=9, y=9)))
    resolve_action(
        state,
        ParsedAction(
            actor="rowan", verb="wild_shape", params={"beast_index": "wolf"}, raw_text="I shift"
        ),
        _FixedRandom([]),  # type: ignore[arg-type]
    )
    state.current_turn = state.turn_order.index("rowan")
    resolve_action(
        state,
        ParsedAction(actor="rowan", verb="revert_wild_shape", raw_text="I revert"),
        _FixedRandom([]),  # type: ignore[arg-type]
    )
    assert rowan.class_resources["wild_shape"] == 0

    state.current_turn = state.turn_order.index("rowan")
    action = ParsedAction(
        actor="rowan", verb="wild_shape", params={"beast_index": "wolf"}, raw_text="I try again"
    )
    with pytest.raises(TurnEngineError, match="no wild shape uses remaining"):
        resolve_action(state, action, _FixedRandom([]))  # type: ignore[arg-type]


def test_revert_rejected_when_not_wild_shaped() -> None:
    rowan = _druid()
    state = _make_state(rowan, _goblin("goblin_1", Position(x=9, y=9)))
    with pytest.raises(TurnEngineError, match="is not wild-shaped"):
        resolve_action(
            state,
            ParsedAction(actor="rowan", verb="revert_wild_shape", raw_text="I try to revert"),
            _FixedRandom([]),  # type: ignore[arg-type]
        )


def test_dropping_to_0_hp_in_beast_form_force_reverts_with_overflow_damage() -> None:
    # Rowan (hp 10) shifts into a wolf (hp 11, full - always the beast's
    # max, regardless of the Druid's own current hp) and takes 15 raw
    # piercing damage: actual_loss clamps at the wolf's 11, overflow = 15 -
    # 11 = 4, carried onto the Druid's real (snapshotted) 10 hp -> 6.
    srd = load_srd()
    rowan = _druid()
    goblin = _goblin("goblin_1", Position(x=0, y=0))
    state = _make_state(rowan, goblin)
    resolve_action(
        state,
        ParsedAction(
            actor="rowan", verb="wild_shape", params={"beast_index": "wolf"}, raw_text="I shift"
        ),
        _FixedRandom([]),  # type: ignore[arg-type]
    )
    assert rowan.hp == 11

    _apply_damage_and_handle_downing(
        state,
        goblin,
        rowan,
        15,
        "piercing",
        _FixedRandom([]),  # type: ignore[arg-type]
        srd,
    )

    assert rowan.wild_shape_beast_index is None
    assert rowan.monster_index is None
    assert rowan.max_hp == 10  # back to the Druid's own max
    assert rowan.hp == 6  # 10 - overflow(4)
    assert not rowan.is_dead
    assert not has_condition(rowan, "unconscious")
    ended_event = next(e for e in state.events if e.type == "wild_shape_ended")
    assert ended_event.payload["forced"] is True
    assert ended_event.payload["overflow_damage"] == 4


def test_overflow_damage_can_also_knock_the_reverted_druid_unconscious() -> None:
    # Same wolf form (hp 11), but this time 25 raw damage: overflow = 25 -
    # 11 = 14, well past the Druid's real 10 hp - clamped at 0, and since
    # the reverted character is still at 0 hp, the ordinary PC-unconscious
    # handling correctly takes over (falls through, doesn't return early).
    srd = load_srd()
    rowan = _druid()
    goblin = _goblin("goblin_1", Position(x=0, y=0))
    state = _make_state(rowan, goblin)
    resolve_action(
        state,
        ParsedAction(
            actor="rowan", verb="wild_shape", params={"beast_index": "wolf"}, raw_text="I shift"
        ),
        _FixedRandom([]),  # type: ignore[arg-type]
    )

    _apply_damage_and_handle_downing(
        state,
        goblin,
        rowan,
        25,
        "piercing",
        _FixedRandom([]),  # type: ignore[arg-type]
        srd,
    )

    assert rowan.wild_shape_beast_index is None
    assert rowan.hp == 0
    assert has_condition(rowan, "unconscious")
    assert not rowan.is_dead  # a PC always goes unconscious, never dies outright
