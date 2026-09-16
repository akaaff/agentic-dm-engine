"""Phase 9G: short/long rest mechanics - hand-computed fixtures, same rigor
as the rest of this engine's deterministic pieces (see CLAUDE.md)."""

from __future__ import annotations

from src.engine.position import Position
from src.engine.resting import apply_long_rest, apply_short_rest
from src.engine.state import Character


class _FixedRandom:
    def __init__(self, values: list[int]) -> None:
        self._values = list(values)

    def randint(self, a: int, b: int) -> int:
        return self._values.pop(0)


def _character(**overrides: object) -> Character:
    base: dict[str, object] = dict(
        id="pc1",
        name="Rian",
        is_pc=True,
        hp=4,
        max_hp=10,
        ac=15,
        position=Position(x=0, y=0),
        stats={"STR": 14, "DEX": 12, "CON": 14, "INT": 10, "WIS": 10, "CHA": 10},
        proficiency_bonus=2,
        speed=30,
        race="Human",
        class_="Fighter",
        background="Acolyte",
        class_index="fighter",
        hit_die_sides=10,
        hit_dice_remaining=1,
    )
    base.update(overrides)
    return Character(**base)  # type: ignore[arg-type]


def test_apply_short_rest_heals_the_fixed_roll_plus_con_mod_and_spends_the_hit_die() -> None:
    # CON 14 -> mod +2. Fixed d10 roll of 6 -> 6 + 2 = 8 healed; 4 + 8 = 12,
    # clamped at max_hp 10.
    character = _character(hp=4, max_hp=10, hit_dice_remaining=1)

    apply_short_rest([character], _FixedRandom([6]))  # type: ignore[arg-type]

    assert character.hp == 10
    assert character.hit_dice_remaining == 0


def test_apply_short_rest_heals_without_clamping_when_under_max() -> None:
    # CON 14 -> mod +2. Fixed d10 roll of 3 -> 3 + 2 = 5 healed; 2 + 5 = 7,
    # under max_hp 20, so no clamping.
    character = _character(hp=2, max_hp=20, hit_dice_remaining=1)

    apply_short_rest([character], _FixedRandom([3]))  # type: ignore[arg-type]

    assert character.hp == 7
    assert character.hit_dice_remaining == 0


def test_apply_short_rest_floors_healing_at_zero_for_a_bad_roll_and_negative_con_mod() -> None:
    # CON 8 -> mod -1. Fixed d10 roll of 1 -> 1 + (-1) = 0 healed, not negative.
    character = _character(
        hp=5,
        max_hp=10,
        stats={"STR": 14, "DEX": 12, "CON": 8, "INT": 10, "WIS": 10, "CHA": 10},
        hit_dice_remaining=1,
    )

    apply_short_rest([character], _FixedRandom([1]))  # type: ignore[arg-type]

    assert character.hp == 5
    assert character.hit_dice_remaining == 0


def test_apply_short_rest_skips_a_character_with_no_hit_dice_remaining() -> None:
    character = _character(hp=1, max_hp=10, hit_dice_remaining=0)

    apply_short_rest([character], _FixedRandom([]))  # type: ignore[arg-type]

    assert character.hp == 1
    assert character.hit_dice_remaining == 0


def test_apply_long_rest_fully_restores_hp_spell_slots_and_hit_dice() -> None:
    character = _character(
        hp=1,
        max_hp=10,
        class_index="wizard",
        hit_dice_remaining=0,
        spell_slots={1: 0},
    )

    apply_long_rest([character])

    assert character.hp == character.max_hp == 10
    assert character.spell_slots == {1: 2}
    assert character.hit_dice_remaining == 1


def test_apply_long_rest_restores_slots_and_hit_dice_for_the_characters_real_level() -> None:
    # Issue #20 regression: a level-5 wizard's slots are {1:4, 2:3, 3:2} per
    # SPELL_SLOTS_BY_LEVEL["wizard"][5], not the level-1 row {1:2}, and they
    # get 5 hit dice back, not 1.
    character = _character(
        hp=1,
        max_hp=40,
        class_index="wizard",
        level=5,
        hit_dice_remaining=0,
        spell_slots={1: 0, 2: 0, 3: 0},
    )

    apply_long_rest([character])

    assert character.spell_slots == {1: 4, 2: 3, 3: 2}
    assert character.hit_dice_remaining == 5


def test_apply_long_rest_gives_a_non_caster_empty_spell_slots() -> None:
    character = _character(class_index="fighter", spell_slots={})

    apply_long_rest([character])

    assert character.spell_slots == {}


