"""The intent-parser prompt is a str.format() template, so any literal brace in
a verb description (e.g. a JSON example like params {"amount": N}) must be
escaped as {{ }} - an unescaped one raised KeyError on every free-text turn,
and no unit test noticed because none rendered the real template."""

from __future__ import annotations

from src.engine.character_creation import create_character
from src.engine.encounter import monster_to_character
from src.engine.position import Position
from src.engine.srd_loader import load_srd
from src.engine.state import GameState
from src.graph.nodes.intent_parser import build_intent_parser_prompt


def test_the_real_template_renders_and_lists_every_verb_it_describes() -> None:
    fighter = create_character(
        character_id="brannock",
        name="Brannock",
        race_index="human",
        class_index="fighter",
        background_index="acolyte",
        base_ability_scores={"STR": 15, "DEX": 13, "CON": 14, "INT": 8, "WIS": 12, "CHA": 10},
        chosen_skills=["skill-athletics", "skill-perception"],
        chosen_equipment=["longsword"],
        position=Position(x=0, y=0),
    )
    goblin = monster_to_character(load_srd().monsters["goblin"], "goblin_1", Position(x=2, y=0))
    state = GameState(
        encounter_id="t",
        characters={fighter.id: fighter, goblin.id: goblin},
        turn_order=[fighter.id, goblin.id],
        current_turn=0,
        round=1,
    )

    prompt = build_intent_parser_prompt(
        {
            "game_state": state,
            "raw_text": "I attack the goblin",
            "parsed_action": None,
            "events_before": 0,
            "round_before": 1,
            "narration": None,
            "scene_image_url": None,
        }
    )

    assert "I attack the goblin" in prompt
    assert '"lay_on_hands"' in prompt
    assert '{"amount": N}' in prompt  # the escaped braces render as literal ones
