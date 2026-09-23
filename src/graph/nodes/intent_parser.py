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
from src.engine.actions import ParsedAction, ParsedActionSequence
from src.engine.monster_ai import approach_path, occupied_squares_by_side
from src.engine.position import Position, distance_feet
from src.engine.rules import effective_speed
from src.engine.state import Character, GameState
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


def _promote_stray_target(action: ParsedAction) -> ParsedAction:
    """Found live for cast_spell first: even with the prompt explicitly
    telling the model to set the top-level "target" field (never nest it in
    params - see intent_parser.md), it still fairly often answers with
    params["target"]/params["targets"] instead - a real, fairly consistent
    quirk of this model, confirmed by direct repeated testing, not something
    further prompt wording reliably fixes (a more verbose instruction
    covering the multi-target case actively made the single-target case
    *worse*). Originally scoped to cast_spell only; found live a second time
    (issue #49's own investigation) that a multi-action sequence can carry
    the same nesting quirk into "move"/"attack" too - once the model starts
    using params.target for one action in a sequence, it can keep doing so
    for the rest, confirmed by a direct repro where a "move" and its
    following "attack" both nested target under a single bad parse.
    Deliberately not restricted to any particular verb: promoting a stray
    params entry to the top level is harmless even for a verb that never
    reads it back out (nothing downstream inspects params["target"] once
    the real field is set), so applying this generically is strictly safer
    than trying to enumerate which verbs need it. A deterministic,
    model-agnostic safety net either way - the data the model extracted is
    already correct, it's just in the wrong place in the JSON."""
    if action.target or action.targets:
        return action
    stray_target = action.params.get("target")
    stray_targets = action.params.get("targets")
    if isinstance(stray_target, str):
        return action.model_copy(update={"target": stray_target})
    if isinstance(stray_targets, list) and all(isinstance(t, str) for t in stray_targets):
        return action.model_copy(update={"targets": stray_targets})
    return action


def _resolve_move_target(action: ParsedAction, game_state: GameState) -> ParsedAction:
    """Issue #48: "move"/"dash" toward a *named* visible character further
    than one square away (e.g. "I charge the wolf" / "come to wolf_1") -
    the prompt sets the top-level "target" field for this instead of
    trying to compute exact squares itself, since intent_parser.md's own
    single-adjacent-step instruction is deliberately narrow (the model was
    never reliable at obstacle-avoiding multi-square paths - see #47's own
    live finding of a malformed empty path when it tried anyway).

    `target` unconditionally overrides any `params.path` the model also
    supplied, rather than only filling in a gap - confirmed live this isn't
    a hypothetical: the model set *both* target="wolf_1" and a bogus
    single-entry path landing 2 squares away (not actually adjacent) for
    the exact same utterance in the same response, and deferring to the
    already-present (wrong) path silently reproduced the original bug this
    issue exists to fix. `target` is the reliable signal (a plain lookup);
    exact square math is what the model keeps getting wrong, so it never
    gets the tie-break once a target is named. A move genuinely aimed at a
    bare destination square (no character involved at all) has no target
    to begin with, so its own params.path is untouched exactly as before.

    Computes a real path via monster_ai.approach_path, the same greedy
    algorithm already proven against turn_engine._resolve_move's own
    affordability check for monster movement - stopping at 5ft/adjacent
    ("move to X" most naturally means "get next to it," not stop at some
    weapon-range-dependent distance - out of scope, see the issue), using
    the actor's real remaining speed budget (doubled for dash, matching
    _resolve_move's own budget formula exactly). An empty/short result
    (blocked, occupied, not enough speed, or already adjacent) is left as
    no `path` at all rather than a partial or stale one silently kept -
    _resolve_move's own "move/dash action requires params['path']" error is
    the honest, already-established way to report "couldn't get there," not
    a new single-action-with-nothing-in-it modeled as if it succeeded."""
    if action.verb not in ("move", "dash") or not action.target:
        return action
    actor = game_state.characters.get(action.actor)
    target = game_state.characters.get(action.target)
    if actor is None or target is None or game_state.battle_map is None:
        return action

    base_speed = effective_speed(actor)
    total_budget = base_speed * 2 if action.verb == "dash" else base_speed
    remaining_budget = max(0, total_budget - actor.movement_used_feet)
    hostile_squares, ally_squares = occupied_squares_by_side(game_state, actor)
    path = approach_path(
        actor.position,
        target.position,
        remaining_budget,
        5,
        game_state.battle_map.terrain,
        hostile_squares,
        ally_squares,
    )
    # Explicitly drop any params.path the model also supplied, even on an
    # empty computed path - already-adjacent or genuinely blocked, either
    # way a stale/wrong model-supplied path from the same response that
    # named this target must not survive to reach turn_engine unexamined.
    new_params = {k: v for k, v in action.params.items() if k != "path"}
    if path:
        new_params["path"] = [{"x": p.x, "y": p.y} for p in path]
    return action.model_copy(update={"params": new_params})