def test_apply_long_rest_refreshes_relentless_endurance() -> None:
    # Issue #23 (Half-Orc's Relentless Endurance): available again after a
    # long rest, once it's been used.
    character = _character(used_relentless_endurance_this_rest=True)

    apply_long_rest([character])

    assert character.used_relentless_endurance_this_rest is False


def test_apply_long_rest_does_not_wipe_a_leveled_monks_ki() -> None:
    # Issue #24 regression: apply_long_rest used to unconditionally rebuild
    # class_resources from the level-1-only table, which has no "ki" entry
    # at all - a leveled Monk's Ki would have been silently wiped to
    # nothing on every long rest, the exact class of bug issue #20 already
    # found and fixed for spell slots/hit dice.
    character = _character(class_index="monk", level=3, class_resources={"ki": 1}, spell_slots={})

    apply_long_rest([character])

    assert character.class_resources == {"ki": 3}


def test_apply_long_rest_restores_wild_shape_uses_for_a_leveled_druid() -> None:
    character = _character(
        class_index="druid", level=4, class_resources={"wild_shape": 0}, spell_slots={}
    )

    apply_long_rest([character])

    assert character.class_resources == {"wild_shape": 2}


def test_apply_long_rest_gives_a_level_1_druid_no_wild_shape_entry() -> None:
    # SRD: no Wild Shape at all until level 2.
    character = _character(class_index="druid", level=1, spell_slots={})

    apply_long_rest([character])

    assert character.class_resources == {}


def test_apply_short_rest_restores_wild_shape_but_not_ki() -> None:
    # Wild Shape recovers on a short OR long rest (per SRD); Ki only
    # recovers on a long rest - a short rest should touch one, not both.
    character = _character(
        class_index="druid", level=4, class_resources={"wild_shape": 0}, hit_dice_remaining=0
    )

    apply_short_rest([character], _FixedRandom([]))  # type: ignore[arg-type]

    assert character.class_resources["wild_shape"] == 2

    monk = _character(class_index="monk", level=3, class_resources={"ki": 0}, hit_dice_remaining=0)

    apply_short_rest([monk], _FixedRandom([]))  # type: ignore[arg-type]

    assert monk.class_resources["ki"] == 0  # untouched - Ki isn't a short-rest resource


def test_apply_short_rest_triggers_arcane_recovery_for_a_wizard() -> None:
    # Issue #25: level-3 Wizard, budget = ceil(3/2) = 2. Recovers the
    # lowest missing slot level first (this engine has no per-slot choice
    # UI - see apply_short_rest's own docstring) - 2 first-level slots cost
    # 2 budget points, exhausting it before the 2nd-level slot is touched.
    character = _character(
        class_index="wizard",
        level=3,
        spell_slots={1: 0, 2: 0},
        class_resources={"arcane_recovery": 1},
        hit_dice_remaining=0,
    )

    apply_short_rest([character], _FixedRandom([]))  # type: ignore[arg-type]

    assert character.spell_slots == {1: 2, 2: 0}
    assert character.class_resources["arcane_recovery"] == 0


def test_apply_short_rest_arcane_recovery_only_triggers_once_per_day() -> None:
    character = _character(
        class_index="wizard",
        level=3,
        spell_slots={1: 0, 2: 0},
        class_resources={"arcane_recovery": 0},  # already used today
        hit_dice_remaining=0,
    )

    apply_short_rest([character], _FixedRandom([]))  # type: ignore[arg-type]

    assert character.spell_slots == {1: 0, 2: 0}  # untouched


def test_apply_short_rest_does_not_trigger_arcane_recovery_for_a_non_wizard() -> None:
    character = _character(
        class_index="fighter",
        spell_slots={1: 0},
        class_resources={"arcane_recovery": 1},
        hit_dice_remaining=0,
    )

    apply_short_rest([character], _FixedRandom([]))  # type: ignore[arg-type]

    assert character.spell_slots == {1: 0}
    assert character.class_resources["arcane_recovery"] == 1  # untouched


def test_apply_long_rest_restores_arcane_recovery() -> None:
    character = _character(
        class_index="wizard", spell_slots={}, class_resources={"arcane_recovery": 0}
    )

    apply_long_rest([character])

    assert character.class_resources == {"arcane_recovery": 1}


def test_apply_long_rest_reduces_exhaustion_by_one() -> None:
    character = _character(exhaustion_level=3)

    apply_long_rest([character])

    assert character.exhaustion_level == 2


def test_apply_long_rest_floors_exhaustion_at_zero() -> None:
    character = _character(exhaustion_level=0)

    apply_long_rest([character])

    assert character.exhaustion_level == 0
