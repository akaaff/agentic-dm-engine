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
) -> RollResult:
    """Advantage and disadvantage cancel each other out per SRD rules."""
    if advantage and disadvantage:
        advantage = disadvantage = False
    rng = rng or random.Random()

    if not (advantage or disadvantage):
        d = rng.randint(1, 20)
        return RollResult(dice=[d], kept=[d], modifier=modifier, total=d + modifier)

    a, b = rng.randint(1, 20), rng.randint(1, 20)
    chosen = max(a, b) if advantage else min(a, b)
    return RollResult(dice=[a, b], kept=[chosen], modifier=modifier, total=chosen + modifier)
