"""Real as of Day 12 (moved up alongside intent_parser - both are plain
teacher-model text calls with no dependency on Day 13's companion-agent
work). Turns the Events this graph invocation actually produced into prose -
read-only, never mutates GameState."""

from __future__ import annotations

import re
from typing import Any

from src.engine.events import Event
from src.engine.state import Character
from src.graph.state_schema import GraphState
from src.llm.providers import chat_english_only, load_prompt


def character_label(character: Character) -> str:
    """ "Lark the bard" for a party member (issue #104), the plain name for a
    monster ("Goblin 1" already says what it is). Used everywhere the narrator
    prompt names someone, so a model with several similar party members can
    tell them apart and has each one's class next to the name instead of an
    opaque id."""
    if character.is_pc and character.class_:
        return f"{character.name} the {character.class_.lower()}"
    return character.name


def _label_ids(value: Any, labels: dict[str, str]) -> Any:
    """Replaces any string equal to a character id, anywhere in an event payload
    (nested dicts/lists included), with that character's label."""
    if isinstance(value, str):
        return labels.get(value, value)
    if isinstance(value, list):
        return [_label_ids(v, labels) for v in value]
    if isinstance(value, dict):
        return {k: _label_ids(v, labels) for k, v in value.items()}
    return value


def _event_line(event: Event, labels: dict[str, str] | None = None) -> str:
    """One prompt line per event. With `labels` (id -> display label) every
    character reference - the actor and any id in the payload - is shown by name
    and class rather than by id (issue #104: with three similar party members
    the model mixed up opaque ids and attributed dialogue to bystanders)."""
    labels = labels or {}
    actor = labels.get(event.actor, event.actor)
    payload = _label_ids(event.payload, labels)
    # Live-found: "actor" means the perpetrator for every other
    # damage-causing event (attack_roll/damage_dealt - actor hits target),
    # but hazard_damage inverts that (actor is the one who stepped on the
    # hazard and got hurt themselves, no attacker at all - see
    # turn_engine._apply_hazard_damage's own docstring). The model
    # apparently read "actor=qasz type=hazard_damage" the same way as a
    # combat event and invented a wolf bite to explain it, even though no
    # attack_roll event for any wolf was present. Spelling this one out
    # explicitly removes the ambiguity the generic "actor=.../type=..."
    # format otherwise leaves for the model to guess at.
    if event.type == "hazard_damage":
        return (
            f"- {actor} steps on hazardous terrain and takes "
            f"{payload.get('amount')} {payload.get('damage_type')} damage "
            "(environmental/terrain damage - not caused by any other character)"
        )
    # A spell's saving throw is rolled by the *target* against the caster's
    # spell, so `success` means the target resisted - success=false means the
    # spell took hold. The model read "success: false" as the SPELL failing
    # (live: a failed Entangle save narrated as the tendrils failing to
    # ensnare the creature) - issue #96. Spelled out the way hazard_damage is.
    if event.type == "saving_throw" and event.payload.get("kind") == "spell_save":
        p = payload
        outcome = (
            "RESISTS the spell (passed the save - it has no effect on them)"
            if p.get("success")
            else "FAILS the save - the spell takes full hold of them"
        )
        return (
            f"- {p.get('target')} makes a {p.get('ability')} saving throw against "
            f"{actor}'s {p.get('spell')} and {outcome}"
        )
    return f"- actor={actor} type={event.type} payload={payload}"


def _fallback_narration(new_events: list[Event], state: GraphState) -> str:
    """Live-found: the model occasionally returns empty/whitespace-only
    text for a real event (confirmed live for a plain repositioning move
    with no combat significance) - and NarrationFeed.tsx renders nothing
    at all for an empty-text entry whose events also have no badge case
    (move events don't - see formatEvent.ts), so a blank response here
    isn't just a dropped sentence, it's a genuinely invisible log entry
    with no visible trace anything happened. This deterministic minimal
    fallback (not a second LLM call) guarantees the caller always has
    *something* to broadcast once narrator_node's own retries are
    exhausted - a plain, honest line naming the actor beats a silent gap."""
    actor_id = new_events[0].actor
    character = state["game_state"].characters.get(actor_id)
    name = character.name if character else actor_id
    return f"{name} acts."


