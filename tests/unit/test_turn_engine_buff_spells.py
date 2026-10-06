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
from src.engine.position import Position
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


# ------------------------------------------------------------- issue #68: Bane


def test_a_failed_bane_save_applies_the_baned_condition() -> None:
    state = _build_demo_state()
    elrond = _prepare(state, "elrond", "bane", 1)
    _end_turn(state, "thorin")
    # Spell save DC 8 + proficiency 2 + INT mod 2 = 12; goblin CHA 8 = -1.
    # Two targets, two independent saves: natural 5 (4 < 12) fails, natural 15
    # (14 >= 12) passes.
    _cast(state, "elrond", "bane", targets=["goblin_1", "goblin_2"], rng=[5, 15])
    goblin_1, goblin_2 = state.characters["goblin_1"], state.characters["goblin_2"]
    assert has_condition(goblin_1, "baned")
    assert not has_condition(goblin_2, "baned")
    (baned,) = goblin_1.conditions
    assert (baned.duration_rounds, baned.source, baned.spell) == (10, "elrond", "Bane")
    assert elrond.concentrating_on == "Bane"
    applied = [e for e in state.events if e.type == "condition_applied"]
    assert [(e.actor, e.payload["condition"]) for e in applied] == [("goblin_1", "baned")]


def test_a_baned_creature_subtracts_a_d4_from_its_attack_rolls() -> None:
    state = _build_demo_state()
    apply_condition(
        state.characters["goblin_1"],
        Condition(name="baned", duration_rounds=10, source="elrond", spell="Bane"),
    )
    # RNG order: baned_penalty's 1d4 first (3), then the attack's d20 (11).
    # +4 attack bonus -3 = +1 -> 12 < Elrond's AC 13: a miss that would have
    # been a hit (15) without Bane.
    _goblin_attacks(state, "elrond", [3, 11])
    attack = next(e for e in state.events if e.type == "attack_roll")
    assert attack.payload["roll_total"] == 12
    assert attack.payload["hit"] is False
    assert ("baned (1d4)", -3) in attack.payload["attack_bonus_breakdown"]


def test_a_baned_creature_subtracts_a_d4_from_its_saving_throws() -> None:
    state = _build_demo_state()
    elrond = state.characters["elrond"]
    elrond.concentrating_on = "Some Spell"
    apply_condition(elrond, Condition(name="baned", duration_rounds=10, source="x", spell="Bane"))
    # Goblin hits Elrond (natural 15, damage die 1 -> 3 damage; DC 10), then
    # Elrond's CON save: baned_penalty's 1d4 (4) first, then the d20 (12).
    # CON mod +1 - 4 = -3: 12 - 3 = 9 < 10 fails - without Bane, 13 would pass.
    _goblin_attacks(state, "elrond", [15, 1, 4, 12])
    save = next(
        e
        for e in state.events
        if e.type == "saving_throw" and e.payload.get("kind") == "concentration"
    )
    assert ("baned (1d4)", -4) in save.payload["modifier_breakdown"]
    assert save.payload["success"] is False
    assert elrond.concentrating_on is None


def test_a_baned_target_penalty_applies_to_a_spell_save_too() -> None:
    state = _build_demo_state()
    elrond = _prepare(state, "elrond", "bane", 1)
    goblin = state.characters["goblin_1"]
    apply_condition(goblin, Condition(name="baned", duration_rounds=3, source="x", spell="Bane"))
    _end_turn(state, "thorin")
    # Save: baned_penalty's 1d4 (4), then natural 14. CHA -1 - 4 = -5 ->
    # 14 - 5 = 9 < DC 12 fails (13 would have passed unpenalized).
    _cast(state, "elrond", "bane", target="goblin_1", rng=[4, 14])
    save = next(e for e in state.events if e.payload.get("kind") == "spell_save")
    assert save.payload["success"] is False
    assert ("baned (1d4)", -4) in save.payload["modifier_breakdown"]
    # Recasting refreshes the (single, non-stacking) condition to a full duration.
    (condition,) = goblin.conditions
    assert condition.duration_rounds == 10
    assert elrond.concentrating_on == "Bane"


def test_bane_does_not_touch_death_saves() -> None:
    state = _build_demo_state()
    thorin = state.characters["thorin"]
    thorin.hp = 0
    apply_condition(thorin, Condition(name="unconscious", source="0 HP"))
    apply_condition(thorin, Condition(name="baned", duration_rounds=10, source="x", spell="Bane"))
    # A death save is a flat d20: a baned_penalty roll before it would
    # consume this list's only value as the d4 and crash on the d20.
    resolve_action(
        state,
        ParsedAction(actor="thorin", verb="death_save", raw_text="hold on"),
        _FixedRandom([12]),  # type: ignore[arg-type]
    )
    assert thorin.death_save_successes == 1


def test_an_unbaned_creature_consumes_no_extra_rng() -> None:
    # Regression guard for every pre-existing fixed-RNG attack/save in the
    # suite: baned_penalty must roll nothing when the actor isn't baned.
    state = _build_demo_state()
    _goblin_attacks(state, "elrond", [15, 1, 10])  # attack, damage die, (no bane d4)
    attack = next(e for e in state.events if e.type == "attack_roll")
    assert all(label != "baned (1d4)" for label, _ in attack.payload["attack_bonus_breakdown"])
    assert attack.payload["roll_total"] == 19


