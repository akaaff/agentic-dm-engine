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

import difflib
import re
from typing import Any

from src import config
from src.engine.actions import ParsedAction, ParsedActionSequence
from src.engine.monster_ai import build_move_toward_target
from src.engine.position import (
    Position,
    chebyshev_distance,
    direction_label,
    distance_feet,
    rank_label,
)
from src.engine.rules import (
    class_spell_indices,
    effective_speed,
    is_unarmed_phrase,
    normalize_skill_name,
    normalize_spell_name,
    spell_mechanic,
    spell_range_feet,
    weapon_range_feet,
)
from src.engine.srd_loader import load_srd
from src.engine.state import Character, GameState
from src.graph.nodes.actor_options import actor_options_summary
from src.graph.state_schema import GraphState
from src.llm.providers import chat_structured, chat_structured_best_effort, load_prompt
from src.training.failed_intents import log_failed_intent


def _character_summary_line(character: Character, actor: Character, rank: int) -> str:
    kind = "PC" if character.is_pc else "monster"
    pos = character.position
    feet = distance_feet(actor.position, pos)
    direction = direction_label(actor.position, pos)
    return (
        f"- {character.id} ({character.name}, {kind}): HP {character.hp}/{character.max_hp}, "
        f"position ({pos.x}, {pos.y}), {feet}ft away ({rank_label(rank)}), {direction}"
    )


