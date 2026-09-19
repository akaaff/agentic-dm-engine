"""_combat_summaries/_state_update_message (api/ws/session.py) - a
proactive UX ask, not a bug fix: the character sheet's "AC 15"/"+5 to hit"
should show base + modifiers, not a bare number. Pure, deterministic, and
fully testable offline against a directly-constructed Session - unlike the
narrator/intent-parser nodes, nothing here calls an LLM.
"""

from __future__ import annotations

from src.api.ws.session import Session, _combat_summaries, _state_update_message, create_session
from src.engine.encounter import monster_to_character
from src.engine.position import Position
from src.engine.srd_loader import SrdIndex, load_srd
from src.engine.state import Character, GameState


def _fighter() -> Character:
    return Character(
        id="thorin",
        name="Thorin",
        race="Human",
        class_="Fighter",
        class_index="fighter",
        background="Acolyte",
        is_pc=True,
        hp=12,
        max_hp=12,
        ac=16,
        level=1,
        position=Position(x=0, y=0),
        stats={"STR": 16, "DEX": 14, "CON": 14, "INT": 10, "WIS": 10, "CHA": 8},
        speed=30,
        proficiency_bonus=2,
        equipped_weapons=["longsword"],
        equipped_armor="chain-mail",
    )


def _session_with(*characters: Character, srd_index: SrdIndex | None) -> Session:
    game_state = GameState(
        encounter_id="combat_summary_test",
        characters={c.id: c for c in characters},
        turn_order=[c.id for c in characters],
        current_turn=0,
        round=1,
    )
    return create_session("combat_summary_test", game_state, srd=srd_index)


def test_combat_summaries_includes_ac_breakdown_and_attacks_for_a_pc() -> None:
    srd = load_srd()
    fighter = _fighter()
    session = _session_with(fighter, srd_index=srd)

    summaries = _combat_summaries(session)

    assert set(summaries) == {"thorin"}
    entry = summaries["thorin"]
    assert isinstance(entry, dict)
    # Chain Mail: base 16, dex_bonus False - no DEX line for this armor.
    assert entry["ac_breakdown"] == [("Chain Mail base", 16)]
    assert len(entry["attacks"]) == 1
    attack = entry["attacks"][0]
    assert attack["source_name"] == "Longsword"
    assert attack["attack_bonus"] == 5  # STR mod 3 + proficiency 2
    assert attack["attack_bonus_breakdown"] == [("STR mod", 3), ("proficiency", 2)]


def test_combat_summaries_excludes_monsters() -> None:
    srd = load_srd()
    fighter = _fighter()
    goblin = monster_to_character(srd.monsters["goblin"], "goblin_1", Position(x=2, y=0))
    session = _session_with(fighter, goblin, srd_index=srd)

    summaries = _combat_summaries(session)

    assert set(summaries) == {"thorin"}


def test_combat_summaries_empty_when_srd_is_not_set() -> None:
    # The demo-encounter/offline-test fallback (Session.srd defaults to
    # None) - must degrade to an empty dict, not crash.
    fighter = _fighter()
    session = _session_with(fighter, srd_index=None)

    assert _combat_summaries(session) == {}
    assert _state_update_message(session)["combat_summaries"] == {}


def test_state_update_message_carries_combat_summaries() -> None:
    srd = load_srd()
    fighter = _fighter()
    session = _session_with(fighter, srd_index=srd)

    message = _state_update_message(session)

    assert message["type"] == "state_update"
    assert "thorin" in message["combat_summaries"]  # type: ignore[operator]