def test_losing_concentration_on_bane_frees_the_baned_creature() -> None:
    state = _build_demo_state()
    elrond = _prepare(state, "elrond", "bane", 1)
    _end_turn(state, "thorin")
    _cast(state, "elrond", "bane", target="goblin_1", rng=[5])
    assert has_condition(state.characters["goblin_1"], "baned")
    # goblin_2 (not baned) hits Elrond for 3; his CON save fails (natural 1).
    _goblin_attacks(state, "elrond", [15, 1, 1], goblin="goblin_2")
    assert elrond.concentrating_on is None
    assert not has_condition(state.characters["goblin_1"], "baned")


# ------------ issue #60: Protection from Energy / Stoneskin / Protection from Poison / Barkskin


def _hit_with(state: GameState, target_id: str, amount: int, damage_type: str) -> int:
    """Applies `amount` of `damage_type` damage to `target_id` through the
    one chokepoint every attack/spell funnels through (where resistance is
    decided), returning the HP actually lost - so a test can pin a resistance
    rule without needing a monster that happens to deal that damage type."""
    from src.engine.srd_loader import load_srd
    from src.engine.turn_engine import _apply_damage_and_handle_downing

    target = state.characters[target_id]
    before = target.hp
    _apply_damage_and_handle_downing(
        state,
        state.characters["goblin_1"],
        target,
        amount,
        damage_type,
        _FixedRandom([20]),  # type: ignore[arg-type]  # a passing concentration save, if asked
        load_srd(),
    )
    return before - target.hp


def test_the_four_resistance_and_ac_spells_are_condition_spells() -> None:
    from src.engine.rules import spell_mechanic
    from src.engine.srd_loader import load_srd

    srd = load_srd()
    for index in ("protection-from-energy", "stoneskin", "protection-from-poison", "barkskin"):
        assert spell_mechanic(srd.spells[index]) == "condition", index


def test_stoneskin_halves_physical_damage_but_not_other_types() -> None:
    state = _build_demo_state()
    thorin = state.characters["thorin"]
    thorin.hp = thorin.max_hp = 40
    apply_condition(thorin, Condition(name="stoneskinned", duration_rounds=600))
    assert _hit_with(state, "thorin", 9, "slashing") == 4  # SRD: halved, rounded down
    assert _hit_with(state, "thorin", 9, "bludgeoning") == 4
    assert _hit_with(state, "thorin", 9, "piercing") == 4
    assert _hit_with(state, "thorin", 9, "fire") == 9


def test_protection_from_energy_only_resists_the_chosen_type() -> None:
    state = _build_demo_state()
    thorin = state.characters["thorin"]
    thorin.hp = thorin.max_hp = 40
    apply_condition(thorin, Condition(name="energy_resistant", duration_rounds=600, detail="fire"))
    assert _hit_with(state, "thorin", 9, "fire") == 4
    assert _hit_with(state, "thorin", 9, "cold") == 9
    assert _hit_with(state, "thorin", 9, "slashing") == 9


def test_protection_from_poison_resists_poison_damage() -> None:
    state = _build_demo_state()
    thorin = state.characters["thorin"]
    thorin.hp = thorin.max_hp = 40
    apply_condition(thorin, Condition(name="poison_protected", duration_rounds=600))
    assert _hit_with(state, "thorin", 9, "poison") == 4
    assert _hit_with(state, "thorin", 9, "fire") == 9


def test_resistance_sources_do_not_stack_with_each_other_or_with_rage() -> None:
    state = _build_demo_state()
    thorin = state.characters["thorin"]
    thorin.hp = thorin.max_hp = 40
    thorin.is_raging = True
    apply_condition(thorin, Condition(name="stoneskinned", duration_rounds=600))
    # Rage and Stoneskin both resist slashing - SRD resistance halves once,
    # never to a quarter.
    assert _hit_with(state, "thorin", 8, "slashing") == 4


def test_different_resistance_spells_coexist_on_one_target() -> None:
    # apply_condition keeps one entry per condition NAME, so spells that each
    # grant a resistance need distinct names or the second cast would erase
    # the first - this is why Protection from Energy/Stoneskin/Poison aren't
    # one shared "resistant" condition.
    state = _build_demo_state()
    thorin = state.characters["thorin"]
    apply_condition(thorin, Condition(name="stoneskinned", duration_rounds=600))
    apply_condition(thorin, Condition(name="energy_resistant", duration_rounds=600, detail="fire"))
    apply_condition(thorin, Condition(name="poison_protected", duration_rounds=600))
    assert {c.name for c in thorin.conditions} == {
        "stoneskinned",
        "energy_resistant",
        "poison_protected",
    }