def build_intent_parser_prompt(
    state: GraphState, *, include_actor_options: bool | None = None
) -> str:
    """Public (Day 25) so src.training.tasks.intent_parser_task can generate
    synthetic training prompts in the *exact* format production sends -
    essential for a Day 26 fine-tuned model to actually behave like the
    teacher it's distilled from once swapped in (Day 27).

    `include_actor_options` (issue #98) adds what the actor really has - spells,
    weapons - so a described effect ("I conjure a flame") maps to a real name
    instead of a hallucinated one. It defaults to on only for the teacher
    backend: the distilled student was trained on prompts without the block and
    the training-data generator passes False, so neither sees a changed format."""
    if include_actor_options is None:
        include_actor_options = config.INTENT_PARSER_BACKEND == "teacher"
    game_state = state["game_state"]
    actor = game_state.characters[game_state.turn_order[game_state.current_turn]]
    # Dead creatures are not candidates (issue #75): listed, a corpse still
    # ranks "2nd closest", so "the nearest goblin" resolved to it. A downed
    # but living PC stays listed - heal/stabilize/Spare the Dying target them.
    others = [c for c in game_state.characters.values() if c.id != actor.id and not c.is_dead]
    # Sorted by distance (found live: "the closest enemy"/"the enemy to my
    # left" were unresolvable from raw coordinates alone) - rank is this
    # sorted position, not list order, so "closest" always means closest
    # regardless of how game_state.characters happens to be ordered.
    others.sort(key=lambda c: distance_feet(actor.position, c.position))
    characters_summary = "\n".join(
        _character_summary_line(c, actor, rank) for rank, c in enumerate(others, start=1)
    )

    actor_options = ""
    if include_actor_options:
        summary = actor_options_summary(actor, load_srd())
        if summary:
            actor_options = (
                "\nWhat the current actor has (use these exact names for item_or_spell - "
                "a spell described by its effect is the matching spell below, never an "
                "invented one; a weapon named for an attack is an attack, not a spell. "
                "This is only a reference: leave item_or_spell unset for an attack where the "
                "player names no weapon - never fill in the equipped weapon yourself, and "
                "punches/kicks/fists are not a weapon):\n"
                f"{summary}"
            )

    return load_prompt("intent_parser").format(
        actor_id=actor.id,
        actor_x=actor.position.x,
        actor_y=actor.position.y,
        actor_speed=effective_speed(actor),
        actor_options=actor_options,
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
    already correct, it's just in the wrong place in the JSON.

    Live-found a third shape: a genuinely multi-target cast (e.g. "I cast
    Bane on kobold_1, kobold_2, and kobold_3" - a real SRD "up to three
    creatures" spell, not Magic Missile's own dart-splitting case) put
    all three ids under the *singular* key params["target"] as a list,
    confirmed 4/4 live - matching neither the single-string params.target
    case nor the plural params.targets case this function already
    handled, so the whole declaration silently had no target at all and
    failed with "cast_spell action requires a target". A list under the
    singular key is promoted to the real plural "targets" field for
    exactly this reason."""
    if action.target or action.targets:
        return action
    stray_target = action.params.get("target")
    stray_targets = action.params.get("targets")
    if isinstance(stray_target, str):
        return action.model_copy(update={"target": stray_target})
    if (
        isinstance(stray_target, list)
        and stray_target
        and all(isinstance(t, str) for t in stray_target)
    ):
        return action.model_copy(update={"targets": stray_target})
    if isinstance(stray_targets, list) and all(isinstance(t, str) for t in stray_targets):
        return action.model_copy(update={"targets": stray_targets})
    return action


def _promote_stray_item_or_spell(action: ParsedAction) -> ParsedAction:
    """Live-found alongside the target-nesting quirk _promote_stray_target
    already handles, in the exact same response: a real multi-target cast
    ("I cast Bane on kobold_1, kobold_2, and kobold_3") that correctly got
    its three ids promoted to the top-level "targets" field still had
    item_or_spell nested under params instead of the real top-level field
    - "Bane" never made it out of params["item_or_spell"], leaving the
    top-level field None and failing a later, different check ("cast_spell
    action requires item_or_spell"). Same deterministic, model-agnostic
    promotion as the target case - the data is already correct, just in
    the wrong place in the JSON."""
    if action.item_or_spell:
        return action
    stray = action.params.get("item_or_spell")
    if isinstance(stray, str):
        return action.model_copy(update={"item_or_spell": stray})
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

    Computes a real path via monster_ai.build_move_toward_target (the exact
    approach_path/occupied_squares_by_side combination already proven
    against turn_engine._resolve_move's own affordability check for monster
    movement, shared rather than duplicated here a second time - see that
    function's own docstring) - stopping at 5ft/adjacent ("move to X" most
    naturally means "get next to it," not stop at some weapon-range-
    dependent distance - out of scope, see the issue). A genuinely
    unreachable result (blocked, not enough speed) is left as no `path` at
    all - _resolve_move's own "move/dash action requires params['path']"
    error is the honest, already-established way to report "couldn't get
    there." An *already adjacent* result gets an explicitly empty path
    instead (see build_move_toward_target's own docstring) - a different,
    deliberate outcome turn_engine._resolve_move treats as a real no-op,
    not an error, rather than a new single-action-with-nothing-in-it
    modeled as if it moved somewhere.

    No target at all falls through to _discard_unresolvable_raw_path -
    see its own docstring for the second, distinct failure mode that
    covers."""
    if action.verb not in ("move", "dash"):
        return action
    actor = game_state.characters.get(action.actor)
    if actor is None:
        return action

    if not action.target:
        return _discard_unresolvable_raw_path(action, actor)

    target = game_state.characters.get(action.target)
    if target is None:
        return action

    move_action = build_move_toward_target(game_state, actor, target, verb=action.verb)
    # Explicitly drop any params.path the model also supplied, even when no
    # real path could be computed - already-adjacent or genuinely blocked,
    # either way a stale/wrong model-supplied path from the same response
    # that named this target must not survive to reach turn_engine unexamined.
    new_params = {k: v for k, v in action.params.items() if k != "path"}
    if move_action is not None:
        new_params["path"] = move_action.params["path"]
    return action.model_copy(update={"params": new_params})


def _discard_unresolvable_raw_path(action: ParsedAction, actor: Character) -> ParsedAction:
    """Live-found: a move/dash describing a vague or unresolvable reference
    (e.g. "the one in the flank") correctly leaves `target` unset - but
    confirmed live, the model still often invents its own destination
    square anyway, almost always nowhere near actually adjacent to the
    actor's real position. This isn't limited to genuinely ambiguous
    phrasing either: the identical utterance ("I dash to the wolves and
    stab the closest one") resolved `target` correctly on one trial and
    produced this exact bogus-path shape on another - real LLM sampling
    variance, not just a hard case the prompt can't cover.

    Without a target to resolve a real path against, that guess would
    otherwise reach turn_engine's own adjacency check completely
    unexamined and fail with a confusing "cost=None" error (the path's
    first "step" is usually nowhere near one square away). Discarded
    here instead, so it fails the same clean, already-handled "requires
    params['path']" way a genuinely path-less declaration does - this
    doesn't resolve the underlying "which hostile did you mean"
    ambiguity (nothing can, without more information than the utterance
    itself gives), just makes the failure mode consistent and non-
    confusing rather than a cryptic engine-level number. A real,
    genuinely adjacent single-step destination (intent_parser.md's own
    "a single obviously-adjacent step" case, e.g. "I step east") is left
    completely untouched - only an invalid guess gets discarded."""
    raw_path = action.params.get("path")
    if isinstance(raw_path, list) and len(raw_path) == 1:
        try:
            step = Position(x=raw_path[0]["x"], y=raw_path[0]["y"])
        except (KeyError, TypeError, ValueError):
            step = None
        if step is not None and chebyshev_distance(actor.position, step) == 1:
            return action  # a real, legitimate single adjacent step
    if not raw_path:
        return action
    new_params = {k: v for k, v in action.params.items() if k != "path"}
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


_SELF_WORDS = re.compile(r"(?:myself|\bon me\b|\bself\b)", re.IGNORECASE)


def _default_self_target(
    action: ParsedAction, game_state: GameState, utterance: str | None
) -> ParsedAction:
    """Issue #99: "I cast cure wounds on myself" sometimes comes back with no
    target at all, and is then rejected for it. The player said who: themselves.
    Only when the words say so (myself / on me / self) - a spell with no named
    target and no such words is left for the engine to reject."""
    if (
        action.verb != "cast_spell"
        or action.target
        or action.targets
        or not action.item_or_spell
        or not _SELF_WORDS.search(utterance or action.raw_text)
        or action.actor not in game_state.characters
    ):
        return action
    return action.model_copy(update={"target": action.actor})


_CHARGE_PHRASES = re.compile(
    r"(?:\b(?:charge|charges|charging|rush|rushes|rushing|advance|advances|sprint|sprints)"
    r"\b|\brun(?:s|ning)? (?:up|at|to|toward|towards)\b|\bclos(?:e|ing) in\b"
    r"|\bclose the distance\b)",
    re.IGNORECASE,
)


def _insert_charge_move(
    actions: list[ParsedAction], game_state: GameState, actor_id: str, utterance: str
) -> list[ParsedAction]:
    """Issue #107: "I rage and charge the nearest goblin, then chop it with my
    greataxe" came back as [rage, attack] with no move, so the attack was
    rejected as out of reach - and a human's explicit attack deliberately isn't
    turned into a move by the engine. When the player's own words say charge /
    rush / run up / close in, the sequence has a melee attack on a target that is
    out of reach, and nothing moves before it, the approach is inserted (the same
    approach_path a companion's out-of-range redirect uses)."""
    if not _CHARGE_PHRASES.search(utterance):
        return actions
    actor = game_state.characters.get(actor_id)
    if actor is None:
        return actions
    first_attack = None
    for i, action in enumerate(actions):
        if action.verb in ("move", "dash"):
            return actions  # the player's sequence already closes the distance
        if action.verb == "attack":
            first_attack = i
            break
    if first_attack is None:
        return actions
    attack = actions[first_attack]
    target = game_state.characters.get(attack.target) if attack.target else None
    if target is None or target.is_pc == actor.is_pc:
        return actions
    srd = load_srd()
    named = (attack.item_or_spell or "").lower().replace("-", " ")
    weapon_index = next(
        (w for w in actor.equipped_weapons if w.replace("-", " ") in named),
        actor.equipped_weapons[0] if actor.equipped_weapons else None,
    )
    reach, long_range = (5, None)
    if weapon_index is not None and weapon_index in srd.equipment:
        reach, long_range = weapon_range_feet(srd.equipment[weapon_index])
    if long_range is not None:
        return actions  # a ranged weapon doesn't need to close in
    if distance_feet(actor.position, target.position) <= reach:
        return actions
    move = build_move_toward_target(game_state, actor, target, "move")
    if move is None or not move.params.get("path"):
        return actions
    return [*actions[:first_attack], move, *actions[first_attack:]]


def _spell_names_for(actor: Character) -> dict[str, str]:
    """Normalized spell index -> display name for what the actor can cast: their
    class's cantrips plus known/prepared spells."""
    srd = load_srd()
    indices = set(actor.known_spells) | set(actor.prepared_spells)
    if actor.class_index:
        indices |= class_spell_indices(actor.class_index, srd, level=0)
    return {i: str(srd.spells[i]["name"]) for i in indices if i in srd.spells}


def _reconcile_cast_name(action: ParsedAction, game_state: GameState) -> ParsedAction:
    """Issue #98: a `cast_spell` whose name is not a real spell. The model
    sometimes invents one from a description ("throw a dart" -> spell "dart") or
    drifts to a near name. Two deterministic repairs, both only when the name is
    NOT an SRD spell (a real spell the actor can't cast is the engine's call):
    - it is a weapon the actor has, aimed at an enemy -> that was an attack;
    - it is close to a spell the actor really has (difflib) -> use that spell.
    Anything else is left for the engine to reject, now with the actor's spell
    list in the message."""
    if action.verb != "cast_spell" or not action.item_or_spell:
        return action
    actor = game_state.characters.get(action.actor)
    if actor is None:
        return action
    srd = load_srd()
    name = normalize_spell_name(action.item_or_spell)
    if name in srd.spells:
        return action

    target = game_state.characters.get(action.target) if action.target else None
    if target is not None and target.is_pc != actor.is_pc:
        for index in [*actor.equipped_weapons, *actor.inventory]:
            weapon = srd.equipment.get(index)
            if weapon is None or not weapon.get("weapon_category"):
                continue
            if normalize_spell_name(str(weapon["name"])) == name or index == name:
                return action.model_copy(update={"verb": "attack", "item_or_spell": index})

    mine = _spell_names_for(actor)
    close = difflib.get_close_matches(name, list(mine), n=1, cutoff=0.75)
    if close:
        return action.model_copy(update={"item_or_spell": mine[close[0]]})
    return action


def _default_attack_target(action: ParsedAction, game_state: GameState) -> ParsedAction:
    """An `attack` that names no target at all (the model dropped it - issue #95's
    "I cast shillelagh ... and bash the goblin" came back as an attack with
    target null, rejected as "requires a target"). The player said who, or there
    is one obvious answer: the nearest living enemy, the same default the prompt's
    "closest" ranking already uses. Left alone when there is no enemy at all."""
    if action.verb != "attack" or action.target or action.targets:
        return action
    actor = game_state.characters.get(action.actor)
    if actor is None:
        return action
    enemies = [
        c for c in game_state.characters.values() if c.is_pc != actor.is_pc and not c.is_dead
    ]
    if not enemies:
        return action
    nearest = min(enemies, key=lambda c: (distance_feet(actor.position, c.position), c.id))
    return action.model_copy(update={"target": nearest.id})


_FOLLOW_UP_ATTACK = re.compile(
    r"(?:\band|\bthen|,)"
    r"\s+(?:(?:then|i|also|just|immediately|quickly|promptly)\s+)*"
    r"(?:bash|hit|strike|smash|swing at|attack|whack|clobber|pummel|stab|slash|chop|smite|"
    r"punch|kick|shoot|hurl|throw)\w*\b",
    re.IGNORECASE,
)


def _append_dropped_attack(
    actions: list[ParsedAction], game_state: GameState, actor_id: str, utterance: str
) -> list[ParsedAction]:
    """ "I cast shillelagh on my quarterstaff and bash the goblin" usually came back
    as just the cast (issue #95): a bonus action, so the turn stayed open and the
    attack the player plainly asked for never happened. When the player's own
    words follow the spell/ability with "and/then <attack verb>" and nothing in
    the sequence ends the turn, the attack at the nearest enemy is appended (the
    same default target a target-less attack gets). Only the first-person
    "and bash..." shape counts - "so he can hit it" is about someone else."""
    if not actions or not _FOLLOW_UP_ATTACK.search(utterance):
        return actions
    if any(not (_is_bonus_action(a) or a.verb in _TURN_PRESERVING_VERBS) for a in actions):
        return actions  # something already uses the main action
    attack = _default_attack_target(
        ParsedAction(actor=actor_id, verb="attack", raw_text=utterance), game_state
    )
    if attack.target is None:
        return actions
    return [*actions, attack]


_SKILL_PHRASE_PATTERNS: tuple[tuple[str, str], ...] = (
    ("survival", r"\b(?:track|tracks|tracking|forage|foraging|navigate|trail)\b"),
    (
        "sleight-of-hand",
        r"\bpick(?:ing)?\b.{0,25}\b(?:pockets?|purse)|\bpickpocket|\bpalm(?:ing)?\b"
        r"|\bpick(?:ing)? (?:the|a) lock",
    ),
    (
        "acrobatics",
        r"\b(?:tumble|tumbling|somersault|cartwheel|flip over|keep my balance)\b",
    ),
    (
        "stealth",
        r"\b(?:sneak|creep|tiptoe|skulk)(?:s|ing)?\b.{0,20}\bpast\b"
        r"|\bstealth check\b",
    ),
)
_SKILL_PHRASES: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (skill, re.compile(pattern, re.IGNORECASE)) for skill, pattern in _SKILL_PHRASE_PATTERNS
)
"""Issue #101: in-character phrasing the model maps to the wrong skill, or to a plain
move. First match wins, so the order is by how specific the wording is."""


def _normalize_skill_phrase(action: ParsedAction, utterance: str | None) -> ParsedAction:
    """Issue #101: "I study the ground to track the goblins" came back as Perception,
    "I pick the goblin's pocket" as Stealth, and "I tumble past the goblin" / "I make a
    stealth check to sneak past" as a `move` toward it. When the player's own words
    clearly name the skill, a skill_check carries it (and a move that was really a
    sneak/tumble past someone becomes the check). Only skill_check and move/dash are
    touched, and only on these specific phrasings."""
    if action.verb not in ("skill_check", "move", "dash", "use_item") or not utterance:
        return action
    for skill, pattern in _SKILL_PHRASES:
        if not pattern.search(utterance):
            continue
        if action.verb == "skill_check":
            if normalize_skill_name(str(action.params.get("skill", ""))) == skill:
                return action
        elif action.verb == "use_item":
            if skill != "sleight-of-hand":
                continue  # "I pick his pocket" parsed as using an item called "pocket"
        elif skill not in ("stealth", "acrobatics"):
            continue  # a move is only ever a mis-parsed sneak/tumble, never a track/pickpocket
        params = {k: v for k, v in action.params.items() if k != "path"}
        params["skill"] = skill
        return action.model_copy(
            update={
                "verb": "skill_check",
                "params": params,
                "target": None if action.verb != "skill_check" else action.target,
                "item_or_spell": None,
            }
        )
    return action


def _hide_as_cunning_action(action: ParsedAction, game_state: GameState) -> ParsedAction:
    """A Rogue of level 2+ with their bonus action free who says "I hide"
    (issue #84) gets Cunning Action's bonus-action Hide: it costs them
    nothing extra and keeps their main action for the attack the hiding is
    for, so there is no reading of the sentence where the full-action Hide is
    what a player wants - and a 7B parser reliably drops "as a bonus action"
    into plain `hide` anyway, which would end the turn before the shot."""
    if action.verb != "hide":
        return action
    actor = game_state.characters.get(action.actor)
    if actor is None or actor.class_index != "rogue" or actor.level < 2 or actor.bonus_action_used:
        return action
    return action.model_copy(update={"verb": "cunning_action", "params": {"action": "hide"}})


def _normalize_unarmed_attack(
    action: ParsedAction, game_state: GameState, utterance: str | None = None
) -> ParsedAction:
    """Issue #77: "I punch the ogre" comes back as `attack` with no weapon (or
    a made-up one), and a character with any weapon equipped then swings that
    weapon - a Monk's punch resolved as their Dart. When the player's own
    words say punch/kick/unarmed/fists and they didn't name a weapon they
    actually hold, make the intent explicit for the engine."""
    # The player's actual words, not the model's paraphrase in action.raw_text.
    if not is_unarmed_phrase(utterance or action.raw_text):
        return action
    actor = game_state.characters.get(action.actor)
    if action.verb == "offhand_attack":
        # "...and then kick it as a bonus action": with the actor-options block in
        # the prompt the model sometimes files a Monk's bonus kick under the
        # two-weapon off-hand attack. A Monk without a second weapon means
        # Martial Arts' bonus strike.
        if actor is not None and actor.class_index == "monk" and len(actor.equipped_weapons) < 2:
            return action.model_copy(update={"verb": "martial_arts_strike", "item_or_spell": None})
        return action
    if action.verb != "attack":
        return action
    if actor is not None and action.item_or_spell:
        named = action.item_or_spell.lower().replace("-", " ")
        said = (utterance or action.raw_text).lower().replace("-", " ")
        # Only a weapon the PLAYER named counts. Issue #98's actor-options block
        # lists the equipped weapon in the prompt, and the model then echoes it
        # into item_or_spell for "I punch the ogre" - being held isn't enough.
        held = [w.replace("-", " ") for w in actor.equipped_weapons]
        if any(w in named and w in said for w in held):
            return action  # "kick it with my dagger"-style: a held weapon was named
    return action.model_copy(update={"item_or_spell": "unarmed strike"})


def _postprocess_action(
    action: ParsedAction,
    expected_actor_id: str,
    game_state: GameState,
    utterance: str | None = None,
) -> ParsedAction:
    """The full pipeline every parsed action goes through, regardless of
    which backend produced it - forcing the real actor, fixing a
    misplaced cast_spell target/item_or_spell, computing a real move/dash
    path from a named target, and dropping a hallucinated non-Paladin
    smite. Order matters only in that each step is independent of the
    others (none touch fields the next one reads)."""
    action = _force_actor(action, expected_actor_id)
    action = _promote_stray_target(action)
    action = _promote_stray_item_or_spell(action)
    action = _resolve_move_target(action, game_state)
    action = _normalize_skill_phrase(action, utterance)
    action = _hide_as_cunning_action(action, game_state)
    action = _default_attack_target(action, game_state)
    action = _default_self_target(action, game_state, utterance)
    action = _reconcile_cast_name(action, game_state)
    action = _normalize_unarmed_attack(action, game_state, utterance)
    return _strip_invalid_smite(action, game_state)


def _log_if_unparseable(action: ParsedAction, state: GraphState, prompt: str) -> None:
    """Real (not synthetic) hard cases for later fine-tuning review - see
    src.training.failed_intents' own module docstring. Best-effort: logging
    failures are swallowed there, never here, so this can never turn a
    genuinely-invalid parse into a second, different kind of failure."""
    if action.verb != "invalid":
        return
    game_state = state["game_state"]
    actor = game_state.characters.get(action.actor)
    if actor is None:
        return
    log_failed_intent(
        reason="unparseable",
        actor=actor,
        raw_text=state["raw_text"],
        prompt=prompt,
        produced_action=action,
    )


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
        final_action = (
            _invalid_action(state)
            if action is None
            else _postprocess_action(action, expected_actor_id, game_state, state["raw_text"])
        )
    elif backend == "finetuned_ollama":
        action = chat_structured_best_effort(
            messages=[{"role": "user", "content": prompt}],
            schema=ParsedAction,
            model=config.INTENT_PARSER_OLLAMA_MODEL,
            temperature=0.2,
        )
        final_action = (
            _invalid_action(state)
            if action is None
            else _postprocess_action(action, expected_actor_id, game_state, state["raw_text"])
        )
    else:
        action = chat_structured(
            messages=[{"role": "user", "content": prompt}],
            schema=ParsedAction,
            temperature=0.2,
        )
        final_action = _postprocess_action(action, expected_actor_id, game_state, state["raw_text"])

    _log_if_unparseable(final_action, state, prompt)
    return {"parsed_action": final_action}


def _normalize_weapon_name(name: str | None) -> str:
    return (name or "").strip().lower().replace(" ", "-")


def _split_dual_wield_attacks(
    actions: list[ParsedAction], game_state: GameState, actor_id: str
) -> list[ParsedAction]:
    """Live-found: "I attack wolf 3 with handaxe and scimitar" parsed into
    two plain `attack` actions, and the first one ends the turn - so the
    second weapon never swung (the sequencing loop correctly stops once the
    turn is over). Dual-wielding is not two attacks, it is one attack plus
    the Two-Weapon Fighting bonus-action `offhand_attack`, which doesn't end
    the turn. Whether a pair of consecutive attacks names exactly the
    actor's two equipped weapons is a plain lookup, so Python rewrites it
    rather than asking the model to know the engine's verb split: the
    equipped_weapons[1] swing becomes the off-hand attack and is placed
    FIRST (the engine has no "after the Attack action" prerequisite for it,
    and the main attack ends the turn, so it has to come before it).
    Each swing keeps its own named target."""
    actor = game_state.characters.get(actor_id)
    if actor is None or len(actor.equipped_weapons) != 2 or actor.bonus_action_used:
        return actions
    main_weapon, off_weapon = (_normalize_weapon_name(w) for w in actor.equipped_weapons)
    for i in range(len(actions) - 1):
        first, second = actions[i], actions[i + 1]
        if first.verb != "attack" or second.verb != "attack":
            continue
        if not first.target or not second.target:
            continue
        named = {
            _normalize_weapon_name(first.item_or_spell),
            _normalize_weapon_name(second.item_or_spell),
        }
        if named != {main_weapon, off_weapon}:
            continue
        main_attack, off_attack = (
            (first, second)
            if _normalize_weapon_name(first.item_or_spell) == main_weapon
            else (second, first)
        )
        offhand = off_attack.model_copy(update={"verb": "offhand_attack", "item_or_spell": None})
        return [*actions[:i], offhand, main_attack, *actions[i + 2 :]]
    return actions


_ALL_WORDS = frozenset({"all", "every", "each", "everyone", "everything", "everybody"})


def _plural_forms(stem: str) -> set[str]:
    forms = {f"{stem}s", f"{stem}es"}
    if stem.endswith("f"):
        forms.add(f"{stem[:-1]}ves")  # wolf -> wolves
    if stem.endswith("fe"):
        forms.add(f"{stem[:-2]}ves")
    return forms


def _is_area_hostile_spell(action: ParsedAction) -> bool:
    """A spell that can catch several creatures and has an enemy-side effect
    the engine resolves per target: an SRD area_of_effect plus a save mechanic
    (Burning Hands, Thunderwave, Entangle, Faerie Fire, Grease...) or Sleep.
    Deliberately excludes Magic Missile (`targets` means darts, not creatures),
    allies-only spells like Bless, and single-target spells."""
    if action.verb != "cast_spell" or not action.item_or_spell:
        return False
    spell = load_srd().spells.get(normalize_spell_name(action.item_or_spell))
    if spell is None or not spell.get("area_of_effect"):
        return False
    return spell_mechanic(spell) == "save" or spell.get("index") == "sleep"


def _is_condition_buff_spell(action: ParsedAction) -> bool:
    """A buff that lands as a condition on each target it names (Bless - up to
    three creatures - Blur, Longstrider...). "Bless me and Buddy" comes back as
    two consecutive casts of the same spell: the first ends the turn, so the
    second never resolves (and would cost a second slot). Same merge as the area
    spells get, without the plural-wording expansion - the player names who."""
    if action.verb != "cast_spell" or not action.item_or_spell:
        return False
    spell = load_srd().spells.get(normalize_spell_name(action.item_or_spell))
    return spell is not None and spell_mechanic(spell) == "condition"


def _expand_area_spell_targets(
    actions: list[ParsedAction], game_state: GameState, actor_id: str, utterance: str
) -> list[ParsedAction]:
    """Issue #78: "I cast sleep on the goblins" came back as a single
    `target`, so one goblin of three fell asleep; "burning hands on the
    goblins" sometimes came back as three separate casts (the first ends the
    turn, so the other two never resolved - and each would have cost a slot).
    Two deterministic fixes for an area spell (see _is_area_hostile_spell):
    consecutive casts of the same spell collapse into one `targets` list, and
    when the player's words are plural ("the goblins", "all of them") the
    list expands to every living hostile of that kind within the spell's
    reach - the engine already treats `targets` as 'creatures in the area'."""
    actor = game_state.characters.get(actor_id)
    if actor is None:
        return actions

    merged: list[ParsedAction] = []
    for action in actions:
        previous = merged[-1] if merged else None
        if (
            previous is not None
            and (_is_area_hostile_spell(action) or _is_condition_buff_spell(action))
            and previous.verb == "cast_spell"
            and normalize_spell_name(previous.item_or_spell or "")
            == normalize_spell_name(action.item_or_spell or "")
        ):
            ids = list(
                dict.fromkeys(
                    [
                        *(previous.targets or ([previous.target] if previous.target else [])),
                        *(action.targets or ([action.target] if action.target else [])),
                    ]
                )
            )
            merged[-1] = previous.model_copy(update={"target": ids[0], "targets": ids})
        else:
            merged.append(action)

    words = set(utterance.lower().replace(",", " ").replace(".", " ").split())
    out: list[ParsedAction] = []
    for action in merged:
        if not _is_area_hostile_spell(action):
            out.append(action)
            continue
        spell = load_srd().spells[normalize_spell_name(action.item_or_spell or "")]
        reach = spell_range_feet(spell)
        hostiles = [
            c
            for c in game_state.characters.values()
            if not c.is_dead
            and c.is_pc != actor.is_pc
            and distance_feet(actor.position, c.position) <= reach
        ]
        by_kind = [
            c
            for c in hostiles
            if words & _plural_forms(c.name.split()[0].lower())
            or words & _plural_forms(c.id.rsplit("_", 1)[0].replace("_", " ").lower())
        ]
        chosen = hostiles if words & _ALL_WORDS else by_kind
        if len(chosen) < 2:
            out.append(action)
            continue
        ids = [c.id for c in chosen]
        existing = action.targets or ([action.target] if action.target else [])
        ids = list(dict.fromkeys([*[t for t in existing if t in ids], *ids]))
        out.append(action.model_copy(update={"target": ids[0], "targets": ids}))
    return out


_BONUS_ACTION_VERBS = frozenset(
    {
        "second_wind",
        "rage",
        "bardic_inspiration",
        "martial_arts_strike",
        "flurry_of_blows",
        "offhand_attack",
        "revert_wild_shape",
        "cunning_action",
    }
)
"""Verbs that are always a bonus action. (A cast_spell is one only when the SRD
spell's casting_time says so - checked separately. `equip` is a free object
interaction and is deliberately NOT moved: "attack with the dagger, then draw
the sword" must keep its order.)"""

# Verbs that neither end the turn nor are bonus actions: they can stay where the
# player put them.
_TURN_PRESERVING_VERBS = frozenset({"move", "equip"})


def _is_bonus_action(action: ParsedAction) -> bool:
    if action.verb in _BONUS_ACTION_VERBS:
        return True
    if action.verb == "cast_spell" and action.item_or_spell:
        spell = load_srd().spells.get(normalize_spell_name(action.item_or_spell))
        return spell is not None and str(spell.get("casting_time", "")) == "1 bonus action"
    return False


def _bonus_actions_first(actions: list[ParsedAction]) -> list[ParsedAction]:
    """Issue #76: players name the main action first and the bonus action
    second ("I attack the goblin and then use second wind", "...and then cast
    healing word on Buddy", "I punch it and kick it as a bonus action"). The
    main action ends the turn, so the sequencing loop in api/ws/session.py
    stopped before the bonus action ever ran and it was silently dropped. The
    engine doesn't enforce "after the Attack action" for any bonus action (the
    same documented simplification Flurry/Martial Arts already make), so the
    order is free: every bonus action that follows the first turn-ending
    action is moved to just before it, keeping the relative order of
    everything else (the dual-wield split already does this for the off-hand
    swing)."""
    first_main = next(
        (
            i
            for i, a in enumerate(actions)
            if a.verb not in _TURN_PRESERVING_VERBS and not _is_bonus_action(a)
        ),
        None,
    )
    if first_main is None:
        return actions
    late_bonus = [a for a in actions[first_main + 1 :] if _is_bonus_action(a)]
    if not late_bonus:
        return actions
    rest = [a for a in actions[first_main + 1 :] if not _is_bonus_action(a)]
    return [*actions[:first_main], *late_bonus, actions[first_main], *rest]


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
    actions = [
        _postprocess_action(a, expected_actor_id, game_state, state["raw_text"])
        for a in sequence.actions
    ]
    actions = _insert_charge_move(actions, game_state, expected_actor_id, state["raw_text"])
    actions = _append_dropped_attack(actions, game_state, expected_actor_id, state["raw_text"])
    actions = _split_dual_wield_attacks(actions, game_state, expected_actor_id)
    actions = _expand_area_spell_targets(actions, game_state, expected_actor_id, state["raw_text"])
    actions = _bonus_actions_first(actions)
    if not actions:
        actions = [_invalid_action(state)]
    for action in actions:
        _log_if_unparseable(action, state, prompt)
    return actions
