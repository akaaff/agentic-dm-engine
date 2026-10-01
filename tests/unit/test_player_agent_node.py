"""player_agent_node (Day 15) generates a companion's own free-text turn via
the teacher model, then intent_parser (already real since Day 12) parses it
normally - no separate companion-specific parsing path. These tests stay
offline by monkeypatching chat_english_only() directly (issue #16's
English-only retry wrapper around chat() - the module calls this, not chat()
itself, since Day 15), mirroring how test_ws_session.py stubs narrator_fn
instead of hitting a live model.
"""

from __future__ import annotations

from typing import Any

import pytest

from src.engine.actions import ParsedAction
from src.engine.position import Position
from src.engine.state import Character, GameState
from src.graph.nodes import player_agent as player_agent_module
from src.graph.nodes.player_agent import player_agent_node
from src.graph.state_schema import GraphState


def _make_character(
    char_id: str,
    *,
    is_pc: bool,
    is_companion: bool = False,
    persona: str | None = None,
    hp: int = 10,
    is_dead: bool = False,
    position: Position | None = None,
    class_index: str | None = None,
    equipped_weapons: list[str] | None = None,
    known_spells: list[str] | None = None,
    prepared_spells: list[str] | None = None,
    spell_slots: dict[int, int] | None = None,
    class_resources: dict[str, int] | None = None,
) -> Character:
    return Character(
        id=char_id,
        name=char_id.title(),
        is_pc=is_pc,
        is_companion=is_companion,
        persona=persona,
        is_dead=is_dead,
        hp=hp,
        max_hp=10,
        ac=15,
        position=position or Position(x=1, y=2),
        stats={"STR": 14, "DEX": 12, "CON": 13, "INT": 10, "WIS": 11, "CHA": 8},
        proficiency_bonus=2,
        speed=30,
        race="Human",
        class_="Fighter",
        class_index=class_index,
        background="Acolyte",
        equipped_weapons=equipped_weapons or [],
        known_spells=known_spells or [],
        prepared_spells=prepared_spells or [],
        spell_slots=spell_slots or {},
        class_resources=class_resources or {},
    )


def _make_state(actor: Character, others: list[Character]) -> GameState:
    characters = {actor.id: actor, **{c.id: c for c in others}}
    return GameState(
        encounter_id="test",
        characters=characters,
        turn_order=[actor.id, *[c.id for c in others]],
        current_turn=0,
        round=1,
        events=[],
        status="in_progress",
    )


def _graph_state(
    game_state: GameState, raw_text: str = "", parsed_action: Any = None
) -> GraphState:
    return {
        "game_state": game_state,
        "raw_text": raw_text,
        "parsed_action": parsed_action,
        "events_before": 0,
        "round_before": 1,
        "narration": None,
        "scene_image_url": None,
    }


def _explode(*args: Any, **kwargs: Any) -> str:
    raise AssertionError("chat_english_only() should not have been called")


def test_bypasses_when_parsed_action_already_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(player_agent_module, "chat_english_only", _explode)
    companion = _make_character("companion_grom", is_pc=True, is_companion=True)
    goblin = _make_character("goblin_1", is_pc=False)
    state = _make_state(companion, [goblin])
    action = ParsedAction(actor=companion.id, verb="end_turn", raw_text="done")

    result = player_agent_node(_graph_state(state, parsed_action=action))

    assert result == {}


def test_bypasses_when_raw_text_already_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(player_agent_module, "chat_english_only", _explode)
    companion = _make_character("companion_grom", is_pc=True, is_companion=True)
    goblin = _make_character("goblin_1", is_pc=False)
    state = _make_state(companion, [goblin])

    result = player_agent_node(_graph_state(state, raw_text="I attack goblin_1"))

    assert result == {}


