"""Issue #22: monster Innate Spellcasting, resolved through resolve_action's
cast_spell dispatch for a monster actor (turn_engine._resolve_monster_innate_
spell). Uses real SRD monster/spell data throughout for the save mechanic
(green-hag's at-will Vicious Mockery, magma-mephit's 1/day Heat Metal) -
this project's curated roster has real, cleanly-resolvable examples for it.
The attack mechanic has no real SRD monster (any CR, not just this
project's CR<=5 roster) whose Innate Spellcasting list includes an
attack-roll spell with usable damage data (every attack_type-tagged innate
spell in the vendored data is either a no-damage utility effect like Ray of
Enfeeblement, or simply absent) - see
test_monster_innate_attack_params_builds_from_the_stat_blocks_own_modifier
below for how that branch is covered instead."""

from __future__ import annotations

import pytest

from src.engine.actions import ParsedAction
from src.engine.encounter import monster_to_character
from src.engine.position import Position
from src.engine.srd_loader import load_srd
from src.engine.state import Character, GameState
from src.engine.turn_engine import TurnEngineError, resolve_action


class _FixedRandom:
    def __init__(self, values: list[int]) -> None:
        self._values = list(values)

    def randint(self, a: int, b: int) -> int:
        return self._values.pop(0)


def _pc(char_id: str, position: Position) -> Character:
    return Character(
        id=char_id,
        name=char_id.title(),
        is_pc=True,
        hp=20,
        max_hp=20,
        ac=15,
        position=position,
        stats={"STR": 14, "DEX": 12, "CON": 13, "INT": 10, "WIS": 10, "CHA": 8},
        proficiency_bonus=2,
        speed=30,
        race="Human",
        class_="Fighter",
        background="Acolyte",
    )


def _make_state(*characters: Character) -> GameState:
    return GameState(
        encounter_id="monster_spellcasting_test",
        characters={c.id: c for c in characters},
        turn_order=[c.id for c in characters],
        current_turn=0,
        round=1,
    )


def test_monster_casts_an_at_will_innate_save_spell_and_deals_damage_on_a_failed_save() -> None:
    # Green-hag's Vicious Mockery: WIS save DC 12, at-will, 60ft range,
    # 1d4 psychic on a failed save (dc_success "none" - no effect at all on
    # a success, not tested here). Thorin's WIS mod is 0 and he has no WIS
    # save proficiency (Fighter saves are STR/CON), so save_bonus is 0.
    # Natural 5 -> total 5 < DC 12 -> fails. Damage die 3 -> 3 psychic.
    srd = load_srd()
    hag = monster_to_character(srd.monsters["green-hag"], "hag_1", Position(x=0, y=0))
    thorin = _pc("thorin", Position(x=5, y=0))  # 25ft, within 60ft
    state = _make_state(hag, thorin)
    action = ParsedAction(
        actor="hag_1",
        verb="cast_spell",
        target="thorin",
        item_or_spell="Vicious Mockery",
        raw_text="the hag mocks Thorin",
    )
    resolve_action(state, action, _FixedRandom([5, 3]))  # type: ignore[arg-type]

    save_event = next(e for e in state.events if e.type == "saving_throw")
    assert save_event.payload["dc"] == 12
    assert save_event.payload["ability"] == "WIS"
    assert save_event.payload["success"] is False
    damage_event = next(e for e in state.events if e.type == "damage_dealt")
    assert damage_event.payload["amount"] == 3
    assert thorin.hp == 17


def test_monster_innate_spell_usage_depletes_and_then_rejects() -> None:
    # Magma-mephit's only innate spell, Heat Metal, is 1/day.
    srd = load_srd()
    mephit = monster_to_character(srd.monsters["magma-mephit"], "mephit_1", Position(x=0, y=0))
    assert mephit.innate_spell_uses_remaining == {"heat-metal": 1}
    thorin = _pc("thorin", Position(x=5, y=0))  # 25ft, within 60ft
    state = _make_state(mephit, thorin)
    action = ParsedAction(
        actor="mephit_1",
        verb="cast_spell",
        target="thorin",
        item_or_spell="Heat Metal",
        raw_text="the mephit heats Thorin's gear",
    )
    # Heat Metal is a level-2 innate spell (2d8) with dc_success "other" -
    # not this test's concern (a pre-existing gap in _cast_save_spell_at_
    # target's own "half"/"none" handling, not introduced by issue #22) -
    # only that the cast resolves and the day's one use gets spent.
    resolve_action(state, action, _FixedRandom([15, 3, 3]))  # type: ignore[arg-type]

    assert mephit.innate_spell_uses_remaining["heat-metal"] == 0

    state.current_turn = state.turn_order.index("mephit_1")  # force it back to the mephit's turn
    with pytest.raises(TurnEngineError, match="no uses of Heat Metal remaining today"):
        resolve_action(state, action, _FixedRandom([]))  # type: ignore[arg-type]


