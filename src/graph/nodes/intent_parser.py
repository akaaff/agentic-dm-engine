"""Real as of Day 12: free text -> ParsedAction via the teacher model's
native structured-output support (see llm/providers.chat_structured).

If parsed_action is already set when the graph is invoked, this node skips
the LLM entirely and passes it through unchanged - the escape hatch every
offline/scripted test (Days 7-11) relies on to exercise the graph
deterministically without a live model.
"""

from __future__ import annotations

from typing import Any

from src.engine.actions import ParsedAction
from src.engine.state import Character
from src.graph.state_schema import GraphState
from src.llm.providers import chat_structured, load_prompt


def _character_summary_line(character: Character) -> str:
    kind = "PC" if character.is_pc else "monster"
    pos = character.position
    return (
        f"- {character.id} ({character.name}, {kind}): "
        f"HP {character.hp}/{character.max_hp}, position ({pos.x}, {pos.y})"
    )


def _build_prompt(state: GraphState) -> str:
    game_state = state["game_state"]
    actor = game_state.characters[game_state.turn_order[game_state.current_turn]]
    others = [c for c in game_state.characters.values() if c.id != actor.id]
    characters_summary = "\n".join(_character_summary_line(c) for c in others)

    return load_prompt("intent_parser").format(
        actor_id=actor.id,
        actor_x=actor.position.x,
        actor_y=actor.position.y,
        actor_speed=actor.speed,
        characters_summary=characters_summary,
        utterance=state["raw_text"],
    )


def intent_parser_node(state: GraphState) -> dict[str, Any]:
    if state["parsed_action"] is not None:
        return {"parsed_action": state["parsed_action"]}

    prompt = _build_prompt(state)
    action = chat_structured(
        messages=[{"role": "user", "content": prompt}],
        schema=ParsedAction,
        temperature=0.2,
    )
    return {"parsed_action": action}