def _event_character_ids(events: list[Event], characters: dict[str, Character]) -> set[str]:
    """Every character an event batch actually involves: each event's actor plus
    any id appearing anywhere in a payload (a target, a caster, an ally)."""
    involved: set[str] = set()

    def walk(value: Any) -> None:
        if isinstance(value, str):
            if value in characters:
                involved.add(value)
        elif isinstance(value, list):
            for v in value:
                walk(v)
        elif isinstance(value, dict):
            for v in value.values():
                walk(v)

    for event in events:
        walk(event.actor)
        walk(event.payload)
    return involved


def _mentions(narration: str, name: str) -> bool:
    return re.search(rf"\b{re.escape(name)}\b", narration, re.IGNORECASE) is not None


def _misattribution_problem(
    narration: str, events: list[Event], characters: dict[str, Character]
) -> str | None:
    """Issue #104: a light check that the narration is about the right people.
    With several similar party members the model attributed actions and quoted
    dialogue to a bystander ("Vex snarls" for a spell Elara cast). Two cheap,
    deterministic tests, applied to party members only (a monster is rightly
    narrated as "the goblin"):
    - a party member who is NOT part of these events is named -> wrong person;
    - exactly one party member acts in these events and is never named.
    Returns what is wrong, for the retry prompt, or None."""
    involved = _event_character_ids(events, characters)
    for character in characters.values():
        if (
            character.is_pc
            and character.id not in involved
            and _mentions(narration, character.name)
        ):
            return (
                f"it names {character.name}, who is not part of these events - only the "
                "characters listed in the events may act or speak"
            )
    pc_actors = {e.actor for e in events if e.actor in characters and characters[e.actor].is_pc}
    if len(pc_actors) == 1:
        actor = characters[next(iter(pc_actors))]
        if not any(_mentions(narration, token) for token in actor.name.split() if len(token) > 2):
            return f"it never names {actor.name}, whose action this is"
    return None


def narrator_node(state: GraphState) -> dict[str, Any]:
    new_events = state["game_state"].events[state["events_before"] :]
    if not new_events:
        return {"narration": ""}

    characters = state["game_state"].characters
    labels = {cid: character_label(c) for cid, c in characters.items()}
    events_summary = "\n".join(_event_line(e, labels) for e in new_events)
    # Issue #33: a live report of the narrator inventing an unrelated
    # monster ("the drow's poison...") mid-fight against a wolves-only
    # encounter. Confirmed this isn't context accumulation across turns -
    # chat_english_only/chat send one stateless request per call (see
    # providers.chat's plain httpx.post, no conversation history kept
    # between calls), so a hallucination at round 12 can't be "bleed" from
    # something said many turns earlier - it never saw those calls. The
    # prompt already said "do not invent... characters... not listed
    # below," but that's a prohibition with nothing concrete to check
    # itself against; giving it the actual closed cast list (same
    # "anchor it to real data" pattern intent_parser's visible-characters
    # list already uses) is a stronger, more falsifiable grounding than a
    # generic instruction alone - not a guaranteed fix for a small model's
    # occasional hallucination, same honest framing as issue #16's CJK
    # mitigation.
    cast_names = ", ".join(sorted({labels[cid] for cid in characters}))
    prompt = load_prompt("narrator").format(events_summary=events_summary, cast_names=cast_names)
    # Retried the same way chat_english_only already retries on a CJK leak
    # (issue #16) - a real independent sample, not a deterministic failure -
    # before falling back to _fallback_narration's deterministic minimum. A
    # draft that fails the attribution check (issue #104) is retried too, with
    # what was wrong spelled out; if every attempt does, the last non-empty
    # draft is used rather than dropping the line.
    last_draft = ""
    attempt_prompt = prompt
    for _ in range(3):
        raw = chat_english_only(
            messages=[{"role": "user", "content": attempt_prompt}], temperature=0.7
        )
        narration = raw.strip()
        if not narration:
            continue
        last_draft = narration
        problem = _misattribution_problem(narration, new_events, characters)
        if problem is None:
            return {"narration": narration}
        attempt_prompt = (
            f"{prompt}\n\nYour previous draft was rejected because {problem}. "
            "Write it again, keeping every action and every quoted line with the character "
            "whose event it is."
        )
    if last_draft:
        return {"narration": last_draft}
    return {"narration": _fallback_narration(new_events, state)}
