"""Issue #98: the intent parser now knows what the actor actually has (spells,
weapons) - a block in the prompt for the teacher backend, and a deterministic
post-pass for a `cast_spell` whose name is not a real spell. Pure and offline;
the real model's behaviour is live-verified separately."""

from __future__ import annotations

import pytest

from src.engine.actions import ParsedAction
from src.engine.character_creation import create_character
from src.engine.encounter import monster_to_character
from src.engine.position import Position
from src.engine.srd_loader import load_srd
from src.engine.state import Character, GameState
from src.engine.turn_engine import TurnEngineError, resolve_action
from src.graph.nodes.intent_parser import _reconcile_cast_name, build_intent_parser_prompt
from src.graph.state_schema import GraphState


def _wizard() -> Character:
    wizard = create_character(
        character_id="elara",
        name="Elara",
        race_index="elf",
        class_index="wizard",
        background_index="acolyte",
        base_ability_scores={"STR": 8, "DEX": 14, "CON": 12, "INT": 15, "WIS": 13, "CHA": 10},
        chosen_skills=["skill-arcana", "skill-history"],
        chosen_prepared_spells=["shield", "magic-missile", "sleep"],
        chosen_equipment=["dagger"],
        position=Position(x=0, y=0),
    )
    wizard.inventory.append("dart")  # carried, not equipped
    return wizard


def _state(actor: Character) -> GameState:
    goblin = monster_to_character(load_srd().monsters["goblin"], "goblin_1", Position(x=3, y=0))
    return GameState(
        encounter_id="options_test",
        characters={actor.id: actor, goblin.id: goblin},
        turn_order=[actor.id, goblin.id],
        current_turn=0,
        round=1,
    )


def _graph_state(game_state: GameState, text: str = "I attack") -> GraphState:
    return {
        "game_state": game_state,
        "raw_text": text,
        "parsed_action": None,
        "events_before": 0,
        "round_before": 1,
        "narration": None,
        "scene_image_url": None,
    }


def test_the_prompt_lists_what_the_actor_has() -> None:
    prompt = build_intent_parser_prompt(_graph_state(_state(_wizard())), include_actor_options=True)

    assert "use these exact names for item_or_spell" in prompt
    assert "Equipped weapon(s): Dagger" in prompt
    assert "Other weapons carried (not equipped): Dart" in prompt
    assert "Fire Bolt" in prompt  # a wizard cantrip
    assert "Shield" in prompt and "Magic Missile" in prompt  # prepared spells
    assert prompt.index("What the current actor has") < prompt.index("Visible characters:")


def test_without_the_block_the_prompt_is_the_original_format() -> None:
    # The training-data generator and the distilled student see this exact shape.
    prompt = build_intent_parser_prompt(
        _graph_state(_state(_wizard())), include_actor_options=False
    )

    assert "What the current actor has" not in prompt
    assert " ft.\n\nVisible characters:" in prompt


def test_the_teacher_backend_includes_the_block_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    from src import config

    graph_state = _graph_state(_state(_wizard()))
    monkeypatch.setattr(config, "INTENT_PARSER_BACKEND", "teacher")
    assert "What the current actor has" in build_intent_parser_prompt(graph_state)
    monkeypatch.setattr(config, "INTENT_PARSER_BACKEND", "finetuned_ollama")
    assert "What the current actor has" not in build_intent_parser_prompt(graph_state)


def _cast(name: str | None, target: str | None = "goblin_1") -> ParsedAction:
    return ParsedAction(
        actor="elara", verb="cast_spell", target=target, item_or_spell=name, raw_text="x"
    )


def test_a_weapon_named_as_a_spell_aimed_at_an_enemy_is_an_attack() -> None:
    state = _state(_wizard())
    fixed = _reconcile_cast_name(_cast("dart"), state)
    assert (fixed.verb, fixed.item_or_spell, fixed.target) == ("attack", "dart", "goblin_1")