def test_monster_innate_spell_rejects_an_unsupported_mechanic() -> None:
    # Dancing Lights: pure utility cantrip, no attack roll, no save, no
    # heal - out of issue #22's "start narrow" scope.
    srd = load_srd()
    hag = monster_to_character(srd.monsters["green-hag"], "hag_1", Position(x=0, y=0))
    thorin = _pc("thorin", Position(x=5, y=0))
    state = _make_state(hag, thorin)
    action = ParsedAction(
        actor="hag_1",
        verb="cast_spell",
        target="thorin",
        item_or_spell="Dancing Lights",
        raw_text="the hag conjures lights",
    )
    with pytest.raises(TurnEngineError, match="isn't an attack-roll or save-based spell"):
        resolve_action(state, action, _FixedRandom([]))  # type: ignore[arg-type]


def test_monster_innate_spell_rejects_an_unknown_spell_name() -> None:
    srd = load_srd()
    hag = monster_to_character(srd.monsters["green-hag"], "hag_1", Position(x=0, y=0))
    thorin = _pc("thorin", Position(x=5, y=0))
    state = _make_state(hag, thorin)
    action = ParsedAction(
        actor="hag_1",
        verb="cast_spell",
        target="thorin",
        item_or_spell="Fireball",
        raw_text="the hag tries something she doesn't know",
    )
    with pytest.raises(TurnEngineError, match="doesn't know an innate spell"):
        resolve_action(state, action, _FixedRandom([]))  # type: ignore[arg-type]


def test_monster_innate_spell_rejects_a_monster_with_no_innate_spellcasting() -> None:
    srd = load_srd()
    goblin = monster_to_character(srd.monsters["goblin"], "goblin_1", Position(x=0, y=0))
    thorin = _pc("thorin", Position(x=5, y=0))
    state = _make_state(goblin, thorin)
    action = ParsedAction(
        actor="goblin_1",
        verb="cast_spell",
        target="thorin",
        item_or_spell="Vicious Mockery",
        raw_text="the goblin tries to cast a spell it doesn't have",
    )
    with pytest.raises(TurnEngineError, match="has no Innate Spellcasting"):
        resolve_action(state, action, _FixedRandom([]))  # type: ignore[arg-type]


def test_monster_innate_spell_rejects_a_target_out_of_range() -> None:
    srd = load_srd()
    hag = monster_to_character(srd.monsters["green-hag"], "hag_1", Position(x=0, y=0))
    thorin = _pc("thorin", Position(x=20, y=0))  # 100ft, beyond Vicious Mockery's 60ft
    state = _make_state(hag, thorin)
    action = ParsedAction(
        actor="hag_1",
        verb="cast_spell",
        target="thorin",
        item_or_spell="Vicious Mockery",
        raw_text="the hag mocks from afar",
    )
    with pytest.raises(TurnEngineError, match="out of range"):
        resolve_action(state, action, _FixedRandom([]))  # type: ignore[arg-type]


def test_monster_innate_spell_rejects_a_same_side_target() -> None:
    srd = load_srd()
    hag = monster_to_character(srd.monsters["green-hag"], "hag_1", Position(x=0, y=0))
    goblin = monster_to_character(srd.monsters["goblin"], "goblin_1", Position(x=5, y=0))
    state = _make_state(hag, goblin)
    action = ParsedAction(
        actor="hag_1",
        verb="cast_spell",
        target="goblin_1",
        item_or_spell="Vicious Mockery",
        raw_text="the hag mocks her ally by mistake",
    )
    with pytest.raises(TurnEngineError, match="same side"):
        resolve_action(state, action, _FixedRandom([]))  # type: ignore[arg-type]


def test_monster_innate_attack_params_builds_from_the_stat_blocks_own_modifier() -> None:
    # See this module's docstring: no real SRD monster's Innate
    # Spellcasting list includes an attack-roll spell with usable damage
    # data, so this builder is tested directly - a hand-built `innate`
    # dict shaped exactly like rules.monster_innate_spellcasting's real
    # return value, paired with the real SRD Fire Bolt (a genuine
    # attack-roll cantrip).
    from src.engine.turn_engine import _monster_innate_attack_params

    srd = load_srd()
    fire_bolt = srd.spells["fire-bolt"]
    innate = {"ability": {"index": "int"}, "dc": 13, "modifier": 5, "spells": []}

    params = _monster_innate_attack_params(innate, fire_bolt, spell_level=0, range_normal_feet=120)

    assert params.attack_bonus == 5
    assert params.damage_type == "fire"
    assert (params.damage_dice_count, params.damage_dice_sides) == (1, 10)
