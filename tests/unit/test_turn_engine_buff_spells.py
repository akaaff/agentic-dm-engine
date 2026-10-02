"""Issues #58-#61 and #68 (the remaining bucket-4 buff/debuff spells from
the #55 spell audit), plus the prerequisite they all share: ending a
caster's concentration has to strip the effect it was sustaining, not just
clear the `concentrating_on` label. Mirrors test_turn_engine_spell_audit.py's
own fixture shape (a hand-built Thorin/Elrond party against the demo
encounter's goblins) rather than importing it, same per-file-fixture
convention this project already uses - one file for the whole batch, like
that audit pass's own.

Demo-encounter geometry these tests rely on: thorin (0,1), elrond (1,2),
goblin_1 (2,1), goblin_2 (2,2) - so the goblins are adjacent to Elrond (and
each other) but 10ft from Thorin. Elrond (7 HP, AC 13, CON save +1, not
CON-proficient) is therefore the natural target of a goblin's melee attack
(+4 to hit, scimitar 1d6+2), and the natural concentrating caster.
"""

from __future__ import annotations

import random

import pytest

from src.cli.play import build_demo_encounter
from src.engine.actions import ParsedAction
from src.engine.character_creation import create_character
from src.engine.conditions import apply_condition, has_condition, tick_conditions
from src.engine.encounter import build_encounter_state
from src.engine.state import Character, Condition, GameState
from src.engine.turn_engine import TurnEngineError, resolve_action


class _FixedRandom:
    def __init__(self, values: list[int]) -> None:
        self._values = list(values)

    def randint(self, a: int, b: int) -> int:
        return self._values.pop(0)


def _two_person_party() -> list[Character]:
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
        chosen_prepared_spells=["magic-missile", "mage-armor", "sleep"],
        chosen_equipment=["dagger"],
    )
    return [thorin, elrond]


_INITIATIVE = [18, 10, 8, 3]  # thorin, elrond, goblin_1, goblin_2


def _build_demo_state(rng_values: list[int] | None = None) -> GameState:
    return build_encounter_state(
        build_demo_encounter(),
        _two_person_party(),
        _FixedRandom(rng_values or _INITIATIVE),  # type: ignore[arg-type]
    )


def _end_turn(state: GameState, actor_id: str) -> None:
    resolve_action(
        state, ParsedAction(actor=actor_id, verb="end_turn", raw_text="x"), random.Random()
    )


def _cast(
    state: GameState,
    actor_id: str,
    spell: str,
    *,
    target: str | None = None,
    targets: list[str] | None = None,
    params: dict[str, object] | None = None,
    raw_text: str | None = None,
    rng: list[int] | None = None,
) -> None:
    action = ParsedAction(
        actor=actor_id,
        verb="cast_spell",
        target=target,
        targets=targets,
        item_or_spell=spell,
        params=params or {},
        raw_text=raw_text or f"I cast {spell}",
    )
    resolve_action(state, action, _FixedRandom(rng or []))  # type: ignore[arg-type]


def _goblin_attacks(
    state: GameState, target: str, rng: list[int], goblin: str = "goblin_1"
) -> None:
    state.current_turn = state.turn_order.index(goblin)
    action = ParsedAction(actor=goblin, verb="attack", target=target, raw_text="attack")
    resolve_action(state, action, _FixedRandom(rng))  # type: ignore[arg-type]


def _sustain(
    state: GameState, caster_id: str, spell: str, on: list[str], condition: str = "blessed"
) -> None:
    """Hand-builds "caster is concentrating on `spell`, which is currently
    sustaining `condition` on each of `on`" without going through a real cast
    - for tests of what happens when concentration ENDS, not of casting."""
    state.characters[caster_id].concentrating_on = spell
    for char_id in on:
        apply_condition(
            state.characters[char_id],
            Condition(
                name=condition,  # type: ignore[arg-type]
                duration_rounds=10,
                source=caster_id,
                spell=spell,
            ),
        )


# ----------------------------------------- prerequisite: concentration ends effects


