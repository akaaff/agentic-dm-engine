"""ParsedAction - the structured output of intent parsing (Day 12+), and the
input the rules engine actually consumes. Also what scripted/autoplay tests
construct directly, bypassing the LLM entirely.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel

ActionVerb = Literal[
    "attack",
    "cast_spell",
    "move",
    "dash",
    "dodge",
    "disengage",
    "use_item",
    "skill_check",
    "death_save",
    "stabilize",
    "help",
    "grapple",
    "shove",
    "second_wind",
    "rage",
    "equip",
    "offhand_attack",
    "cunning_action",
    "flurry_of_blows",
    "martial_arts_strike",
    "wild_shape",
    "revert_wild_shape",
    "bardic_inspiration",
    "end_turn",
    "invalid",
]


class ParsedAction(BaseModel):
    actor: str
    verb: ActionVerb
    target: str | None = None
    targets: list[str] | None = None
    item_or_spell: str | None = None
    params: dict[str, Any] = {}
    """Verb-specific extras. Convention: `move`/`dash` carry
    `params["move_to"] = {"x": int, "y": int}`; `skill_check` carries
    `params["skill"] = str`; `equip` carries `params["items"] = [str, ...]`
    (the weapon/armor/shield indices to make the new active equipped set).
    `cast_spell` needs `target` (or `targets` for a multi-target cast, e.g.
    Burning Hands) the same way `attack` does - found live: the intent-
    parser prompt never actually told the model to set it, so a spell cast
    naming a real target could still reach turn_engine with neither field
    set and get rejected as "requires a target." For an "auto_hit" spell
    (Magic Missile, issue #35 - see rules.spell_mechanic), `targets` means
    something different from every other mechanic: one entry per *dart*,
    not one entry per independently-resolved target - a bare `target`
    (no `targets` list) sends every available dart at that one creature.
    `offhand_attack` needs only `target` (like `attack`) - always resolves
    against the actor's second equipped weapon, no `item_or_spell`.
    `attack` may optionally carry `params["smite_slot_level"] = int`
    (Paladin's Divine Smite, issue #21) - this engine has no mid-resolution
    "did it hit?" pause to ask the player after the fact, so committing to
    spending a slot is part of declaring the attack itself; the slot is only
    actually consumed if the attack lands, matching SRD's real "decide after
    a hit, before damage" timing closely enough that a miss never costs
    anything. `cunning_action` (Rogue, issue #21) carries
    `params["action"] = "dash" | "disengage"`, plus `params["path"]` too
    when `action == "dash"` (the same shape a plain `dash` action uses).
    `flurry_of_blows` (Monk, issue #24) needs only `target` (like `attack`)
    - always resolves as two unarmed strikes, no `item_or_spell`.
    `martial_arts_strike` (Monk, issue #36) needs only `target` (like
    `attack`) - one free bonus-action unarmed strike, no Ki cost and
    available from level 1 (unlike `flurry_of_blows`, which costs 1 Ki and
    needs level 2+) - real SRD Martial Arts: "when you use the Attack
    action with an unarmed strike or a monk weapon, you can make one
    unarmed strike as a bonus action."
    `wild_shape` (Druid, issue #24) carries `params["beast_index"] = str`
    (an SRD monster index, e.g. "wolf") - no target, it transforms the
    actor. `revert_wild_shape` needs nothing at all - it only makes sense
    while already transformed. `bardic_inspiration` (Bard, issue #25)
    needs only `target` (like `attack`/`help`) - the ally who receives the
    banked die, no `item_or_spell`."""
    raw_text: str
    confidence: float | None = None
    """Set by the LLM intent parser; absent for scripted/hand-authored actions."""


class ParsedActionSequence(BaseModel):
    """Issue #47: an ordered list of ParsedActions extracted from one
    utterance - e.g. "I rage, move to the wolf, and attack it" becomes
    [rage, move, attack]. Almost always length 1; more than one entry only
    for a genuinely multi-clause command. The model's job is purely
    extraction (what did the player describe, in what order) - resolving
    them (stopping early the moment the actor's turn actually ends, per
    resolve_action's own per-verb ends_turn signal, or a sub-action fails)
    is the caller's job, the same "give the model structure, let Python own
    the legality/sequencing logic" split already used elsewhere in this
    project. See api/ws/session.py's own sequencing loop for where that
    happens - intent_parser_node's own single-action contract is unchanged,
    since every other raw_text path (companion turns, autoplay, tests)
    still only ever needs one action per call."""

    actions: list[ParsedAction]
