"""Phase 2 of the story-adaptive-encounters initiative (Phase 1: situation-
matched battle maps, src/engine/battle_map_templates.py). Plain LLM calls
for a party_choice scene's live pause - deliberately NOT LangGraph nodes:
nothing here reads or writes GraphState, and a narrative-choice moment isn't
a mechanical turn (no Event log, no ParsedAction). Called directly from
api/ws/session.py, the same way campaign_runner's own plain functions
already are.
"""

from __future__ import annotations

from src.engine.state import Character
from src.graph.personas import persona_block
from src.llm.providers import chat_english_only, load_prompt


def generate_companion_party_choice_response(character: Character, situation: str) -> str:
    """A companion's own in-character reaction to a party_choice scene's
    situation - one or two sentences of what they say or do. Deliberately a
    different prompt from player_agent_node's own: that one demands exactly
    one concrete combat verb (the right shape for a turn a downstream parser
    has to resolve into a ParsedAction), which is the wrong shape here -
    there's no verb to extract from a narrative opinion or suggestion."""
    prompt = load_prompt("party_choice_companion").format(
        persona=persona_block(character), actor_name=character.name, situation=situation
    )
    utterance = chat_english_only(messages=[{"role": "user", "content": prompt}], temperature=0.8)
    return utterance.strip()


def synthesize_party_choice_narration(
    situation: str, responses: dict[str, str], party: list[Character]
) -> str:
    """Weaves every party member's stated input (companions' own generated
    lines plus whatever the humans actually typed) into one continuation of
    the story. The model's job is prose synthesis only - the scene chain
    itself stays fixed regardless of what anyone said; real branching on the
    party's choice is Phase 3's job, not this one."""
    names = {c.id: c.name for c in party}
    responses_summary = (
        "\n".join(
            f"- {names.get(character_id, character_id)}: {text}"
            for character_id, text in responses.items()
        )
        or "(the party said nothing in particular)"
    )
    prompt = load_prompt("party_choice_synthesis").format(
        situation=situation, responses_summary=responses_summary
    )
    narration = chat_english_only(messages=[{"role": "user", "content": prompt}], temperature=0.7)
    return narration.strip()