def test_a_failed_concentration_save_strips_the_effect_from_every_target() -> None:
    state = _build_demo_state()
    elrond, thorin = state.characters["elrond"], state.characters["thorin"]
    _sustain(state, "elrond", "Bless", on=["thorin", "elrond"])
    # Goblin attack on Elrond: natural 15 (+4 = 19 vs AC 13, hit), damage die
    # 1 (+2 = 3), then Elrond's CON save: DC max(10, 3//2) = 10, natural 1 +
    # CON mod 1 (not proficient) = 2 -> fails. Elrond is himself blessed here
    # (he's sustaining it on himself too), so blessed_bonus rolls a 1d4 before
    # the save - 1 more RNG value, 1 -> total 3, still a fail.
    _goblin_attacks(state, "elrond", [15, 1, 1, 1])
    assert elrond.concentrating_on is None
    assert not has_condition(thorin, "blessed")
    assert not has_condition(elrond, "blessed")
    removed = [e for e in state.events if e.type == "condition_removed"]
    assert {e.actor for e in removed} == {"thorin", "elrond"}
    assert all(e.payload["reason"] == "concentration ended" for e in removed)
    assert all(e.payload["spell"] == "Bless" for e in removed)


def test_a_successful_concentration_save_keeps_the_effect() -> None:
    state = _build_demo_state()
    elrond, thorin = state.characters["elrond"], state.characters["thorin"]
    _sustain(state, "elrond", "Bless", on=["thorin"])
    # Same hit, but a natural 10 on the save: 10 + 1 = 11 >= DC 10. (Elrond
    # isn't blessed himself here, so no blessed_bonus roll.)
    _goblin_attacks(state, "elrond", [15, 1, 10])
    assert elrond.concentrating_on == "Bless"
    assert has_condition(thorin, "blessed")
    assert not [e for e in state.events if e.type == "condition_removed"]


def test_casting_a_second_concentration_spell_drops_the_first_ones_effects() -> None:
    state = _build_demo_state()
    elrond, thorin = state.characters["elrond"], state.characters["thorin"]
    elrond.spell_slots[2] = 1
    elrond.prepared_spells.append("invisibility")  # level-gap poke, same as spell_audit's
    _sustain(state, "elrond", "Bless", on=["thorin"])
    _end_turn(state, "thorin")
    _cast(state, "elrond", "invisibility", target="elrond")
    assert elrond.concentrating_on == "Invisibility"
    assert has_condition(elrond, "invisible")
    assert not has_condition(thorin, "blessed"), "the old spell's effect must end"


def test_recasting_the_same_concentration_spell_replaces_rather_than_stacks() -> None:
    state = _build_demo_state()
    elrond, thorin = state.characters["elrond"], state.characters["thorin"]
    elrond.prepared_spells.append("bless")
    _sustain(state, "elrond", "Bless", on=["thorin"])
    _end_turn(state, "thorin")
    _cast(state, "elrond", "bless", target="elrond")
    assert elrond.concentrating_on == "Bless"
    assert has_condition(elrond, "blessed")
    assert not has_condition(thorin, "blessed"), "the first cast's target is no longer blessed"


def test_going_unconscious_ends_concentration_without_a_save() -> None:
    state = _build_demo_state()
    elrond, thorin = state.characters["elrond"], state.characters["thorin"]
    _sustain(state, "elrond", "Bless", on=["thorin"])
    # Natural 15 hits; damage die 6 (+2 = 8) >= Elrond's 7 HP -> down. The
    # concentration save is still rolled first (damage_dealt precedes the
    # downing) - a natural 20 passes it, isolating that it's going
    # unconscious, not a failed save, that ends concentration.
    _goblin_attacks(state, "elrond", [15, 6, 20])
    assert elrond.hp == 0
    assert elrond.concentrating_on is None
    assert not has_condition(thorin, "blessed")


def test_an_effect_without_a_spell_tag_is_never_stripped_by_a_concentration_ending() -> None:
    # A condition from the same source that wasn't applied by the
    # concentrated spell (e.g. a charm) is not what concentration sustains.
    state = _build_demo_state()
    thorin = state.characters["thorin"]
    _sustain(state, "elrond", "Bless", on=["thorin"])
    apply_condition(thorin, Condition(name="charmed", duration_rounds=5, source="elrond"))
    _goblin_attacks(state, "elrond", [15, 1, 1])
    assert state.characters["elrond"].concentrating_on is None
    assert not has_condition(thorin, "blessed")
    assert has_condition(thorin, "charmed")


