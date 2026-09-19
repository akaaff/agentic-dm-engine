"""Real as of Day 12: free text -> ParsedAction via the teacher model's
native structured-output support (see llm/providers.chat_structured).

If parsed_action is already set when the graph is invoked, this node skips
the LLM entirely and passes it through unchanged - the escape hatch every
offline/scripted test (Days 7-11) relies on to exercise the graph
deterministically without a live model.

Day 27: `config.INTENT_PARSER_BACKEND == "finetuned"` swaps the Ollama
teacher call for the LoRA-distilled 0.5B student (llm/local_parser),
loaded via transformers/peft. The prompt is identical either way; the
student has no server-side grammar constraint, so a failed parse degrades
to an `invalid` action (the graph already handles that terminal state).

Day 27 detour: `"finetuned_ollama"` uses the same distilled student, but
merged + converted to GGUF and served by Ollama (INTENT_PARSER_OLLAMA_MODEL)
- for the *speed* of the teacher's llama.cpp engine, but NOT via
`chat_structured`'s grammar constraint. That constraint measurably hurts
this model (see CLAUDE.md): it collapses `params` (an untyped dict) to
`{}` on every move/skill_check example even though the same model/prompt
produces the right value unconstrained. So this backend uses
`chat_structured_best_effort` instead - same no-grammar, retry-then-fall-
back-to-invalid shape as the transformers-served "finetuned" backend
above, just hitting Ollama instead of a locally loaded model.
"""

from __future__ import annotations

from typing import Any

from src import config
from src.engine.actions import ParsedAction
from src.engine.position import Position, distance_feet
from src.engine.state import Character
from src.graph.state_schema import GraphState
from src.llm.providers import chat_structured, chat_structured_best_effort, load_prompt

_ORDINAL_WORDS = ["closest", "2nd closest", "3rd closest"]
"""Beyond 3rd, falls back to "Nth closest" (see _rank_label) - a hand-picked
list rather than a general ordinal-suffix function since English's 1st/2nd/
3rd/4th... irregularity only matters for the first three anyway, and this
project's encounters rarely have more than a handful of visible characters."""


def _rank_label(rank: int) -> str:
    if rank <= len(_ORDINAL_WORDS):
        return _ORDINAL_WORDS[rank - 1]
    return f"{rank}th closest"


def _direction_label(actor_pos: Position, other_pos: Position) -> str:
    """8-way compass direction of `other_pos` relative to `actor_pos`, on
    this project's own (x right/east, y down/south) grid convention (see
    position.py/BattleMap's own "y=0 is the top row" comment) - found live:
    the model had no reliable way to resolve "the enemy to my left" from
    raw (x, y) pairs alone, so this computes the answer directly instead of
    asking it to do grid arithmetic in its head."""
    dx = other_pos.x - actor_pos.x
    dy = other_pos.y - actor_pos.y
    ns = "north" if dy < 0 else "south" if dy > 0 else ""
    ew = "west" if dx < 0 else "east" if dx > 0 else ""
    direction = ns + ew
    return f"{direction} of you" if direction else "at your position"


def _character_summary_line(character: Character, actor: Character, rank: int) -> str:
    kind = "PC" if character.is_pc else "monster"
    pos = character.position
    feet = distance_feet(actor.position, pos)
    direction = _direction_label(actor.position, pos)
    return (
        f"- {character.id} ({character.name}, {kind}): HP {character.hp}/{character.max_hp}, "
        f"position ({pos.x}, {pos.y}), {feet}ft away ({_rank_label(rank)}), {direction}"
    )


def build_intent_parser_prompt(state: GraphState) -> str:
    """Public (Day 25) so src.training.tasks.intent_parser_task can generate
    synthetic training prompts in the *exact* format production sends -
    essential for a Day 26 fine-tuned model to actually behave like the
    teacher it's distilled from once swapped in (Day 27)."""
    game_state = state["game_state"]
    actor = game_state.characters[game_state.turn_order[game_state.current_turn]]
    others = [c for c in game_state.characters.values() if c.id != actor.id]
    # Sorted by distance (found live: "the closest enemy"/"the enemy to my
    # left" were unresolvable from raw coordinates alone) - rank is this
    # sorted position, not list order, so "closest" always means closest
    # regardless of how game_state.characters happens to be ordered.
    others.sort(key=lambda c: distance_feet(actor.position, c.position))
    characters_summary = "\n".join(
        _character_summary_line(c, actor, rank) for rank, c in enumerate(others, start=1)
    )

    return load_prompt("intent_parser").format(
        actor_id=actor.id,
        actor_x=actor.position.x,
        actor_y=actor.position.y,
        actor_speed=actor.speed,
        characters_summary=characters_summary,
        utterance=state["raw_text"],
    )


