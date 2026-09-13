"""Short/long rest mechanics (Phase 9G of the rules-completeness initiative -
see CLAUDE.md). Kept as its own small module rather than folded into
campaign_runner.py or rules.py: these two functions are pure Character
mutations with no scene/campaign-chain knowledge of their own (campaign_runner
just calls them when it walks past a rest scene), and rules.py is reserved for
the deterministic combat/check math turn_engine consumes - resting is neither.

Level-1-only project (Phase 9J is what adds real leveling, not this phase):
every character has exactly one hit die, sized per their class
(Character.hit_die_sides, populated at creation from the SRD class's
`hit_die` - see character_creation.create_character) and tracked via
Character.hit_dice_remaining (starts at 1).

A long rest's "full spell slots" is deliberately looked up fresh each time
from character_creation.LEVEL_1_SPELL_SLOTS via class_index, rather than
snapshotting a `max_spell_slots` field onto Character at creation time - the
level-1 table is fixed for the life of a level-1-only character, so a second
stored copy would just be one more place for the two to drift, for no benefit
this project needs yet.
"""

from __future__ import annotations

import random

from src.engine.character_creation import CLASS_RESOURCES_AT_LEVEL_1, LEVEL_1_SPELL_SLOTS
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
    """Full HP, full spell slots (per class_index via LEVEL_1_SPELL_SLOTS,
    empty for non-casters/monsters), hit dice reset to 1 (level 1 = 1 hit
    die), exhaustion reduced by one level (rules.set_exhaustion_level
    already floors at 0), every class_resource restored to its level-1 max
    (short-rest ones like Second Wind recover here too - a long rest is a
    superset of a short rest's benefits, per SRD), and Rage ends
    (Character.is_raging - this engine doesn't model rage's real mid-combat
    duration/maintenance conditions, so "clears on any rest" is the
    documented substitute, not a silent omission)."""
    for character in party:
        character.hp = character.max_hp
        character.spell_slots = dict(LEVEL_1_SPELL_SLOTS.get(character.class_index or "", {}))
        character.hit_dice_remaining = 1
        set_exhaustion_level(character, character.exhaustion_level - 1)
        character.class_resources = dict(
            CLASS_RESOURCES_AT_LEVEL_1.get(character.class_index or "", {})
        )
        character.is_raging = False