def test_concentration_ends_quietly_once_the_last_sustained_effect_expires() -> None:
    state = _build_demo_state()
    elrond, thorin = state.characters["elrond"], state.characters["thorin"]
    _sustain(state, "elrond", "Bless", on=["thorin"])
    thorin.conditions[0] = thorin.conditions[0].model_copy(update={"duration_rounds": 1})
    for actor in ("thorin", "elrond", "goblin_1", "goblin_2"):
        _end_turn(state, actor)
    assert state.round == 2
    assert not has_condition(thorin, "blessed")
    assert elrond.concentrating_on is None
    # Expiry stays silent, same as it always was - no condition_removed event.
    assert not [e for e in state.events if e.type == "condition_removed"]


def test_concentration_continues_while_another_sustained_effect_remains() -> None:
    state = _build_demo_state()
    elrond, thorin = state.characters["elrond"], state.characters["thorin"]
    _sustain(state, "elrond", "Bless", on=["thorin", "elrond"])
    thorin.conditions[0] = thorin.conditions[0].model_copy(update={"duration_rounds": 1})
    for actor in ("thorin", "elrond", "goblin_1", "goblin_2"):
        _end_turn(state, actor)
    assert not has_condition(thorin, "blessed")
    assert has_condition(elrond, "blessed")
    assert elrond.concentrating_on == "Bless"


def test_ticking_a_condition_preserves_its_spell_and_detail_tags() -> None:
    # tick_conditions used to rebuild each Condition from a hand-picked field
    # list, which would have silently dropped every field added after it.
    character = _two_person_party()[0]
    apply_condition(
        character,
        Condition(
            name="energy_resistant", duration_rounds=5, source="elrond", spell="X", detail="fire"
        ),
    )
    tick_conditions(character)
    (condition,) = character.conditions
    assert (condition.duration_rounds, condition.source, condition.spell, condition.detail) == (
        4,
        "elrond",
        "X",
        "fire",
    )


# ----------------------------------------------- issue #59: Blur / Longstrider / Death Ward


def _prepare(state: GameState, caster_id: str, spell: str, level: int) -> Character:
    """Gives a caster a spell outside their real class list plus one slot of
    its level - same "poke state" precedent test_turn_engine_spell_audit.py
    uses for level-gap/class-gap spells, since these tests are about the
    spell's mechanic, not about who may learn it."""
    caster = state.characters[caster_id]
    caster.prepared_spells.append(spell)
    caster.spell_slots[level] = caster.spell_slots.get(level, 0) + 1
    return caster


def test_blur_is_a_self_only_concentration_condition() -> None:
    state = _build_demo_state()
    elrond = _prepare(state, "elrond", "blur", 2)
    _end_turn(state, "thorin")
    _cast(state, "elrond", "blur")  # no target named: Blur is Self-range
    assert has_condition(elrond, "blurred")
    assert elrond.concentrating_on == "Blur"
    assert elrond.spell_slots[2] == 0
    (blurred,) = elrond.conditions
    assert (blurred.duration_rounds, blurred.source, blurred.spell) == (10, "elrond", "Blur")


def test_blur_cannot_be_cast_on_someone_else() -> None:
    state = _build_demo_state()
    _prepare(state, "elrond", "blur", 2)
    _end_turn(state, "thorin")
    with pytest.raises(TurnEngineError, match="only be cast on yourself"):
        _cast(state, "elrond", "blur", target="thorin")
    # A rejected cast must not burn the slot or start concentrating.
    assert state.characters["elrond"].spell_slots[2] == 1
    assert state.characters["elrond"].concentrating_on is None


def test_attacks_against_a_blurred_target_have_disadvantage() -> None:
    state = _build_demo_state()
    apply_condition(state.characters["elrond"], Condition(name="blurred", duration_rounds=10))
    # Disadvantage rolls two d20s and keeps the lower: 20 then 2 -> natural 2
    # (+4 = 6 vs AC 13) is a miss. Without Blur a single 20 would be a crit.
    _goblin_attacks(state, "elrond", [20, 2])
    attack = next(e for e in state.events if e.type == "attack_roll")
    assert attack.payload["natural"] == 2
    assert attack.payload["hit"] is False


def test_attacks_against_an_unblurred_target_roll_a_single_d20() -> None:
    state = _build_demo_state()
    _goblin_attacks(state, "elrond", [20, 3, 10])  # d20, damage die, CON save
    attack = next(e for e in state.events if e.type == "attack_roll")
    assert attack.payload["natural"] == 20
    assert attack.payload["hit"] is True


def test_longstrider_adds_ten_feet_to_speed() -> None:
    from src.engine.rules import effective_speed

    thorin = _two_person_party()[0]
    assert effective_speed(thorin) == 30
    apply_condition(thorin, Condition(name="longstrider", duration_rounds=600))
    assert effective_speed(thorin) == 40