def _strip_invalid_smite(action: ParsedAction, game_state: GameState) -> ParsedAction:
    """Issue #49: the model hallucinates params.smite_slot_level on a plain
    "attack" surprisingly often - live-narrowed to specific weapon names at
    first (e.g. "I attack wolf_1 with my handaxe"), then found live a second
    time to be broader: forceful verb synonyms ("smash"/"crush"/"strike"/
    "hit"/"pummel"/"smack" instead of "attack") paired with certain weapons
    (warhammer, longsword, handaxe, battleaxe...) reproduce it consistently
    too - not a single-weapon quirk, a general "this reads like a mighty
    blow" association the model makes regardless of the actor's actual
    class. turn_engine's own validation already rejects this cleanly for a
    non-Paladin ("X is not a Paladin and cannot use Divine Smite"), so no
    bad state was ever at risk - but that's still a real attack the player
    described, failing outright with a confusing rules error for something
    they never asked for. Whether the actor is actually a Paladin is a
    plain, already-known fact in Python - the same "give the model
    pre-computed facts / let Python own the legality logic" split already
    used for cast_spell's target and the closest-enemy/direction
    resolution - so this strips a hallucinated smite unconditionally for
    anyone but a real Paladin, before it ever reaches turn_engine. A
    genuine Paladin's own smite declaration is completely untouched; a
    residual false-positive there just costs a real spell slot on a hit,
    same as any other spellcasting misparse this project already accepts."""
    if action.verb != "attack" or "smite_slot_level" not in action.params:
        return action
    actor = game_state.characters.get(action.actor)
    if actor is not None and actor.class_index == "paladin":
        return action
    new_params = {k: v for k, v in action.params.items() if k != "smite_slot_level"}
    return action.model_copy(update={"params": new_params})


def _postprocess_action(
    action: ParsedAction, expected_actor_id: str, game_state: GameState
) -> ParsedAction:
    """The full pipeline every parsed action goes through, regardless of
    which backend produced it - forcing the real actor, fixing a
    misplaced cast_spell target, computing a real move/dash path from a
    named target, and dropping a hallucinated non-Paladin smite. Order
    matters only in that each step is independent of the others (none
    touch fields the next one reads)."""
    action = _force_actor(action, expected_actor_id)
    action = _promote_stray_target(action)
    action = _resolve_move_target(action, game_state)
    return _strip_invalid_smite(action, game_state)


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
        return {"parsed_action": _postprocess_action(action, expected_actor_id, game_state)}

    if backend == "finetuned_ollama":
        action = chat_structured_best_effort(
            messages=[{"role": "user", "content": prompt}],
            schema=ParsedAction,
            model=config.INTENT_PARSER_OLLAMA_MODEL,
            temperature=0.2,
        )
        if action is None:
            return {"parsed_action": _invalid_action(state)}
        return {"parsed_action": _postprocess_action(action, expected_actor_id, game_state)}

    action = chat_structured(
        messages=[{"role": "user", "content": prompt}],
        schema=ParsedAction,
        temperature=0.2,
    )
    return {"parsed_action": _postprocess_action(action, expected_actor_id, game_state)}


def parse_intent_sequence(state: GraphState) -> list[ParsedAction]:
    """Issue #47: like intent_parser_node, but can return more than one
    ParsedAction for a single utterance describing multiple distinct
    actions in sequence (e.g. "I rage, move to the wolf, and attack it") -
    called directly by api/ws/session.py's own sequencing loop for real
    human free text, not by the graph itself. intent_parser_node's own
    single-action contract above is unchanged and still what every other
    raw_text path goes through (companion turns via player_agent_node,
    autoplay, tests) - none of those need more than one action per call.

    Only the "teacher" backend (the default, real production path)
    understands the multi-action list format, since it's the only one the
    prompt/schema below actually target. "finetuned"/"finetuned_ollama"
    were prompted/trained on (and still only asked for) a single action -
    not a regression, since batching more than one action per submission
    was never something they could do anyway - so they fall back to
    intent_parser_node's existing single-action call, wrapped in a
    one-element list."""
    if state["parsed_action"] is not None:
        return [state["parsed_action"]]

    if config.INTENT_PARSER_BACKEND != "teacher":
        result = intent_parser_node(state)
        action = result["parsed_action"]
        assert isinstance(action, ParsedAction)
        return [action]

    game_state = state["game_state"]
    expected_actor_id = game_state.turn_order[game_state.current_turn]
    prompt = build_intent_parser_prompt(state)
    sequence = chat_structured(
        messages=[{"role": "user", "content": prompt}],
        schema=ParsedActionSequence,
        temperature=0.2,
    )
    actions = [_postprocess_action(a, expected_actor_id, game_state) for a in sequence.actions]
    return actions or [_invalid_action(state)]
