"""Dice rolling - the only source of randomness in the engine.

Every function takes an injectable random.Random so combat is exactly
reproducible under a fixed seed (needed for the hand-computed-fixture tests
this engine relies on throughout the build).
"""

from __future__ import annotations

import random
from dataclasses import dataclass


@dataclass(frozen=True)
class RollResult:
    dice: list[int]
    """Every individual die actually rolled (both dice, for advantage/disadvantage)."""
    kept: list[int]
    """The subset of `dice` that counted toward `total`."""
    modifier: int
    total: int


def roll(n: int, sides: int, modifier: int = 0, rng: random.Random | None = None) -> RollResult:
    rng = rng or random.Random()
    dice = [rng.randint(1, sides) for _ in range(n)]
    return RollResult(dice=dice, kept=dice, modifier=modifier, total=sum(dice) + modifier)


def roll_d20(
    modifier: int = 0,
    rng: random.Random | None = None,
    advantage: bool = False,
    disadvantage: bool = False,
    reroll_on_natural_1: bool = False,
) -> RollResult:
    """Advantage and disadvantage cancel each other out per SRD rules.

    `reroll_on_natural_1` (Halfling's Lucky trait, issue #23): if the kept
    die comes up a natural 1, roll one more d20 and use that instead - SRD's
    "reroll the die and must use the new roll," modeled as automatic (this
    engine has no player-choice prompt to decline it) rather than optional.
    The extra die is appended to `dice` (so a caller inspecting the full
    roll history can see it happened) but only ever affects `kept`/`total`."""
    if advantage and disadvantage:
        advantage = disadvantage = False
    rng = rng or random.Random()

    if not (advantage or disadvantage):
        dice = [rng.randint(1, 20)]
    else:
        a, b = rng.randint(1, 20), rng.randint(1, 20)
        dice = [a, b]

    kept = max(dice) if advantage else (min(dice) if disadvantage else dice[0])
    if reroll_on_natural_1 and kept == 1:
        kept = rng.randint(1, 20)
        dice.append(kept)

    return RollResult(dice=dice, kept=[kept], modifier=modifier, total=kept + modifier)
