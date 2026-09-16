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
    - always resolves as two unarmed strikes, no `item_or_spell`."""
    raw_text: str
    confidence: float | None = None
    """Set by the LLM intent parser; absent for scripted/hand-authored actions."""
