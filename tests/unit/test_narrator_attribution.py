"""Issue #104: with three similar party members the narrator mixed them up -
"Vex snarls" for a spell Elara cast, a wizard "readies her sword". The event
lines now carry each character's name and class instead of an opaque id, and a
deterministic check retries a draft that names a bystander or never names the
actor. The model call is monkeypatched; the real model's behaviour is
live-verified separately."""

from __future__ import annotations

from typing import Any

import pytest

from src.engine.events import Event
from src.engine.position import Position
from src.engine.state import Character, GameState
from src.graph.nodes import narrator as narrator_module
from src.graph.nodes.narrator import (
    _event_line,
    _misattribution_problem,
    character_label,
    narrator_node,
)


def _pc(char_id: str, name: str, class_: str) -> Character:
    return Character(
        id=char_id,
        name=name,
        race="Human",
        class_=class_,
        class_index=class_.lower(),
        background="Acolyte",
        is_pc=True,
        hp=10,
        max_hp=10,
        ac=12,
        position=Position(x=0, y=0),
        stats={"STR": 10, "DEX": 10, "CON": 10, "INT": 10, "WIS": 10, "CHA": 10},
        speed=30,
        proficiency_bonus=2,
    )


def _goblin() -> Character:
    return Character(
        id="goblin_1",
        name="Goblin 1",
        race="humanoid",
        class_="Monster",
        monster_index="goblin",
        background="",
        is_pc=False,
        hp=7,
        max_hp=7,
        ac=15,
        position=Position(x=3, y=0),
        stats={"STR": 8, "DEX": 14, "CON": 10, "INT": 10, "WIS": 8, "CHA": 8},
        speed=30,
        proficiency_bonus=2,
    )


def _state() -> GameState:
    elara = _pc("elara", "Elara", "Wizard")
    vex = _pc("vex", "Vex", "Rogue")
    fenn = _pc("fenn", "Fenn", "Druid")
    goblin = _goblin()
    return GameState(
        encounter_id="attribution_test",
        characters={c.id: c for c in (elara, vex, fenn, goblin)},
        turn_order=["elara", "vex", "fenn", "goblin_1"],
        current_turn=0,
        round=1,
    )


def _cast_event() -> Event:
    return Event(
        round=1,
        turn_index=0,
        actor="elara",
        type="spell_cast",
        payload={"spell": "Fire Bolt", "target": "goblin_1", "hit": True},
    )


def _graph_state(state: GameState) -> dict[str, Any]:
    return {
        "game_state": state,
        "raw_text": "",
        "parsed_action": None,
        "events_before": 0,
        "round_before": 1,
        "narration": None,
        "scene_image_url": None,
    }


def test_labels_carry_the_class_for_party_members_only() -> None:
    state = _state()
    assert character_label(state.characters["elara"]) == "Elara the wizard"
    assert character_label(state.characters["goblin_1"]) == "Goblin 1"


def test_event_lines_show_names_and_classes_instead_of_ids() -> None:
    state = _state()
    labels = {cid: character_label(c) for cid, c in state.characters.items()}
    line = _event_line(_cast_event(), labels)

    assert "actor=Elara the wizard" in line
    assert "'target': 'Goblin 1'" in line
    assert "elara" not in line and "goblin_1" not in line


def test_the_special_cased_event_lines_use_labels_too() -> None:
    state = _state()
    labels = {cid: character_label(c) for cid, c in state.characters.items()}
    hazard = Event(
        round=1,
        turn_index=0,
        actor="vex",
        type="hazard_damage",
        payload={"amount": 2, "damage_type": "piercing"},
    )
    assert _event_line(hazard, labels).startswith("- Vex the rogue steps on hazardous terrain")
    save = Event(
        round=1,
        turn_index=0,
        actor="elara",
        type="saving_throw",
        payload={
            "kind": "spell_save",
            "target": "goblin_1",
            "ability": "DEX",
            "spell": "Sleep",
            "success": False,
        },
    )
    line = _event_line(save, labels)
    assert "Goblin 1 makes a DEX saving throw against Elara the wizard's Sleep" in line


def test_without_labels_the_line_is_unchanged() -> None:
    assert "actor=elara" in _event_line(_cast_event())


def test_a_bystander_named_in_the_narration_is_flagged() -> None:
    state = _state()
    problem = _misattribution_problem(
        'Elara hurls fire. "Missed by a hair, goblin scum!" Vex snarls.',
        [_cast_event()],
        state.characters,
    )
    assert problem is not None and "Vex" in problem


def test_a_sole_party_actor_who_is_never_named_is_flagged() -> None:
    state = _state()
    problem = _misattribution_problem(
        "A streak of flame leaps across the room and the goblin staggers.",
        [_cast_event()],
        state.characters,
    )
    assert problem is not None and "Elara" in problem


def test_a_correct_narration_passes() -> None:
    state = _state()
    assert (
        _misattribution_problem(
            "Elara flicks her fingers and a bolt of fire scorches the goblin.",
            [_cast_event()],
            state.characters,
        )
        is None
    )


def test_monsters_are_not_held_to_being_named() -> None:
    # "The goblin lunges" is fine - only party members are checked by name.
    state = _state()
    goblin_attack = Event(
        round=1,
        turn_index=3,
        actor="goblin_1",
        type="attack_roll",
        payload={"target": "vex", "hit": False},
    )
    assert (
        _misattribution_problem(
            "The goblin lunges at Vex and misses.", [goblin_attack], state.characters
        )
        is None
    )


def test_a_misattributed_draft_is_retried_with_the_problem_spelled_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = _state()
    state.events.append(_cast_event())
    prompts: list[str] = []
    drafts = iter(["Vex snarls as the fire flies.", "Elara sends a bolt of fire at the goblin."])

    def fake_chat(messages: list[dict[str, str]], **_kwargs: Any) -> str:
        prompts.append(messages[0]["content"])
        return next(drafts)

    monkeypatch.setattr(narrator_module, "chat_english_only", fake_chat)
    result = narrator_node(_graph_state(state))  # type: ignore[arg-type]

    assert result["narration"] == "Elara sends a bolt of fire at the goblin."
    assert len(prompts) == 2
    assert "rejected because it names Vex" in prompts[1]
    # The first prompt shows names and classes, not ids.
    assert "Elara the wizard" in prompts[0] and "Vex the rogue" in prompts[0]
    assert "actor=elara" not in prompts[0]


def test_if_every_draft_fails_the_check_the_last_one_is_still_used(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = _state()
    state.events.append(_cast_event())
    calls = {"n": 0}

    def fake_chat(messages: list[dict[str, str]], **_kwargs: Any) -> str:
        calls["n"] += 1
        return f"Vex snarls (draft {calls['n']})."

    monkeypatch.setattr(narrator_module, "chat_english_only", fake_chat)
    result = narrator_node(_graph_state(state))  # type: ignore[arg-type]

    assert calls["n"] == 3
    assert result["narration"] == "Vex snarls (draft 3)."
