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

_SHORT_REST_RESOURCES = {"second_wind"}
"""Phase 9I: class_resources keys that recover on a short rest, per SRD
(Second Wind). Everything else in CLASS_RESOURCES_AT_LEVEL_1 (currently
just "rage") recovers on a long rest instead - see apply_long_rest."""


def apply_short_rest(party: list[Character], rng: random.Random) -> None:
    """SRD short rests let a player choose how many hit dice to spend; this
    engine simplifies to "spend everything available" (the issue's own
    scope), which at level 1 is just the character's one die. Each die heals
    1d(hit_die_sides) + CON modifier (floored at 0 - a rest should never
    *cost* hp even for a character with a negative CON mod, which the bare
    SRD math doesn't rule out), clamped at max_hp. `hit_dice_remaining` is
    left at 0 afterward for anyone who rested.

    Also restores any Phase 9I class_resources that recover on a short rest
    (Second Wind) to their level-1 max - real leveling (Phase 9J) doesn't
    scale these resources yet, so "level-1 max" is the only max there is.
    A character with no hit dice remaining (already spent this long-rest
    cycle) still gets their short-rest class resources restored - those are
    two independent SRD mechanics, not the same budget.
    """
    for character in party:
        for resource in _SHORT_REST_RESOURCES:
            max_uses = CLASS_RESOURCES_AT_LEVEL_1.get(character.class_index or "", {}).get(resource)
            if max_uses is not None:
                character.class_resources[resource] = max_uses

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
    restored to its level-1 max (short-rest ones like Second Wind recover
    here too - a long rest is a superset of a short rest's benefits, per
    SRD - real leveling, Phase 9J, doesn't scale these resources yet, so
    "level-1 max" is still the only max there is), Rage ends
    (Character.is_raging - this engine doesn't model rage's real mid-combat
    duration/maintenance conditions, so "clears on any rest" is the
    documented substitute, not a silent omission), and a Half-Orc's
    Relentless Endurance (issue #23) becomes available again."""
    for character in party:
        character.hp = character.max_hp
        character.spell_slots = dict(
            SPELL_SLOTS_BY_LEVEL.get(character.class_index or "", {}).get(character.level, {})
        )
        character.hit_dice_remaining = character.level
        set_exhaustion_level(character, character.exhaustion_level - 1)
        character.class_resources = dict(
            CLASS_RESOURCES_AT_LEVEL_1.get(character.class_index or "", {})
        )
        character.is_raging = False
        character.used_relentless_endurance_this_rest = False
