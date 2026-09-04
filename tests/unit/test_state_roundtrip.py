import pytest
from pydantic import ValidationError

from src.engine.actions import ParsedAction
from src.engine.events import Event
from src.engine.position import Position
from src.engine.state import Character, Condition, GameState


def _make_character(char_id: str, is_pc: bool) -> Character:
    return Character(
        id=char_id,
        name=char_id.title(),
        is_pc=is_pc,
        hp=10,
        max_hp=10,
        ac=15,
        position=Position(x=1, y=2),
        conditions=[Condition(name="prone", duration_rounds=1, source="tripped")],
        spell_slots={1: 2},
        inventory=["longsword", "shield"],
        stats={"STR": 14, "DEX": 12, "CON": 13, "INT": 10, "WIS": 11, "CHA": 8},
        proficiency_bonus=2,
        speed=30,
        race="Human",
        class_="Fighter",
        background="Acolyte",
    )


def _make_game_state() -> GameState:
    pc = _make_character("thorin", is_pc=True)
    goblin = _make_character("goblin_1", is_pc=False)
    event = Event(
        round=1,
        turn_index=0,
        actor="thorin",
        type="attack_roll",
        payload={"roll": 17, "target_ac": 15, "hit": True},
    )
    pending = ParsedAction(
        actor="goblin_1",
        verb="attack",
        target="thorin",
        raw_text="the goblin attacks",
    )
    return GameState(
        encounter_id="goblin_ambush",
        characters={"thorin": pc, "goblin_1": goblin},
        turn_order=["thorin", "goblin_1"],
        current_turn=0,
        round=1,
        events=[event],
        pending_action=pending,
        status="in_progress",
    )


def test_game_state_round_trips_through_json() -> None:
    original = _make_game_state()

    restored = GameState.model_validate_json(original.model_dump_json())

    assert restored == original


def test_parsed_action_rejects_unknown_verb() -> None:
    with pytest.raises(ValidationError):
        ParsedAction(actor="thorin", verb="fly_away", raw_text="I fly away")  # type: ignore[arg-type]


def test_event_rejects_unknown_type() -> None:
    with pytest.raises(ValidationError):
        Event(round=1, turn_index=0, actor="thorin", type="teleport")  # type: ignore[arg-type]


def test_condition_rejects_unknown_name() -> None:
    with pytest.raises(ValidationError):
        Condition(name="on_fire")  # type: ignore[arg-type]