def test_longstrider_composes_with_the_speed_penalties_instead_of_overriding_them() -> None:
    from src.engine.rules import effective_speed

    thorin = _two_person_party()[0]
    apply_condition(thorin, Condition(name="longstrider", duration_rounds=600))
    thorin.exhaustion_level = 2  # SRD: speed halved - applied to the boosted total
    assert effective_speed(thorin) == 20
    thorin.exhaustion_level = 5  # speed 0 stays 0
    assert effective_speed(thorin) == 0
    thorin.exhaustion_level = 0
    apply_condition(thorin, Condition(name="grappled", duration_rounds=3))
    assert effective_speed(thorin) == 0


def test_longstrider_can_be_cast_on_an_ally_and_is_not_concentration() -> None:
    state = _build_demo_state()
    elrond = _prepare(state, "elrond", "longstrider", 1)
    _end_turn(state, "thorin")
    _cast(state, "elrond", "longstrider", target="thorin")
    thorin = state.characters["thorin"]
    assert has_condition(thorin, "longstrider")
    assert elrond.concentrating_on is None
    (condition,) = thorin.conditions
    assert condition.duration_rounds == 600  # SRD: 1 hour = 600 six-second rounds


def test_death_ward_drops_a_lethal_hit_to_one_hp_and_is_consumed() -> None:
    state = _build_demo_state()
    elrond = state.characters["elrond"]
    apply_condition(elrond, Condition(name="death_warded", duration_rounds=4800, source="thorin"))
    # Natural 15 hits; damage die 6 (+2 = 8) would drop Elrond (7 HP) to 0.
    _goblin_attacks(state, "elrond", [15, 6])
    assert elrond.hp == 1
    assert not has_condition(elrond, "unconscious")
    assert not has_condition(elrond, "death_warded"), "the spell ends once it triggers"
    assert [e.type for e in state.events if e.type == "death_ward"] == ["death_ward"]
    damage = next(e for e in state.events if e.type == "damage_dealt")
    assert damage.payload["target_hp_remaining"] == 1


def test_death_ward_does_not_trigger_on_damage_that_leaves_the_target_standing() -> None:
    state = _build_demo_state()
    elrond = state.characters["elrond"]
    apply_condition(elrond, Condition(name="death_warded", duration_rounds=4800, source="thorin"))
    _goblin_attacks(state, "elrond", [15, 1])  # 1 + 2 = 3 damage, 7 -> 4
    assert elrond.hp == 4
    assert has_condition(elrond, "death_warded")
    assert not [e for e in state.events if e.type == "death_ward"]


def test_a_second_lethal_hit_after_death_ward_is_spent_downs_the_target() -> None:
    state = _build_demo_state()
    elrond = state.characters["elrond"]
    apply_condition(elrond, Condition(name="death_warded", duration_rounds=4800, source="thorin"))
    _goblin_attacks(state, "elrond", [15, 6])
    assert elrond.hp == 1
    _goblin_attacks(state, "elrond", [15, 1], goblin="goblin_2")  # any damage now drops him
    assert elrond.hp == 0
    assert has_condition(elrond, "unconscious")


def test_death_ward_can_be_cast_on_an_ally() -> None:
    state = _build_demo_state()
    elrond = _prepare(state, "elrond", "death-ward", 4)
    _end_turn(state, "thorin")
    _cast(state, "elrond", "death-ward", target="thorin")
    thorin = state.characters["thorin"]
    assert has_condition(thorin, "death_warded")
    assert elrond.concentrating_on is None  # SRD: not a concentration spell
    (condition,) = thorin.conditions
    assert condition.duration_rounds == 4800  # 8 hours


def test_death_ward_is_kept_when_relentless_endurance_triggers_first() -> None:
    # Both leave the target at 1 HP. Spending the free once-per-rest trait
    # first keeps the ward the spell slot paid for for the next lethal hit.
    state = _build_demo_state()
    elrond = state.characters["elrond"]
    elrond.race_index = "half-orc"
    apply_condition(elrond, Condition(name="death_warded", duration_rounds=4800, source="thorin"))
    _goblin_attacks(state, "elrond", [15, 6])
    assert elrond.hp == 1
    assert elrond.used_relentless_endurance_this_rest is True
    assert has_condition(elrond, "death_warded")
    assert not [e for e in state.events if e.type == "death_ward"]
