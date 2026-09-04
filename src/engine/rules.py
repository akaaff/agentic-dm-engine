"""Attack, damage, saving throw, and skill check resolution.

Pure functions over Character/RollResult - no GameState/Event coupling here,
so combat math can be tested in complete isolation. Day 7 wires these into
the turn loop and translates results into Events.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from src.engine.dice import RollResult, roll, roll_d20
from src.engine.state import AbilityScore, Character


@dataclass(frozen=True)
class AttackResult:
    attack_roll: RollResult
    hit: bool
    critical: bool
    damage: int | None
    """None on a miss."""
    damage_type: str | None


def resolve_attack(
    defender_ac: int,
    attack_bonus: int,
    damage_dice_count: int,
    damage_dice_sides: int,
    damage_bonus: int,
    damage_type: str,
    rng: random.Random,
    advantage: bool = False,
    disadvantage: bool = False,
) -> AttackResult:
    """A natural 1 always misses, a natural 20 always hits and doubles the
    damage dice (not the flat bonus), per SRD rules."""
    attack_roll = roll_d20(
        modifier=attack_bonus, rng=rng, advantage=advantage, disadvantage=disadvantage
    )
    natural = attack_roll.kept[0]

    if natural == 1:
        return AttackResult(attack_roll, hit=False, critical=False, damage=None, damage_type=None)

    critical = natural == 20
    hit = critical or attack_roll.total >= defender_ac
    if not hit:
        return AttackResult(attack_roll, hit=False, critical=False, damage=None, damage_type=None)

    dice_count = damage_dice_count * 2 if critical else damage_dice_count
    damage_roll = roll(dice_count, damage_dice_sides, modifier=damage_bonus, rng=rng)
    damage = max(0, damage_roll.total)
    return AttackResult(
        attack_roll, hit=True, critical=critical, damage=damage, damage_type=damage_type
    )


def apply_damage(character: Character, amount: int) -> int:
    """Mutates character.hp, clamped at 0. Returns the actual HP lost
    (may be less than `amount` if it would have gone negative)."""
    actual = min(amount, character.hp)
    character.hp = max(0, character.hp - amount)
    return actual


def resolve_saving_throw(
    save_bonus: int,
    dc: int,
    rng: random.Random,
    advantage: bool = False,
    disadvantage: bool = False,
) -> tuple[RollResult, bool]:
    result = roll_d20(modifier=save_bonus, rng=rng, advantage=advantage, disadvantage=disadvantage)
    return result, result.total >= dc


def resolve_skill_check(
    modifier: int,
    dc: int,
    rng: random.Random,
    advantage: bool = False,
    disadvantage: bool = False,
) -> tuple[RollResult, bool]:
    result = roll_d20(modifier=modifier, rng=rng, advantage=advantage, disadvantage=disadvantage)
    return result, result.total >= dc


def ability_modifier(score: int) -> int:
    return (score - 10) // 2


def ability_check_modifier(
    character: Character, ability: AbilityScore, proficient: bool = False
) -> int:
    mod = ability_modifier(character.stats[ability])
    return mod + character.proficiency_bonus if proficient else mod