def _invalid_action(state: GraphState) -> ParsedAction:
    game_state = state["game_state"]
    actor_id = game_state.turn_order[game_state.current_turn]
    return ParsedAction(actor=actor_id, verb="invalid", raw_text=state["raw_text"])


def _force_actor(action: ParsedAction, expected_actor_id: str) -> ParsedAction:
    """Live-found: the model's structured output includes its own `actor`
    field, generated (not copied) as part of the JSON - the prompt gives it
    `actor_id` as context text, but nothing makes its output literally echo
    that string byte-for-byte, and it occasionally doesn't. Confirmed live
    with an actual test character id "asssssass" (a repeated-letter nonsense
    string - exactly what LLM tokenization reproduces worst): the model
    returned "assssssass" (one extra "s"), which then failed
    resolve_action's actor-mismatch check with a confusing, near-identical-
    looking error. But the acting character is never actually a model
    decision - it's always exactly whoever's turn it currently is, already
    known with certainty before the call ever happens. Same "give the model
    pre-computed facts instead of asking it to reason/reproduce" fix shape
    already used for cast_spell's target field and the closest-enemy/
    direction resolution above - here the fact is asserted after the call
    instead of given as a prompt hint before it, since there's nothing to
    "reason" about, just an exact value to not let the model retype."""
    if action.actor == expected_actor_id:
        return action
    return action.model_copy(update={"actor": expected_actor_id})


def _normalize_cast_spell_target(action: ParsedAction) -> ParsedAction:
    """Found live: even with the prompt explicitly telling the model to set
    the top-level "target" field for cast_spell (never nest it in params -
    see intent_parser.md), it still fairly often answers with
    params["target"]/params["targets"] instead - a real, fairly consistent
    quirk of this model on this one field, confirmed by direct repeated
    testing, not something further prompt wording reliably fixes (a more
    verbose instruction covering the multi-target case actively made the
    single-target case *worse*). Promoting a stray params entry here is a
    deterministic, model-agnostic safety net: the data the model extracted
    is already correct, it's just in the wrong place in the JSON."""
    if action.verb != "cast_spell" or action.target or action.targets:
        return action
    stray_target = action.params.get("target")
    stray_targets = action.params.get("targets")
    if isinstance(stray_target, str):
        return action.model_copy(update={"target": stray_target})
    if isinstance(stray_targets, list) and all(isinstance(t, str) for t in stray_targets):
        return action.model_copy(update={"targets": stray_targets})
    return action


def intent_parser_node(state: GraphState) -> dict[str, Any]:
    if state["parsed_action"] is not None:
        return {"parsed_action": state["parsed_action"]}

    prompt = build_intent_parser_prompt(state)
    backend = config.INTENT_PARSER_BACKEND
    game_state = state["game_state"]
    expected_actor_id = game_state.turn_order[game_state.current_turn]

    if backend == "finetuned":
        from src.llm.local_parser import parse_intent_local

        action = parse_intent_local(prompt, config.INTENT_PARSER_ADAPTER_DIR)
        if action is None:
            return {"parsed_action": _invalid_action(state)}
        action = _force_actor(action, expected_actor_id)
        return {"parsed_action": _normalize_cast_spell_target(action)}

    if backend == "finetuned_ollama":
        action = chat_structured_best_effort(
            messages=[{"role": "user", "content": prompt}],
            schema=ParsedAction,
            model=config.INTENT_PARSER_OLLAMA_MODEL,
            temperature=0.2,
        )
        if action is None:
            return {"parsed_action": _invalid_action(state)}
        action = _force_actor(action, expected_actor_id)
        return {"parsed_action": _normalize_cast_spell_target(action)}

    action = chat_structured(
        messages=[{"role": "user", "content": prompt}],
        schema=ParsedAction,
        temperature=0.2,
    )
    action = _force_actor(action, expected_actor_id)
    return {"parsed_action": _normalize_cast_spell_target(action)}