def test_protection_from_energy_records_the_damage_type_named_in_the_cast() -> None:
    state = _build_demo_state()
    elrond = _prepare(state, "elrond", "protection-from-energy", 3)
    _end_turn(state, "thorin")
    _cast(
        state,
        "elrond",
        "protection-from-energy",
        target="thorin",
        raw_text="I cast protection from energy on Thorin against fire",
    )
    (condition,) = state.characters["thorin"].conditions
    assert (condition.name, condition.detail, condition.spell) == (
        "energy_resistant",
        "fire",
        "Protection From Energy",
    )
    assert elrond.concentrating_on == "Protection From Energy"


def test_protection_from_energy_accepts_the_type_as_a_param_too() -> None:
    state = _build_demo_state()
    _prepare(state, "elrond", "protection-from-energy", 3)
    _end_turn(state, "thorin")
    _cast(
        state,
        "elrond",
        "protection-from-energy",
        target="thorin",
        params={"damage_type": "Thunder"},
    )
    assert state.characters["thorin"].conditions[0].detail == "thunder"


@pytest.mark.parametrize(
    "raw_text",
    [
        "I cast protection from energy on Thorin",  # names no type
        "I cast protection from energy on Thorin against fire or cold",  # ambiguous
        "I cast protection from energy on Thorin against necrotic",  # not one of the five
    ],
)
def test_protection_from_energy_without_one_clear_type_is_rejected_before_spending_a_slot(
    raw_text: str,
) -> None:
    state = _build_demo_state()
    elrond = _prepare(state, "elrond", "protection-from-energy", 3)
    _end_turn(state, "thorin")
    with pytest.raises(TurnEngineError, match="acid, cold, fire, lightning, or thunder"):
        _cast(state, "elrond", "protection-from-energy", target="thorin", raw_text=raw_text)
    assert elrond.spell_slots[3] == 1
    assert elrond.concentrating_on is None
    assert not state.characters["thorin"].conditions


def test_stoneskin_is_castable_on_an_ally_and_concentrates() -> None:
    state = _build_demo_state()
    elrond = _prepare(state, "elrond", "stoneskin", 4)
    _end_turn(state, "thorin")
    _cast(state, "elrond", "stoneskin", target="thorin")
    assert has_condition(state.characters["thorin"], "stoneskinned")
    assert elrond.concentrating_on == "Stoneskin"


def test_protection_from_poison_cures_poisoned_and_is_not_concentration() -> None:
    state = _build_demo_state()
    elrond = _prepare(state, "elrond", "protection-from-poison", 2)
    thorin = state.characters["thorin"]
    apply_condition(thorin, Condition(name="poisoned", duration_rounds=5, source="goblin_1"))
    _end_turn(state, "thorin")
    _cast(state, "elrond", "protection-from-poison", target="thorin")
    assert not has_condition(thorin, "poisoned")
    assert has_condition(thorin, "poison_protected")
    assert elrond.concentrating_on is None  # SRD: not a concentration spell
    cure = next(e for e in state.events if e.type == "condition_removed")
    assert (cure.actor, cure.payload["condition"], cure.payload["reason"]) == (
        "thorin",
        "poisoned",
        "cured",
    )


def test_protection_from_poison_on_someone_not_poisoned_just_grants_the_resistance() -> None:
    state = _build_demo_state()
    _prepare(state, "elrond", "protection-from-poison", 2)
    _end_turn(state, "thorin")
    _cast(state, "elrond", "protection-from-poison", target="thorin")
    assert has_condition(state.characters["thorin"], "poison_protected")
    assert not [e for e in state.events if e.type == "condition_removed"]


# --- Barkskin: an AC floor, not a flat add


def _armored_fighter() -> Character:
    return create_character(
        character_id="thorin",
        name="Thorin",
        race_index="human",
        class_index="fighter",
        background_index="acolyte",
        base_ability_scores={"STR": 15, "DEX": 14, "CON": 13, "INT": 12, "WIS": 10, "CHA": 8},
        chosen_skills=["skill-athletics", "skill-perception"],
        chosen_equipment=["chain-mail", "shield"],
    )


def test_barkskin_raises_ac_to_sixteen_and_the_breakdown_still_sums_to_it() -> None:
    from src.engine.rules import armor_ac_breakdown
    from src.engine.srd_loader import load_srd

    srd = load_srd()
    breakdown = armor_ac_breakdown(None, None, 2, None, srd.equipment, ac_floor=16)
    assert sum(v for _, v in breakdown) == 16  # unarmored 10 + DEX 2 = 12, floored to 16
    assert ("Barkskin minimum", 4) in breakdown


def test_barkskin_never_lowers_an_ac_already_above_sixteen() -> None:
    from src.engine.rules import armor_ac_breakdown
    from src.engine.srd_loader import load_srd

    srd = load_srd()
    fighter = _armored_fighter()
    assert fighter.ac == 18  # chain mail 16 + shield 2
    breakdown = armor_ac_breakdown(
        fighter.equipped_armor, fighter.equipped_shield, 2, None, srd.equipment, ac_floor=16
    )
    assert sum(v for _, v in breakdown) == 18
    assert all(label != "Barkskin minimum" for label, _ in breakdown)


