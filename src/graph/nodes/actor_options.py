"""What a character actually has available - equipped and carried weapons,
cantrips, known/prepared spells, spell slots, class resources - as plain prompt
lines. Every line reads straight off the real Character/SRD data (the same "give
the model the fact, don't ask it to remember" approach as the rest of this
project). Shared by the companion-turn prompt (player_agent) and, behind a flag,
the free-text intent parser (issue #98)."""

from __future__ import annotations

from src.engine.rules import class_spell_indices, spell_range_feet, weapon_range_feet
from src.engine.srd_loader import SrdIndex
from src.engine.state import Character

_SPELL_USAGE_HINTS = {
    "misty-step": (
        "teleport: set target to the creature to appear next to, or params.to {x, y} for a square"
    ),
    "color-spray": "set targets to the creatures in the cone",
}
"""How to fill in the parsed action for the few spells whose target isn't the obvious one.
Shown only to a character who actually has the spell, next to its name, so the intent parser
prompt doesn't carry hints for spells nobody in the scene can cast."""


def spell_option_line(spell_index: str, srd: SrdIndex) -> str:
    spell = srd.spells.get(spell_index)
    if spell is None:
        return spell_index
    hint = _SPELL_USAGE_HINTS.get(spell_index)
    suffix = f"; {hint}" if hint else ""
    return f"{spell['name']} ({spell_range_feet(spell)}ft{suffix})"


def actor_options_summary(actor: Character, srd: SrdIndex) -> str:
    """Live-found, same report as _character_summary_line above: the prompt
    never told the model what the actor actually has available (equipped
    weapon, known spells, remaining slots/class resources) - it had to
    improvise entirely blind, which is a big part of why a spellcasting
    companion with no melee weapon dashed toward melee range instead of
    casting a spell it already knew was in range. Every line here reads
    straight off the real Character/SRD data (the same ground-truth
    reasoning as every other "give the model the fact" fix in this
    project), not invented or guessed."""
    lines = []

    if actor.equipped_weapons:
        weapon_bits = []
        for index in actor.equipped_weapons:
            weapon = srd.equipment.get(index)
            if weapon is None:
                weapon_bits.append(index)
                continue
            normal, _long = weapon_range_feet(weapon)
            weapon_bits.append(f"{weapon['name']} ({normal}ft)")
        lines.append(f"Equipped weapon(s): {', '.join(weapon_bits)}")
    else:
        lines.append("Equipped weapon(s): none - no melee weapon to attack with")

    # Carried but not equipped (issue #83/#98): naming one is a request to draw it
    # or throw it, not to cast a spell called that.
    carried = [
        srd.equipment[index]["name"]
        for index in dict.fromkeys(actor.inventory)
        if index not in actor.equipped_weapons
        and index in srd.equipment
        and srd.equipment[index].get("weapon_category")
    ]
    if carried:
        lines.append(f"Other weapons carried (not equipped): {', '.join(carried)}")

    if actor.class_index:
        cantrip_ids = sorted(
            class_spell_indices(actor.class_index, srd, level=0),
            key=lambda idx: srd.spells[idx]["name"],
        )
        if cantrip_ids:
            cantrip_bits = [spell_option_line(idx, srd) for idx in cantrip_ids]
            lines.append(f"Cantrips you know (unlimited, no slot cost): {', '.join(cantrip_bits)}")

    known = actor.known_spells + actor.prepared_spells
    if known:
        spell_bits = [spell_option_line(idx, srd) for idx in sorted(set(known))]
        lines.append(f"Spells you know/have prepared: {', '.join(spell_bits)}")

    slots = {level: count for level, count in actor.spell_slots.items() if count > 0}
    if slots:
        slot_bits = [f"level {level}: {count} remaining" for level, count in sorted(slots.items())]
        lines.append(f"Spell slots: {', '.join(slot_bits)}")

    if actor.class_resources:
        resource_bits = [
            f"{name.replace('_', ' ')}: {count} remaining"
            for name, count in sorted(actor.class_resources.items())
            if count > 0
        ]
        if resource_bits:
            lines.append(f"Class resources: {', '.join(resource_bits)}")

    return "\n".join(lines)
