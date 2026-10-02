"""Real as of Day 15. Generates a companion's own free-text turn declaration
via the teacher model + persona, so the existing intent_parser (Day 12) can
parse it into a ParsedAction exactly like human free text - no separate
companion-specific parsing path needed.

Bypassed (returns {} - no state change) whenever parsed_action or raw_text is
already set, the same escape-hatch shape as intent_parser_node, so every
existing scripted/offline/human-input/debug_action path is unaffected by
this node's insertion at the front of the graph. Also bypassed for any actor
that isn't a companion (human PCs supply their own raw_text over the
WebSocket; monster turns are driven by engine.monster_ai, which builds a
ParsedAction directly and so never reaches this node with an empty
parsed_action either).

An unconscious companion (hp <= 0, not yet dead) is forced straight to a
`death_save` ParsedAction rather than asked to role-play - found live
(first real autoplay run): turn_engine only accepts "death_save" while
unconscious (see turn_engine.resolve_action's top-level guard), but nothing
told the persona-driven LLM that, so it kept generating ordinary combat
declarations that turn_engine correctly rejected, forever - there's no
narrative content in "attempt a death save" for a persona to add anyway.
"""

from __future__ import annotations

from typing import Any

from src.engine.actions import ParsedAction
from src.engine.position import direction_label, distance_feet, rank_label
from src.engine.rules import (
    class_spell_indices,
    effective_speed,
    spell_range_feet,
    weapon_range_feet,
)
from src.engine.srd_loader import SrdIndex, load_srd
from src.engine.state import Character, GameState
from src.graph.personas import persona_block
from src.graph.state_schema import GraphState
from src.llm.providers import chat_english_only, load_prompt


def _character_summary_line(character: Character, actor: Character, rank: int) -> str:
    # Live-found (user report: a Bard dashed toward melee range instead of
    # casting an already-in-range spell) - this used to give the model raw
    # (x, y) pairs only, the same gap intent_parser.py's own prompt had
    # before issue #48's fix. Mirrors that fix exactly (distance/rank/
    # direction, sorted nearest-first) so the model can actually tell
    # whether anything is already within reach of a spell/weapon it has,
    # instead of guessing from coordinates alone.
    kind = "PC" if character.is_pc else "monster"
    pos = character.position
    feet = distance_feet(actor.position, pos)
    direction = direction_label(actor.position, pos)
    return (
        f"- {character.id} ({character.name}, {kind}): HP {character.hp}/{character.max_hp}, "
        f"position ({pos.x}, {pos.y}), {feet}ft away ({rank_label(rank)}), {direction}"
    )


def _recent_events_summary(game_state: GameState, limit: int = 5) -> str:
    recent = game_state.events[-limit:]
    if not recent:
        return "(none yet)"
    return "\n".join(f"- actor={e.actor} type={e.type} payload={e.payload}" for e in recent)


def _spell_option_line(spell_index: str, srd: SrdIndex) -> str:
    spell = srd.spells.get(spell_index)
    if spell is None:
        return spell_index
    return f"{spell['name']} ({spell_range_feet(spell)}ft)"


def _actor_options_summary(actor: Character, srd: SrdIndex) -> str:
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

    if actor.class_index:
        cantrip_ids = sorted(
            class_spell_indices(actor.class_index, srd, level=0),
            key=lambda idx: srd.spells[idx]["name"],
        )
        if cantrip_ids:
            cantrip_bits = [_spell_option_line(idx, srd) for idx in cantrip_ids]
            lines.append(f"Cantrips you know (unlimited, no slot cost): {', '.join(cantrip_bits)}")

    known = actor.known_spells + actor.prepared_spells
    if known:
        spell_bits = [_spell_option_line(idx, srd) for idx in sorted(set(known))]
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


def _build_prompt(game_state: GameState, actor: Character, srd: SrdIndex) -> str:
    others = [c for c in game_state.characters.values() if c.id != actor.id]
    others.sort(key=lambda c: distance_feet(actor.position, c.position))
    characters_summary = "\n".join(
        _character_summary_line(c, actor, rank) for rank, c in enumerate(others, start=1)
    )

    return load_prompt("player_agent").format(
        actor_name=actor.name,
        persona=persona_block(actor),
        actor_x=actor.position.x,
        actor_y=actor.position.y,
        actor_hp=actor.hp,
        actor_max_hp=actor.max_hp,
        actor_speed=effective_speed(actor),
        actor_options_summary=_actor_options_summary(actor, srd),
        characters_summary=characters_summary,
        recent_events_summary=_recent_events_summary(game_state),
    )


def player_agent_node(state: GraphState) -> dict[str, Any]:
    if state["parsed_action"] is not None or state["raw_text"]:
        return {}

    game_state = state["game_state"]
    actor = game_state.characters[game_state.turn_order[game_state.current_turn]]
    if not actor.is_companion:
        return {}

    if actor.hp <= 0 and not actor.is_dead:
        return {
            "parsed_action": ParsedAction(
                actor=actor.id,
                verb="death_save",
                raw_text=f"{actor.name} fights to stay conscious.",
            )
        }

    # load_srd() is @cache'd (src/engine/srd_loader.py) - cheap to call here
    # on every turn rather than threading a factory-injected srd through
    # graph_builder.build_graph the way rules_engine_node needs for rng
    # (which genuinely must be test-injectable for determinism); srd is
    # read-only and stateless, so there's nothing to inject for tests.
    prompt = _build_prompt(game_state, actor, load_srd())
    utterance = chat_english_only(messages=[{"role": "user", "content": prompt}], temperature=0.8)
    return {"raw_text": utterance.strip()}