def test_casting_barkskin_sets_the_floor_and_marks_concentration() -> None:
    state = _build_demo_state()
    elrond = _prepare(state, "elrond", "barkskin", 2)
    thorin = state.characters["thorin"]
    _end_turn(state, "thorin")
    _cast(state, "elrond", "barkskin", target="thorin")
    assert thorin.ac == 16
    assert has_condition(thorin, "barkskin")
    assert elrond.concentrating_on == "Barkskin"


def test_barkskin_floor_is_reapplied_when_ac_is_recomputed() -> None:
    # The reason it's a condition checked by _recompute_ac rather than a
    # one-time `ac = max(ac, 16)`: anything that recomputes AC afterwards
    # (equipping armor, a level-up) must not silently drop the floor.
    from src.engine.srd_loader import load_srd
    from src.engine.turn_engine import _recompute_ac

    srd = load_srd()
    thorin = _two_person_party()[0]
    apply_condition(thorin, Condition(name="barkskin", duration_rounds=600, source="x"))
    _recompute_ac(thorin, srd)
    assert thorin.ac == 16
    thorin.conditions.clear()
    _recompute_ac(thorin, srd)
    assert thorin.ac == 12, "removing Barkskin restores the real AC"


def test_losing_concentration_on_barkskin_restores_the_targets_real_ac() -> None:
    state = _build_demo_state()
    elrond = _prepare(state, "elrond", "barkskin", 2)
    thorin = state.characters["thorin"]
    _end_turn(state, "thorin")
    _cast(state, "elrond", "barkskin", target="thorin")
    assert thorin.ac == 16
    _goblin_attacks(state, "elrond", [15, 1, 1])  # hit, 3 damage, CON save natural 1: fails
    assert elrond.concentrating_on is None
    assert thorin.ac == 12


def test_barkskin_expiring_by_time_restores_the_targets_real_ac() -> None:
    state = _build_demo_state()
    thorin = state.characters["thorin"]
    apply_condition(thorin, Condition(name="barkskin", duration_rounds=1, source="elrond"))
    thorin.ac = 16
    for actor in ("thorin", "elrond", "goblin_1", "goblin_2"):
        _end_turn(state, actor)
    assert state.round == 2
    assert not has_condition(thorin, "barkskin")
    assert thorin.ac == 12


# ------------------------------------- issue #58: Hunter's Mark / Divine Favor


def _attack(state: GameState, actor_id: str, target_id: str, rng: list[int]) -> None:
    resolve_action(
        state,
        ParsedAction(actor=actor_id, verb="attack", target=target_id, raw_text="attack"),
        _FixedRandom(rng),  # type: ignore[arg-type]
    )


def test_hunters_mark_and_divine_favor_are_condition_spells() -> None:
    from src.engine.rules import spell_mechanic
    from src.engine.srd_loader import load_srd

    srd = load_srd()
    for index in ("hunters-mark", "divine-favor"):
        assert spell_mechanic(srd.spells[index]) == "condition", index


def test_divine_favor_is_a_self_cast_bonus_action_that_leaves_the_main_action() -> None:
    state = _build_demo_state()
    elrond = _prepare(state, "elrond", "divine-favor", 1)
    _end_turn(state, "thorin")
    _cast(state, "elrond", "divine-favor")  # Self range: no target named
    assert has_condition(elrond, "divine_favor")
    assert elrond.concentrating_on == "Divine Favor"
    assert elrond.bonus_action_used is True
    assert state.turn_order[state.current_turn] == "elrond", "a bonus action doesn't end the turn"
    (condition,) = elrond.conditions
    assert (condition.duration_rounds, condition.source) == (10, "elrond")


def test_divine_favor_cannot_be_cast_on_someone_else() -> None:
    state = _build_demo_state()
    _prepare(state, "elrond", "divine-favor", 1)
    _end_turn(state, "thorin")
    with pytest.raises(TurnEngineError, match="only be cast on yourself"):
        _cast(state, "elrond", "divine-favor", target="thorin")


def test_divine_favor_adds_a_d4_of_extra_damage_on_a_weapon_hit() -> None:
    state = _build_demo_state()
    elrond = state.characters["elrond"]
    apply_condition(
        elrond, Condition(name="divine_favor", duration_rounds=10, source="elrond", spell="DF")
    )
    _end_turn(state, "thorin")
    goblin = state.characters["goblin_1"]
    # Dagger: natural 18 hits (+5 vs AC 15); 1d4 weapon die 1 + DEX mod 3 =
    # 4; then Divine Favor's own d4 -> 2. Total 6 of the goblin's 7 HP.
    _attack(state, "elrond", "goblin_1", [18, 1, 2])
    attack = next(e for e in state.events if e.type == "attack_roll")
    assert attack.payload["divine_favor_damage"] == 2
    assert goblin.hp == 1


def test_divine_favor_dice_double_on_a_critical_hit() -> None:
    state = _build_demo_state()
    elrond = state.characters["elrond"]
    apply_condition(
        elrond, Condition(name="divine_favor", duration_rounds=10, source="elrond", spell="DF")
    )
    _end_turn(state, "thorin")
    # Natural 20: the dagger's own dice double (1, 1), and so do Divine
    # Favor's (2d4 -> 2, 3) - extra damage on the same attack doubles like
    # Sneak Attack's and Divine Smite's already do.
    _attack(state, "elrond", "goblin_1", [20, 1, 1, 2, 3])
    attack = next(e for e in state.events if e.type == "attack_roll")
    assert attack.payload["divine_favor_damage"] == 5


