"""Rules on what a no-combat utility spell accomplishes (issue #55).

The engine records a cast of Mage Hand, Detect Magic, Knock and the like as a `spell_cast`
event flagged `utility` - it has no scene contents to roll against (see engine/utility_spells.py).
This node runs between the rules engine and the narrator: for each such event this action
produced it asks the model for a verdict - success, partial or failure plus one sentence of what
happens - given the spell's real SRD text, the per-spell guideline and the scene, and records it
as a `spell_ruling` event. The narrator is told to treat that event as fact, the same way it
treats a death save's `success` flag, so a cast can't be narrated as working when it was ruled a
failure.

If the model call fails (Ollama down, an unparseable reply) the cast is ruled a plain success
with no sentence: a utility spell with no mechanical stakes failing because a server hiccuped
would be worse than letting it work, and the narrator still describes it from the attempt."""

from __future__ import annotations

import logging
import re
from typing import Any, Literal

from pydantic import BaseModel, Field

from src.engine.events import Event
from src.engine.position import distance_feet
from src.engine.srd_loader import load_srd
from src.engine.state import Character, GameState
from src.engine.utility_spells import guideline_for
from src.graph.state_schema import GraphState
from src.llm.providers import chat_structured, load_prompt
from src.observability.mechanics_log import log_mechanic

logger = logging.getLogger(__name__)

_DEFAULT_GUIDANCE = (
    "Judge the attempt against the spell's rules text: success if it is within what the spell "
    "does, partial if it only partly fits, failure if the text rules it out."
)


class SpellRuling(BaseModel):
    # `ruling` is declared first on purpose: the model writes what actually happens - including
    # anything the spell can't do - and only then labels it, so the label follows its own
    # sentence. (Asking it to name the limit an attempt exceeds *before* ruling was tried and
    # made things worse: it wrote "none" for attempts the guideline plainly forbids.)
    ruling: str = Field(
        description=(
            "One plain sentence in the present tense saying what actually happens as a result, "
            "including anything the spell cannot do."
        )
    )
    outcome: Literal["success", "partial", "failure"]


def _scene_summary(game_state: GameState, caster: Character) -> str:
    lines = []
    for other in game_state.characters.values():
        if other.id == caster.id or other.is_dead:
            continue
        side = "ally" if other.is_pc == caster.is_pc else "enemy"
        feet = distance_feet(caster.position, other.position)
        lines.append(f"- {other.name} ({side}), {feet} ft from {caster.name}")
    battle_map = game_state.battle_map
    where = f"a {battle_map.width} x {battle_map.height} square battlefield" if battle_map else ""
    return "\n".join([where, *lines]).strip() or "(nothing else is nearby)"


def _build_prompt(game_state: GameState, event: Event) -> str:
    spell_name = str(event.payload.get("spell", ""))
    index = str(event.payload.get("spell_index", ""))
    spell = load_srd().spells.get(index, {})
    caster = game_state.characters[event.actor]
    return load_prompt("spell_adjudicator").format(
        caster=f"{caster.name}, a level {caster.level} {caster.class_.lower()}",
        spell_name=spell_name,
        spell_text="\n".join(spell.get("desc", [])) or "(no text available)",
        guidance=guideline_for(index) or _DEFAULT_GUIDANCE,
        attempt=str(event.payload.get("attempt", "")).replace('"', "'") or "(not specified)",
        scene=_scene_summary(game_state, caster),
    )


def adjudicate_utility_spell(game_state: GameState, event: Event) -> SpellRuling:
    """The verdict for one `spell_cast` utility event; a plain success if the model can't give
    one (see the module docstring)."""
    try:
        ruling = chat_structured(
            messages=[{"role": "user", "content": _build_prompt(game_state, event)}],
            schema=SpellRuling,
            temperature=0.3,
        )
        # Grammar-constrained decoding sometimes leaves stray JSON punctuation after the sentence.
        return ruling.model_copy(update={"ruling": re.sub(r"[{}\[\]]+", "", ruling.ruling).strip()})
    except Exception as exc:  # noqa: BLE001 - any failure falls back the same way
        logger.warning("spell adjudication failed (%s); ruling success", exc)
        return SpellRuling(ruling="", outcome="success")


def spell_adjudicator_node(state: GraphState) -> dict[str, Any]:
    game_state = state["game_state"]
    new_events = game_state.events[state["events_before"] :]
    for event in new_events:
        if event.type != "spell_cast" or not event.payload.get("utility"):
            continue
        verdict = adjudicate_utility_spell(game_state, event)
        ruling = Event(
            round=game_state.round,
            turn_index=game_state.current_turn,
            actor=event.actor,
            type="spell_ruling",
            payload={
                "spell": event.payload.get("spell"),
                "attempt": event.payload.get("attempt"),
                "outcome": verdict.outcome,
                "ruling": verdict.ruling.strip(),
            },
        )
        game_state.events.append(ruling)
        log_mechanic(ruling, game_state)
    return {}
