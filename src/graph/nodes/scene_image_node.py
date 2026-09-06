"""Real as of Day 16. Generates one scene image at the start of a new round
or at the very start of the encounter - not on every single action, which
would be both slow (even SD-Turbo's ~1s/image adds up) and repetitive
(nothing meaningfully changes frame-to-frame within a single round).

Returns a local filesystem path as scene_image_url - there's no HTTP-
servable media endpoint yet (that's Phase 4 frontend work); mounting a
FastAPI StaticFiles route at imagegen.service.DEFAULT_OUTPUT_DIR later won't
require touching this node.
"""

from __future__ import annotations

from typing import Any

from src.graph.state_schema import GraphState
from src.imagegen.service import generate_scene_image


def _scene_prompt(state: GraphState) -> str:
    game_state = state["game_state"]
    actor = game_state.characters[game_state.turn_order[game_state.current_turn]]
    location = game_state.encounter_id.replace("_", " ")
    combatants = ", ".join(
        f"{c.name} ({'ally' if c.is_pc else 'enemy'}, {c.hp}/{c.max_hp} HP)"
        for c in game_state.characters.values()
        if not c.is_dead
    )
    return (
        "Fantasy tabletop RPG battle scene, digital painting, dramatic lighting. "
        f"Setting: {location}. Combatants present: {combatants}. "
        f"It is {actor.name}'s turn."
    )


def scene_image_node(state: GraphState) -> dict[str, Any]:
    game_state = state["game_state"]
    is_scene_start = state["events_before"] == 0
    is_round_start = game_state.round != state["round_before"]
    if not (is_scene_start or is_round_start):
        return {"scene_image_url": None}

    image_path = generate_scene_image(_scene_prompt(state))
    return {"scene_image_url": str(image_path) if image_path else None}
