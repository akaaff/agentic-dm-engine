"""Short/long rest mechanics (Phase 9G of the rules-completeness initiative -
see CLAUDE.md). Kept as its own small module rather than folded into
campaign_runner.py or rules.py: these two functions are pure Character
mutations with no scene/campaign-chain knowledge of their own (campaign_runner
just calls them when it walks past a rest scene), and rules.py is reserved for
the deterministic combat/check math turn_engine consumes - resting is neither.

Written when this was still a level-1-only project, before Phase 9J added
real leveling (roughly levels 1-5) - and never revisited afterward, which
was a real bug (issue #20, SRD mechanics audit): apply_long_rest restored
spell slots from the level-1-only LEVEL_1_SPELL_SLOTS table and hardcoded
hit_dice_remaining back to 1, regardless of the character's actual level.
A level 3-5 caster who rested was silently reset to their level-1 slot
layout. Fixed to read character.level via character_creation.
SPELL_SLOTS_BY_LEVEL and set hit_dice_remaining = character.level instead -
a long rest's "full spell slots"/"full hit dice" are looked up/computed
fresh each time rather than snapshotting a max onto Character at creation,
so there's no second stored copy to drift out of sync with level_up.
"""

from __future__ import annotations

import random

from src.engine.character_creation import (
    CLASS_RESOURCES_AT_LEVEL_1,
    SPELL_SLOTS_BY_LEVEL,
)
from src.engine.dice import roll
from src.engine.rules import ability_modifier, set_exhaustion_level
from src.engine.state import Character

_SHORT_REST_RESOURCES = {"second_wind", "wild_shape"}
"""Phase 9I/issue #24: class_resources keys that recover on a short rest,
per SRD (Second Wind, Wild Shape). Everything else in
_max_class_resources's output (currently "rage" and "ki") recovers on a
long rest instead - see apply_long_rest."""


def _max_class_resources(class_index: str | None, level: int) -> dict[str, int]:
    """Every class_resource's current max for `class_index` at `level` -
    CLASS_RESOURCES_AT_LEVEL_1's fixed value for Second Wind/Rage (a
    documented simplification: those don't scale with level in this
    project), plus the two resources that genuinely do (issue #24): Monk's
    Ki (= character level, mirroring level_up's own identical formula) and
    Druid's Wild Shape (fixed at 2 uses, but only available from level 2
    on - absent below that, same as a level-1 Monk having no "ki" key at
    all). Used by both rest functions so a long rest's full-dict
    replacement and a short rest's single-key restore agree on the same
    numbers, rather than each hardcoding its own copy."""
    resources = dict(CLASS_RESOURCES_AT_LEVEL_1.get(class_index or "", {}))
    if class_index == "monk":
        resources["ki"] = level
    if class_index == "druid" and level >= 2:
        resources["wild_shape"] = 2
    return resources


def apply_short_rest(party: list[Character], rng: random.Random) -> None:
    """SRD short rests let a player choose how many hit dice to spend; this
    engine simplifies to "spend everything available" (the issue's own
    scope), which at level 1 is just the character's one die. Each die heals
    1d(hit_die_sides) + CON modifier (floored at 0 - a rest should never
    *cost* hp even for a character with a negative CON mod, which the bare
    SRD math doesn't rule out), clamped at max_hp. `hit_dice_remaining` is
    left at 0 afterward for anyone who rested.

    Also restores any class_resources that recover on a short rest (Second
    Wind, Wild Shape) to their current max via _max_class_resources - not
    just a level-1 value, now that issue #24 added a resource (Wild Shape)
    that's only available from level 2 on. A character with no hit dice
    remaining (already spent this long-rest cycle) still gets their
    short-rest class resources restored - those are two independent SRD
    mechanics, not the same budget.
    """
    for character in party:
        max_resources = _max_class_resources(character.class_index, character.level)
        for resource in _SHORT_REST_RESOURCES:
            if resource in max_resources:
                character.class_resources[resource] = max_resources[resource]

        if character.hit_dice_remaining <= 0:
            continue
        con_mod = ability_modifier(character.stats["CON"])
        while character.hit_dice_remaining > 0:
            healed = max(0, roll(1, character.hit_die_sides, modifier=con_mod, rng=rng).total)
            character.hp = min(character.max_hp, character.hp + healed)
            character.hit_dice_remaining -= 1


def apply_long_rest(party: list[Character]) -> None:
    """Full HP, full spell slots (per class_index and the character's real
    level via SPELL_SLOTS_BY_LEVEL, empty for non-casters/monsters/levels
    with no entry), hit dice reset to one per character level (issue #20 -
    was hardcoded to 1 regardless of level), exhaustion reduced by one level
    (rules.set_exhaustion_level already floors at 0), every class_resource
    restored to its current max via _max_class_resources (short-rest ones
    like Second Wind/Wild Shape recover here too - a long rest is a
    superset of a short rest's benefits, per SRD) - issue #24 found and
    fixed a real bug here: this used to rebuild class_resources from the
    level-1-only table unconditionally, which would have silently wiped a
    leveled Monk's Ki back to nothing (a level-1 Monk has no "ki" key at
    all) every time they took a long rest, the exact same class of bug
    issue #20 already found and fixed for spell slots/hit dice - Rage ends
    (Character.is_raging - this engine doesn't model rage's real mid-combat
    duration/maintenance conditions, so "clears on any rest" is the
    documented substitute, not a silent omission), and a Half-Orc's
    Relentless Endurance (issue #23) becomes available again. Doesn't touch
    a Druid's Wild Shape state either way (issue #24) - resting mid-combat
    encounter while transformed isn't a scenario campaign_runner's rest
    flow can actually reach, so it's left alone rather than guessed at."""
    for character in party:
        character.hp = character.max_hp
        character.spell_slots = dict(
            SPELL_SLOTS_BY_LEVEL.get(character.class_index or "", {}).get(character.level, {})
        )
        character.hit_dice_remaining = character.level
        set_exhaustion_level(character, character.exhaustion_level - 1)
        character.class_resources = _max_class_resources(character.class_index, character.level)
        character.is_raging = False
        character.used_relentless_endurance_this_rest = False
