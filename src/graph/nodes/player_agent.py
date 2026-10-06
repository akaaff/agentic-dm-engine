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
from src.engine.rules import effective_speed
from src.engine.srd_loader import SrdIndex, load_srd
from src.engine.state import Character, GameState
from src.graph.nodes.actor_options import actor_options_summary
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


def _build_prompt(game_state: GameState, actor: Character, srd: SrdIndex) -> str:
    # Dead creatures aren't candidates (issue #75) - see intent_parser.
    others = [c for c in game_state.characters.values() if c.id != actor.id and not c.is_dead]
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
        actor_options_summary=actor_options_summary(actor, srd),
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