def test_divine_favor_rolls_nothing_on_a_miss() -> None:
    state = _build_demo_state()
    apply_condition(
        state.characters["elrond"],
        Condition(name="divine_favor", duration_rounds=10, source="elrond", spell="DF"),
    )
    _end_turn(state, "thorin")
    # A natural 2 misses (2 + 5 = 7 vs AC 15); the list holds only the d20, so
    # a stray damage/bonus roll would raise IndexError.
    _attack(state, "elrond", "goblin_1", [2])
    attack = next(e for e in state.events if e.type == "attack_roll")
    assert attack.payload["hit"] is False
    assert "divine_favor_damage" not in attack.payload


def test_hunters_mark_marks_an_enemy_within_range_as_a_bonus_action() -> None:
    state = _build_demo_state()
    elrond = _prepare(state, "elrond", "hunters-mark", 1)
    _end_turn(state, "thorin")
    _cast(state, "elrond", "hunters-mark", target="goblin_1")
    goblin = state.characters["goblin_1"]
    assert has_condition(goblin, "hunters_marked")
    (mark,) = goblin.conditions
    assert (mark.source, mark.spell, mark.duration_rounds) == ("elrond", "Hunter's Mark", 600)
    assert elrond.concentrating_on == "Hunter's Mark"
    assert state.turn_order[state.current_turn] == "elrond", "bonus action: the turn continues"


def test_hunters_mark_rejects_an_ally_as_its_target() -> None:
    state = _build_demo_state()
    elrond = _prepare(state, "elrond", "hunters-mark", 1)
    _end_turn(state, "thorin")
    with pytest.raises(TurnEngineError, match="not an enemy"):
        _cast(state, "elrond", "hunters-mark", target="thorin")
    assert elrond.spell_slots[1] == 3, "a rejected cast must not burn the slot"
    assert elrond.concentrating_on is None


def test_the_marker_deals_an_extra_d6_when_they_hit_the_marked_creature() -> None:
    state = _build_demo_state()
    apply_condition(
        state.characters["goblin_1"],
        Condition(name="hunters_marked", duration_rounds=600, source="elrond", spell="HM"),
    )
    _end_turn(state, "thorin")
    # Dagger: hit (natural 18), weapon die 1 + 3 = 4, then the mark's d6 -> 4.
    _attack(state, "elrond", "goblin_1", [18, 1, 4])
    attack = next(e for e in state.events if e.type == "attack_roll")
    assert attack.payload["hunters_mark_damage"] == 4
    assert state.characters["goblin_1"].hp == 0  # 7 - (4 + 4), clamped


def test_someone_else_hitting_the_marked_creature_gets_no_bonus() -> None:
    state = _build_demo_state()
    apply_condition(
        state.characters["goblin_1"],
        Condition(name="hunters_marked", duration_rounds=600, source="elrond", spell="HM"),
    )
    thorin = state.characters["thorin"]
    thorin.position = Position(x=1, y=1)  # adjacent to goblin_1 at (2, 1)
    # Longsword: hit, 1d8 die 2 + STR 3 = 5. The list has no d6 for a mark
    # bonus - the mark belongs to Elrond, not Thorin.
    _attack(state, "thorin", "goblin_1", [18, 2])
    attack = next(e for e in state.events if e.type == "attack_roll")
    assert "hunters_mark_damage" not in attack.payload
    assert state.characters["goblin_1"].hp == 2


def test_the_marker_gets_no_bonus_against_an_unmarked_creature() -> None:
    state = _build_demo_state()
    apply_condition(
        state.characters["goblin_1"],
        Condition(name="hunters_marked", duration_rounds=600, source="elrond", spell="HM"),
    )
    _end_turn(state, "thorin")
    _attack(state, "elrond", "goblin_2", [18, 1])  # goblin_2 isn't the marked one
    attack = next(e for e in state.events if e.type == "attack_roll")
    assert "hunters_mark_damage" not in attack.payload


def test_losing_concentration_ends_the_mark() -> None:
    state = _build_demo_state()
    elrond = _prepare(state, "elrond", "hunters-mark", 1)
    _end_turn(state, "thorin")
    _cast(state, "elrond", "hunters-mark", target="goblin_1")
    assert has_condition(state.characters["goblin_1"], "hunters_marked")
    # goblin_2 hits Elrond for 3; his CON save (natural 1) fails.
    _goblin_attacks(state, "elrond", [15, 1, 1], goblin="goblin_2")
    assert elrond.concentrating_on is None
    assert not has_condition(state.characters["goblin_1"], "hunters_marked")


