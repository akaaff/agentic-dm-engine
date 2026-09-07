"""Walks a Campaign's scene chain between combat encounters (Day 22).

The engine already has two well-tested pieces: turn-based combat resolution
(turn_engine.py, driven off a GameState) and the skill_check verb (Day 13).
What was missing was anything that actually chained scenes together -
cli.play.run_autoplay and api.ws.session both silently skipped straight past
every narrative_beat/skill_challenge scene to find the first combat
encounter, per their own comments ("Multi-scene chaining ... is Day 22's
job").

This module fills that gap. It's deliberately kept outside turn_engine and
GameState: narrative beats and skill challenges have no turn order, no
battle map, and no per-turn actor - forcing them through the combat-shaped
GameState machinery would mean fabricating fake turn/round bookkeeping for
no benefit. Skill-challenge narration is authored per-scene (success_text/
failure_text on SkillChallengeDef), not LLM-generated, for the same reason
narrative_intro is authored text: only in-combat turn narration goes through
the narrator LLM node.
"""

from __future__ import annotations

import random

from src.engine.campaign import Campaign, Scene, SkillChallengeDef
from src.engine.rules import (
    ability_check_modifier,
    normalize_skill_name,
    resolve_skill_check,
    skill_ability,
)
from src.engine.srd_loader import SrdIndex
from src.engine.state import Character


def resolve_skill_challenge(
    challenge: SkillChallengeDef,
    party: list[Character],
    srd: SrdIndex,
    rng: random.Random,
) -> tuple[bool, str]:
    """The living party member with the best modifier for the challenge's
    skill attempts it - ties broken by party order for determinism. Returns
    (success, a narration line covering both the roll and the authored
    success/failure flavor text)."""
    ability = skill_ability(challenge.skill, srd)
    skill_key = f"skill-{normalize_skill_name(challenge.skill)}"

    def modifier_for(character: Character) -> int:
        proficient = skill_key in character.skill_proficiencies
        return ability_check_modifier(character, ability, proficient=proficient)

    living = [c for c in party if not c.is_dead] or party
    champion = max(living, key=modifier_for)
    modifier = modifier_for(champion)

    result, success = resolve_skill_check(modifier=modifier, dc=challenge.dc, rng=rng)
    outcome = challenge.success_text if success else challenge.failure_text
    verdict = "succeeds" if success else "fails"
    narration = (
        f"{champion.name} attempts a {normalize_skill_name(challenge.skill)} check "
        f"(rolled {result.total} vs DC {challenge.dc}) and {verdict}. {outcome}"
    )
    return success, narration


def advance_to_next_encounter(
    campaign: Campaign,
    scene: Scene,
    party: list[Character],
    srd: SrdIndex,
    rng: random.Random,
) -> tuple[Scene | None, list[str]]:
    """Walks the scene chain starting at `scene` (inclusive), narrating each
    scene it passes through and resolving any skill_challenge along the way,
    until it reaches a `combat` scene or runs off the end of the chain.

    Returns `(combat_scene, narration_log)` when a combat scene is found -
    the combat scene's own narrative_intro is included in the log, so the
    caller only needs to build/play its encounter, not narrate its intro
    separately. Returns `(None, narration_log)` when the chain ends without
    another combat scene - the campaign is complete.
    """
    narration: list[str] = []
    current: Scene | None = scene
    while current is not None:
        narration.append(current.narrative_intro)
        if current.type == "combat":
            return current, narration
        if current.type == "skill_challenge":
            if current.skill_challenge_def is None:
                raise ValueError(
                    f"Scene {current.id!r} is type=skill_challenge but has no skill_challenge_def"
                )
            _success, outcome_text = resolve_skill_challenge(
                current.skill_challenge_def, party, srd, rng
            )
            narration.append(outcome_text)
        # narrative_beat / roleplay: narrative_intro alone is the scene's
        # content, already appended above - no mechanics to resolve.
        current = campaign.next_scene(current)
    return None, narration