def test_bypasses_for_a_non_companion_actor(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(player_agent_module, "chat_english_only", _explode)
    human_pc = _make_character("thorin", is_pc=True, is_companion=False)
    goblin = _make_character("goblin_1", is_pc=False)
    state = _make_state(human_pc, [goblin])

    result = player_agent_node(_graph_state(state))

    assert result == {}


def test_generates_raw_text_for_a_companions_empty_turn(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def _fake_chat(messages: list[dict[str, str]], temperature: float = 0.7) -> str:
        captured["prompt"] = messages[0]["content"]
        return "  I swing my axe at goblin_1.  \n"

    monkeypatch.setattr(player_agent_module, "chat_english_only", _fake_chat)
    companion = _make_character(
        "companion_grom", is_pc=True, is_companion=True, persona="Gruff dwarf, loyal to a fault."
    )
    goblin = _make_character("goblin_1", is_pc=False)
    state = _make_state(companion, [goblin])

    result = player_agent_node(_graph_state(state))

    assert result == {"raw_text": "I swing my axe at goblin_1."}
    assert "Gruff dwarf, loyal to a fault." in captured["prompt"]
    assert "goblin_1" in captured["prompt"]


def test_unconscious_companion_is_forced_to_a_death_save(monkeypatch: pytest.MonkeyPatch) -> None:
    # Regression guard for a real bug hit on the first live autoplay run:
    # turn_engine only accepts "death_save" while hp<=0, but nothing told
    # the persona LLM that - it kept declaring ordinary actions that
    # turn_engine rejected forever, hanging the encounter. No LLM call
    # should happen at all for this case (it's purely mechanical).
    monkeypatch.setattr(player_agent_module, "chat_english_only", _explode)
    companion = _make_character("companion_grom", is_pc=True, is_companion=True, hp=0)
    goblin = _make_character("goblin_1", is_pc=False)
    state = _make_state(companion, [goblin])

    result = player_agent_node(_graph_state(state))

    assert result["parsed_action"].verb == "death_save"
    assert result["parsed_action"].actor == "companion_grom"


def test_dead_companion_is_not_forced_to_a_death_save(monkeypatch: pytest.MonkeyPatch) -> None:
    # A dead (not merely unconscious) companion never gets another turn at
    # all in practice (turn_engine skips dead characters) - but if this node
    # were ever called for one anyway, it must not misclassify "dead" as
    # "needs a death save".
    def _fake_chat(messages: list[dict[str, str]], temperature: float = 0.7) -> str:
        return "I lie still."

    monkeypatch.setattr(player_agent_module, "chat_english_only", _fake_chat)
    companion = _make_character("companion_grom", is_pc=True, is_companion=True, hp=0, is_dead=True)
    goblin = _make_character("goblin_1", is_pc=False)
    state = _make_state(companion, [goblin])

    result = player_agent_node(_graph_state(state))

    assert result == {"raw_text": "I lie still."}


def test_prompt_lists_the_actors_own_equipped_weapon_and_range(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Live-found (user report: a Bard dashed toward melee instead of
    # casting an in-range spell) - the prompt used to say nothing at all
    # about what the actor actually has. A Fighter with a longsword
    # equipped should see its real SRD range (melee, 5ft) spelled out.
    captured: dict[str, Any] = {}

    def _fake_chat(messages: list[dict[str, str]], temperature: float = 0.7) -> str:
        captured["prompt"] = messages[0]["content"]
        return "I attack goblin_1."

    monkeypatch.setattr(player_agent_module, "chat_english_only", _fake_chat)
    companion = _make_character(
        "companion_thorin", is_pc=True, is_companion=True, equipped_weapons=["longsword"]
    )
    goblin = _make_character("goblin_1", is_pc=False)
    state = _make_state(companion, [goblin])

    player_agent_node(_graph_state(state))

    assert "Equipped weapon(s): Longsword (5ft)" in captured["prompt"]


def test_prompt_flags_no_melee_weapon_equipped(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def _fake_chat(messages: list[dict[str, str]], temperature: float = 0.7) -> str:
        captured["prompt"] = messages[0]["content"]
        return "I cast thunderwave."

    monkeypatch.setattr(player_agent_module, "chat_english_only", _fake_chat)
    companion = _make_character("companion_pip", is_pc=True, is_companion=True, class_index="bard")
    goblin = _make_character("goblin_1", is_pc=False)
    state = _make_state(companion, [goblin])

    player_agent_node(_graph_state(state))

    assert "Equipped weapon(s): none - no melee weapon to attack with" in captured["prompt"]


def test_prompt_lists_known_spells_slots_and_cantrips_with_real_srd_ranges(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A Bard (issue the user actually reported) with Thunderwave known and
    # a 1st-level slot remaining should see both the spell's real SRD range
    # and the slot count - not just a bare class/persona description.
    captured: dict[str, Any] = {}

    def _fake_chat(messages: list[dict[str, str]], temperature: float = 0.7) -> str:
        captured["prompt"] = messages[0]["content"]
        return "I cast thunderwave at goblin_1."

    monkeypatch.setattr(player_agent_module, "chat_english_only", _fake_chat)
    companion = _make_character(
        "companion_pip",
        is_pc=True,
        is_companion=True,
        class_index="bard",
        known_spells=["thunderwave", "healing-word"],
        spell_slots={1: 2},
        class_resources={"bardic_inspiration": 2},
    )
    goblin = _make_character("goblin_1", is_pc=False)
    state = _make_state(companion, [goblin])

    player_agent_node(_graph_state(state))

    prompt = captured["prompt"]
    assert "Cantrips you know" in prompt
    assert "Healing Word (60ft)" in prompt
    assert "Thunderwave (5ft)" in prompt  # SRD range is "Self (15-foot cube)" - falls back to 5ft
    assert "Spell slots: level 1: 2 remaining" in prompt
    assert "Class resources: bardic inspiration: 2 remaining" in prompt


def test_prompt_annotates_other_characters_with_distance_rank_and_direction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Same gap/fix shape as issue #48's intent_parser.py annotations - the
    # model needs real distance/direction, not raw (x, y) pairs, to judge
    # whether anything it has is already in range.
    captured: dict[str, Any] = {}

    def _fake_chat(messages: list[dict[str, str]], temperature: float = 0.7) -> str:
        captured["prompt"] = messages[0]["content"]
        return "I attack goblin_1."

    monkeypatch.setattr(player_agent_module, "chat_english_only", _fake_chat)
    companion = _make_character(
        "companion_pip", is_pc=True, is_companion=True, position=Position(x=0, y=0)
    )
    near = _make_character("goblin_1", is_pc=False, position=Position(x=1, y=0))
    far = _make_character("goblin_2", is_pc=False, position=Position(x=5, y=0))
    state = _make_state(companion, [far, near])  # deliberately out of distance order

    player_agent_node(_graph_state(state))

    prompt = captured["prompt"]
    assert (
        "goblin_1 (Goblin_1, monster): HP 10/10, position (1, 0), 5ft away (closest), east of you"
    ) in prompt
    assert (
        "goblin_2 (Goblin_2, monster): HP 10/10, position (5, 0), "
        "25ft away (2nd closest), east of you"
    ) in prompt
    # Nearest enemy listed before the farther one, regardless of input order.
    assert prompt.index("goblin_1") < prompt.index("goblin_2")