def test_a_weapon_named_as_a_spell_with_no_enemy_target_is_left_alone() -> None:
    # "I cast shillelagh on my dagger": the spell is unsupported, but turning it
    # into an attack on someone would be inventing an action.
    state = _state(_wizard())
    assert _reconcile_cast_name(_cast("dagger", target="elara"), state).verb == "cast_spell"
    assert _reconcile_cast_name(_cast("dagger", target=None), state).verb == "cast_spell"


def test_a_near_miss_of_a_spell_the_actor_has_is_corrected() -> None:
    state = _state(_wizard())
    assert _reconcile_cast_name(_cast("fire bolts"), state).item_or_spell == "Fire Bolt"
    assert _reconcile_cast_name(_cast("magic missle"), state).item_or_spell == "Magic Missile"


def test_a_real_spell_name_is_never_touched_even_if_the_actor_cant_cast_it() -> None:
    state = _state(_wizard())
    assert _reconcile_cast_name(_cast("flamestrike"), state).item_or_spell == "flamestrike"


def test_a_name_that_is_nothing_is_left_for_the_engine() -> None:
    state = _state(_wizard())
    fixed = _reconcile_cast_name(_cast("zzz glorp"), state)
    assert (fixed.verb, fixed.item_or_spell) == ("cast_spell", "zzz glorp")


def test_an_unknown_spell_rejection_lists_what_the_actor_can_cast() -> None:
    wizard = _wizard()
    state = _state(wizard)
    with pytest.raises(TurnEngineError, match=r"you can cast: .*Fire Bolt.*Shield"):
        resolve_action(state, _cast("zzz glorp"), None)  # type: ignore[arg-type]


def test_consecutive_casts_of_a_condition_buff_merge_into_one_multi_target_cast() -> None:
    # With the options block the model answers "bless me and Buddy" as two casts;
    # the first would end the turn and the second never resolve.
    from src.graph.nodes.intent_parser import _expand_area_spell_targets

    wizard = _wizard()
    state = _state(wizard)
    casts = [_cast("Bless", target="buddy"), _cast("Bless", target="elara")]
    merged = _expand_area_spell_targets(casts, state, "elara", "I cast bless on myself and Buddy")

    assert len(merged) == 1
    assert merged[0].targets == ["buddy", "elara"]
    assert merged[0].target == "buddy"


def _monk() -> Character:
    monk = _wizard()
    monk.class_index = "monk"
    monk.class_ = "Monk"
    monk.equipped_weapons = ["dart"]
    return monk


def test_an_equipped_weapon_echoed_into_a_punch_is_replaced_by_the_unarmed_strike() -> None:
    # The options block lists the Dart, and the model copies it into "I punch the
    # goblin" - a held weapon only counts if the player's own words name it.
    from src.graph.nodes.intent_parser import _normalize_unarmed_attack

    monk = _monk()
    state = _state(monk)
    echoed = ParsedAction(
        actor="elara", verb="attack", target="goblin_1", item_or_spell="Dart", raw_text="x"
    )
    fixed = _normalize_unarmed_attack(echoed, state, "I punch the goblin")
    assert fixed.item_or_spell == "unarmed strike"
    # ...but "with my dart" is the player naming it.
    kept = _normalize_unarmed_attack(echoed, state, "I kick the goblin with my dart")
    assert kept.item_or_spell == "Dart"


def test_a_monks_bonus_kick_filed_as_an_offhand_attack_is_a_martial_arts_strike() -> None:
    from src.graph.nodes.intent_parser import _normalize_unarmed_attack

    state = _state(_monk())
    offhand = ParsedAction(
        actor="elara", verb="offhand_attack", target="goblin_1", item_or_spell="kick", raw_text="x"
    )
    fixed = _normalize_unarmed_attack(offhand, state, "I punch it and then kick it")
    assert (fixed.verb, fixed.item_or_spell) == ("martial_arts_strike", None)