def test_hunters_mark_rejects_a_dead_target_without_dropping_current_concentration() -> None:
    state = _build_demo_state()
    elrond = _prepare(state, "elrond", "hunters-mark", 1)
    _sustain(state, "elrond", "Bless", on=["thorin"])
    state.characters["goblin_1"].is_dead = True
    _end_turn(state, "thorin")
    with pytest.raises(TurnEngineError, match="already dead"):
        _cast(state, "elrond", "hunters-mark", target="goblin_1")
    assert elrond.spell_slots[1] == 3
    assert elrond.concentrating_on == "Bless", "a rejected cast must not end the current one"
    assert has_condition(state.characters["thorin"], "blessed")


# --------------- issue #61: Sanctuary / Mirror Image / Protection from Evil and Good


def _ward(state: GameState, target_id: str, caster_id: str = "elrond") -> None:
    apply_condition(
        state.characters[target_id],
        Condition(name="warded", duration_rounds=10, source=caster_id, spell="Sanctuary"),
    )


def _images(state: GameState, target_id: str, count: int) -> None:
    apply_condition(
        state.characters[target_id],
        Condition(
            name="mirror_image",
            duration_rounds=10,
            source=target_id,
            spell="Mirror Image",
            detail=str(count),
        ),
    )


def test_the_three_redirect_spells_are_condition_spells() -> None:
    from src.engine.rules import spell_mechanic
    from src.engine.srd_loader import load_srd

    srd = load_srd()
    for index in ("sanctuary", "mirror-image", "protection-from-evil-and-good"):
        assert spell_mechanic(srd.spells[index]) == "condition", index


# --- Sanctuary


def test_sanctuary_is_a_bonus_action_ward_on_an_ally_and_not_concentration() -> None:
    state = _build_demo_state()
    elrond = _prepare(state, "elrond", "sanctuary", 1)
    _end_turn(state, "thorin")
    _cast(state, "elrond", "sanctuary", target="thorin")
    (ward,) = state.characters["thorin"].conditions
    assert (ward.name, ward.duration_rounds, ward.source, ward.spell) == (
        "warded",
        10,
        "elrond",
        "Sanctuary",
    )
    assert elrond.concentrating_on is None  # SRD: 1 minute, no concentration
    assert state.turn_order[state.current_turn] == "elrond", "a bonus action keeps the turn"


def test_an_attacker_who_fails_the_wisdom_save_loses_the_attack() -> None:
    state = _build_demo_state()
    elrond = state.characters["elrond"]
    _ward(state, "elrond")
    # Sanctuary DC = 8 + proficiency 2 + Elrond's INT mod 2 = 12; the goblin's
    # WIS 8 is -1. Natural 5 -> 4: fails. The list holds only that d20 - a
    # stray attack roll would raise IndexError.
    _goblin_attacks(state, "elrond", [5])
    save = next(e for e in state.events if e.payload.get("kind") == "sanctuary")
    assert (save.actor, save.payload["success"], save.payload["dc"]) == ("goblin_1", False, 12)
    assert save.payload["target"] == "elrond"
    blocked = next(e for e in state.events if e.type == "sanctuary_blocked")
    assert (blocked.actor, blocked.payload["target"]) == ("goblin_1", "elrond")
    assert not [e for e in state.events if e.type == "attack_roll"]
    assert elrond.hp == 7
    assert state.turn_order[state.current_turn] != "goblin_1", (
        "the lost attack still spent the turn"
    )


def test_an_attacker_who_passes_the_wisdom_save_attacks_normally() -> None:
    state = _build_demo_state()
    _ward(state, "elrond")
    # Natural 13 - 1 = 12 >= DC 12 passes (ties go to the roller); then the
    # ordinary attack: natural 15, damage die 1.
    _goblin_attacks(state, "elrond", [13, 15, 1])
    save = next(e for e in state.events if e.payload.get("kind") == "sanctuary")
    assert save.payload["success"] is True
    assert next(e for e in state.events if e.type == "attack_roll")
    assert not [e for e in state.events if e.type == "sanctuary_blocked"]


def test_sanctuary_ends_when_the_warded_creature_attacks() -> None:
    state = _build_demo_state()
    elrond = state.characters["elrond"]
    _ward(state, "elrond")
    _end_turn(state, "thorin")
    _attack(state, "elrond", "goblin_1", [2])  # a miss still counts as attacking
    assert not has_condition(elrond, "warded")
    ended = next(e for e in state.events if e.type == "condition_removed")
    assert (ended.actor, ended.payload["condition"], ended.payload["reason"]) == (
        "elrond",
        "warded",
        "attacked",
    )


def test_sanctuary_ends_when_the_warded_creature_casts_a_harmful_spell() -> None:
    state = _build_demo_state()
    elrond = _prepare(state, "elrond", "bane", 1)
    _ward(state, "elrond")
    _end_turn(state, "thorin")
    _cast(state, "elrond", "bane", target="goblin_1", rng=[15])
    assert not has_condition(elrond, "warded")


def test_sanctuary_survives_a_beneficial_spell() -> None:
    state = _build_demo_state()
    elrond = state.characters["elrond"]
    _ward(state, "elrond")
    _end_turn(state, "thorin")
    _cast(state, "elrond", "mage armor", target="elrond")
    assert has_condition(elrond, "warded")


def test_sanctuary_also_stops_a_spell_attack() -> None:
    state = _build_demo_state()
    _ward(state, "goblin_1", caster_id="not_a_caster")  # unresolvable caster -> DC 10
    _end_turn(state, "thorin")
    # Elrond's Fire Bolt at the warded goblin: his WIS save is +3 (WIS 13 +1,
    # proficient in WIS saves, +2); natural 2 -> 5 < DC 10 fails.
    _cast(state, "elrond", "fire bolt", target="goblin_1", rng=[2])
    assert next(e for e in state.events if e.type == "sanctuary_blocked")
    assert state.characters["goblin_1"].hp == 7


# --- Mirror Image


def test_mirror_image_creates_three_images_and_is_not_concentration() -> None:
    state = _build_demo_state()
    elrond = _prepare(state, "elrond", "mirror-image", 2)
    _end_turn(state, "thorin")
    _cast(state, "elrond", "mirror-image")  # Self range: no target named
    (image,) = elrond.conditions
    assert (image.name, image.detail, image.duration_rounds) == ("mirror_image", "3", 10)
    assert elrond.concentrating_on is None


def test_mirror_image_cannot_be_cast_on_someone_else() -> None:
    state = _build_demo_state()
    elrond = _prepare(state, "elrond", "mirror-image", 2)
    _end_turn(state, "thorin")
    with pytest.raises(TurnEngineError, match="only be cast on yourself"):
        _cast(state, "elrond", "mirror-image", target="thorin")
    assert elrond.spell_slots[2] == 1


@pytest.mark.parametrize(
    ("images", "redirect_natural", "redirected"),
    [
        (3, 5, False),  # three images: a 6 or higher redirects
        (3, 6, True),
        (2, 7, False),  # two: 8 or higher
        (2, 8, True),
        (1, 10, False),  # one: 11 or higher
        (1, 11, True),
    ],
)
def test_the_redirect_threshold_depends_on_how_many_images_remain(
    images: int, redirect_natural: int, redirected: bool
) -> None:
    state = _build_demo_state()
    _images(state, "elrond", images)
    # Redirected: the goblin then rolls its attack against the duplicate (a
    # natural 2 here, a miss), and the real Elrond is never rolled against.
    # Not redirected: the same value list falls through to a real attack
    # (natural 15, damage die 1) - so the two outcomes consume different RNG.
    _goblin_attacks(
        state, "elrond", [redirect_natural, 2] if redirected else [redirect_natural, 15, 1]
    )
    events = {e.type for e in state.events}
    assert ("mirror_image" in events) is redirected
    assert ("attack_roll" in events) is (not redirected)


def test_an_attack_that_hits_a_duplicate_destroys_it_and_never_touches_the_real_target() -> None:
    state = _build_demo_state()
    elrond = state.characters["elrond"]
    _images(state, "elrond", 3)
    # Redirect natural 6; duplicate AC = 10 + Elrond's DEX mod 3 = 13; the
    # goblin's natural 10 + 4 = 14 >= 13 hits it.
    _goblin_attacks(state, "elrond", [6, 10])
    event = next(e for e in state.events if e.type == "mirror_image")
    assert (event.actor, event.payload["target"]) == ("goblin_1", "elrond")
    assert event.payload["image_hit"] is True
    assert event.payload["images_remaining"] == 2
    assert elrond.hp == 7
    assert elrond.conditions[0].detail == "2"
    assert not [e for e in state.events if e.type == "attack_roll"]


def test_an_attack_that_misses_a_duplicate_leaves_it_standing() -> None:
    state = _build_demo_state()
    _images(state, "elrond", 3)
    _goblin_attacks(state, "elrond", [6, 4])  # 4 + 4 = 8 < 13
    event = next(e for e in state.events if e.type == "mirror_image")
    assert event.payload["image_hit"] is False
    assert event.payload["images_remaining"] == 3
    assert state.characters["elrond"].conditions[0].detail == "3"


def test_destroying_the_last_duplicate_ends_the_spell() -> None:
    state = _build_demo_state()
    elrond = state.characters["elrond"]
    _images(state, "elrond", 1)
    _goblin_attacks(state, "elrond", [11, 10])
    assert not has_condition(elrond, "mirror_image")
    removed = next(e for e in state.events if e.type == "condition_removed")
    assert (removed.payload["condition"], removed.payload["reason"]) == (
        "mirror_image",
        "all duplicates destroyed",
    )


def test_a_spell_attack_aimed_at_the_image_owner_can_hit_a_duplicate_too() -> None:
    state = _build_demo_state()
    goblin = state.characters["goblin_1"]
    _images(state, "goblin_1", 3)
    _end_turn(state, "thorin")
    # Out of melee: a ranged spell attack with a hostile within 5ft has
    # disadvantage (issue #80), which isn't what this test is about.
    state.characters["elrond"].position = Position(x=0, y=0)
    # Fire Bolt: redirect natural 6; the duplicate's AC is 10 + the goblin's
    # DEX mod (14 -> +2) = 12; Elrond's natural 8 + 4 (INT +2, proficiency +2) =
    # 12 >= 12 hits it.
    _cast(state, "elrond", "fire bolt", target="goblin_1", rng=[6, 8])
    assert next(e for e in state.events if e.type == "mirror_image").payload["image_hit"] is True
    assert goblin.hp == 7, "the real goblin is untouched"


def test_a_creature_without_mirror_image_rolls_no_redirect_die() -> None:
    # Regression guard for every pre-existing fixed-RNG attack fixture: the
    # d20 redirect check must consume nothing when there's no spell.
    state = _build_demo_state()
    _goblin_attacks(state, "elrond", [15, 1, 10])
    assert next(e for e in state.events if e.type == "attack_roll").payload["roll_total"] == 19


# --- Protection from Evil and Good


def test_protection_from_evil_and_good_is_a_concentration_ward_on_an_ally() -> None:
    state = _build_demo_state()
    elrond = _prepare(state, "elrond", "protection-from-evil-and-good", 1)
    _end_turn(state, "thorin")
    _cast(state, "elrond", "protection-from-evil-and-good", target="thorin")
    (ward,) = state.characters["thorin"].conditions
    assert (ward.name, ward.duration_rounds) == ("protected_from_evil", 100)
    assert elrond.concentrating_on == "Protection from Evil and Good"


@pytest.mark.parametrize(
    "creature_type", ["undead", "fiend", "celestial", "fey", "elemental", "aberration"]
)
def test_the_six_protected_against_types_attack_a_warded_creature_with_disadvantage(
    creature_type: str,
) -> None:
    from src.engine.rules import protected_from_evil_disadvantage
    from src.engine.srd_loader import load_srd

    srd = load_srd()
    monster_index = next(i for i, m in srd.monsters.items() if m["type"] == creature_type)
    state = _build_demo_state()
    attacker = state.characters["goblin_1"]
    attacker.monster_index = monster_index
    warded = state.characters["elrond"]
    assert protected_from_evil_disadvantage(attacker, warded, srd) is False  # no ward yet
    apply_condition(warded, Condition(name="protected_from_evil", duration_rounds=100))
    assert protected_from_evil_disadvantage(attacker, warded, srd) is True


@pytest.mark.parametrize("creature_type", ["humanoid", "beast", "dragon", "monstrosity"])
def test_other_creature_types_are_not_hampered_by_the_ward(creature_type: str) -> None:
    from src.engine.rules import protected_from_evil_disadvantage
    from src.engine.srd_loader import load_srd

    srd = load_srd()
    monster_index = next(i for i, m in srd.monsters.items() if m["type"] == creature_type)
    state = _build_demo_state()
    attacker = state.characters["goblin_1"]
    attacker.monster_index = monster_index
    warded = state.characters["elrond"]
    apply_condition(warded, Condition(name="protected_from_evil", duration_rounds=100))
    assert protected_from_evil_disadvantage(attacker, warded, srd) is False


def test_an_undead_attacker_rolls_with_disadvantage_against_the_warded_creature() -> None:
    state = _build_demo_state()
    state.characters["goblin_2"].monster_index = "skeleton"  # undead, adjacent to Elrond
    apply_condition(
        state.characters["elrond"], Condition(name="protected_from_evil", duration_rounds=100)
    )
    # Disadvantage: two d20s, the lower kept (20 then 3 -> 3; +4 = 7 < AC 13).
    _goblin_attacks(state, "elrond", [20, 3], goblin="goblin_2")
    attack = next(e for e in state.events if e.type == "attack_roll")
    assert attack.payload["natural"] == 3
    assert attack.payload["hit"] is False


def test_a_humanoid_attacker_is_unaffected_by_the_ward() -> None:
    state = _build_demo_state()
    apply_condition(
        state.characters["elrond"], Condition(name="protected_from_evil", duration_rounds=100)
    )
    _goblin_attacks(state, "elrond", [20, 3, 10])  # one d20, damage die, CON save
    assert next(e for e in state.events if e.type == "attack_roll").payload["natural"] == 20


def test_a_non_monster_attacker_is_unaffected_by_the_ward() -> None:
    from src.engine.rules import protected_from_evil_disadvantage
    from src.engine.srd_loader import load_srd

    state = _build_demo_state()
    apply_condition(
        state.characters["elrond"], Condition(name="protected_from_evil", duration_rounds=100)
    )
    assert (
        protected_from_evil_disadvantage(
            state.characters["thorin"], state.characters["elrond"], load_srd()
        )
        is False
    )


def test_a_condition_spell_records_who_cast_it_before_the_per_target_effects() -> None:
    # The condition_applied events have the TARGET as their actor, so without
    # an explicit spell_cast the narrator had nothing saying the caster did it
    # (found live: a Cleric's Sanctuary was narrated as someone else's cast).
    state = _build_demo_state()
    _prepare(state, "elrond", "sanctuary", 1)
    _end_turn(state, "thorin")
    _cast(state, "elrond", "sanctuary", target="thorin")
    kinds = [e.type for e in state.events if e.type in ("spell_cast", "condition_applied")]
    assert kinds == ["spell_cast", "condition_applied"]
    cast = next(e for e in state.events if e.type == "spell_cast")
    assert (cast.actor, cast.payload) == ("elrond", {"spell": "Sanctuary", "targets": ["thorin"]})
