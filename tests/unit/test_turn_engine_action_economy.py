"""Phase 9H: a narrow slice of real action economy on top of this engine's
one-action-per-turn model. A bonus-action spell (SRD casting_time "1 bonus
action", e.g. Healing Word) doesn't end the turn, so the actor still gets
their main action afterward - gated by Character.bonus_action_used so a
second bonus-action cast the same turn is rejected. "disengage" finally has
a real effect (Character.disengaged_this_turn): the one reaction this
engine models, an opportunity attack, fires when a character moves out of
a hostile's 5ft reach without having disengaged, capped at one reaction per
reactor per round (Character.reaction_used_this_round).
"""

from __future__ import annotations

import pytest

from src.cli.play import build_demo_encounter
from src.engine.actions import ParsedAction
from src.engine.character_creation import create_character
from src.engine.encounter import build_encounter_state, monster_to_character
from src.engine.position import BattleMap, Position
from src.engine.srd_loader import load_srd
from src.engine.state import Character, GameState
from src.engine.turn_engine import TurnEngineError, resolve_action


class _FixedRandom:
    def __init__(self, values: list[int]) -> None:
        self._values = list(values)

    def randint(self, a: int, b: int) -> int:
        return self._values.pop(0)


def _cleric() -> Character:
    return create_character(
        character_id="mira",
        name="Mira",
        race_index="human",
        class_index="cleric",
        background_index="acolyte",
        base_ability_scores={"STR": 13, "DEX": 10, "CON": 14, "INT": 8, "WIS": 15, "CHA": 12},
        chosen_skills=["skill-medicine", "skill-religion"],
        # WIS15 -> mod3 after Human's +1; issue #30's follow-up phase
        # requires exactly prepared_spell_count("cleric", 1, 3) == 4 real
        # level-1 Cleric spells.
        chosen_prepared_spells=["cure-wounds", "healing-word", "bless", "shield-of-faith"],
    )


def _bonus_action_state() -> GameState:
    # Initiative rolls: mira 20 (goes first, guaranteeing it's her turn for
    # the whole test), the demo encounter's two goblins 5 and 5 - same
    # values test_turn_engine_spells.py's heal-spell tests already use for
    # this identical solo-mira-in-the-demo-encounter setup.
    encounter = build_demo_encounter()
    return build_encounter_state(encounter, [_cleric()], _FixedRandom([20, 5, 5]))  # type: ignore[arg-type]


# --- Bonus-action spells (Healing Word) ------------------------------------


def test_bonus_action_spell_does_not_end_the_turn_and_a_main_action_still_can() -> None:
    # Mira (human Cleric, WIS15->16 after the racial +1 -> mod+3) casts
    # Healing Word on herself (bonus action - CON... no, WIS mod applies):
    # 1d4+3, roll 3 -> 6 healed. Turn should NOT advance - still her turn -
    # so she can immediately attack goblin_1 (poked adjacent) as her main
    # action: unarmed strike, STR13->human+1->14->mod+2, +2 proficiency =
    # attack_bonus 4; roll 15 -> total 19 vs goblin AC15, hits; unarmed
    # strike has no damage die, so no further RNG value is consumed for it.
    state = _bonus_action_state()
    state.characters["mira"].hp = 1
    state.characters["goblin_1"].position = state.characters["mira"].position

    heal_action = ParsedAction(
        actor="mira",
        verb="cast_spell",
        target="mira",
        item_or_spell="healing word",
        raw_text="I speak a word of healing over myself",
    )
    resolve_action(state, heal_action, _FixedRandom([3]))  # type: ignore[arg-type]

    assert state.characters["mira"].hp == 7  # 1 + 6
    assert state.characters["mira"].bonus_action_used is True
    # Still Mira's turn - the bonus-action cast didn't advance it.
    assert state.turn_order[state.current_turn] == "mira"

    attack_action = ParsedAction(
        actor="mira", verb="attack", target="goblin_1", raw_text="I punch the goblin"
    )
    resolve_action(state, attack_action, _FixedRandom([15]))  # type: ignore[arg-type]

    attack_event = next(e for e in state.events if e.type == "attack_roll")
    assert attack_event.payload["hit"] is True
    # A real (turn-ending) action was taken - the turn has now advanced away.
    assert state.turn_order[state.current_turn] != "mira"


def test_second_bonus_action_spell_same_turn_is_rejected() -> None:
    state = _bonus_action_state()
    heal_action = ParsedAction(
        actor="mira",
        verb="cast_spell",
        target="mira",
        item_or_spell="healing word",
        raw_text="I speak a word of healing",
    )
    resolve_action(state, heal_action, _FixedRandom([3]))  # type: ignore[arg-type]
    assert state.turn_order[state.current_turn] == "mira"  # still her turn

    with pytest.raises(TurnEngineError, match="already used their bonus action"):
        resolve_action(state, heal_action, _FixedRandom([]))  # type: ignore[arg-type]


def test_ordinary_action_spell_still_ends_the_turn() -> None:
    # Cure Wounds is "1 action" (not bonus action) - the pre-existing Day
    # 14/Phase 9D behavior must be completely unaffected by 9H.
    state = _bonus_action_state()
    state.characters["mira"].hp = 1
    action = ParsedAction(
        actor="mira",
        verb="cast_spell",
        target="mira",
        item_or_spell="cure wounds",
        raw_text="I cast cure wounds on myself",
    )
    resolve_action(state, action, _FixedRandom([6]))  # type: ignore[arg-type]

    assert state.characters["mira"].bonus_action_used is False
    assert state.turn_order[state.current_turn] != "mira"


# --- Opportunity attacks -----------------------------------------------------


def _open_map(width: int, height: int) -> BattleMap:
    return BattleMap(
        width=width,
        height=height,
        terrain=[["floor"] * width for _ in range(height)],
        spawn_points={},
    )


def _build_oa_state(thorin_position: Position, goblin_position: Position) -> GameState:
    thorin = create_character(
        character_id="thorin",
        name="Thorin",
        race_index="human",
        class_index="fighter",
        background_index="acolyte",
        base_ability_scores={"STR": 15, "DEX": 14, "CON": 13, "INT": 12, "WIS": 10, "CHA": 8},
        chosen_skills=["skill-athletics", "skill-perception"],
        chosen_equipment=["longsword"],
        position=thorin_position,
    )
    srd = load_srd()
    goblin = monster_to_character(srd.monsters["goblin"], "goblin_1", goblin_position)
    return GameState(
        encounter_id="oa_test",
        characters={thorin.id: thorin, goblin.id: goblin},
        turn_order=[thorin.id, goblin.id],
        current_turn=0,
        round=1,
        battle_map=_open_map(10, 10),
    )


def test_moving_away_from_an_adjacent_non_disengaged_hostile_triggers_one_opportunity_attack() -> (
    None
):
    # Thorin (0,0) starts 5ft from goblin_1 (1,0) - adjacent - and moves to
    # (3,0), 15ft away - well outside the goblin's 5ft reach. No armor/
    # shield chosen -> AC 10+DEX mod(2) = 12; HP = hit_die(10)+CON mod(2) =
    # 12. No disengage was taken, so the goblin gets one free Scimitar
    # attack (attack_bonus +4): roll 15 -> total 19 vs AC 12, hits; damage
    # 1d6+2, die 3 -> 5 total.
    state = _build_oa_state(Position(x=0, y=0), Position(x=1, y=0))
    action = ParsedAction(
        actor="thorin",
        verb="move",
        raw_text="I back away",
        params={"path": [{"x": 1, "y": 0}, {"x": 2, "y": 0}, {"x": 3, "y": 0}]},
    )
    resolve_action(state, action, _FixedRandom([15, 3]))  # type: ignore[arg-type]

    attack_events = [e for e in state.events if e.type == "attack_roll"]
    assert len(attack_events) == 1
    assert attack_events[0].actor == "goblin_1"
    assert attack_events[0].payload["hit"] is True
    assert state.characters["thorin"].hp == 12 - 5
    assert state.characters["thorin"].position == Position(x=3, y=0)
    assert state.characters["goblin_1"].reaction_used_this_round is True


def test_moving_while_staying_adjacent_triggers_no_opportunity_attack() -> None:
    # Thorin (0,0) moves to (1,1) - still exactly 5ft (diagonal) from
    # goblin_1 (1,0) the whole time - never actually leaves its reach.
    state = _build_oa_state(Position(x=0, y=0), Position(x=1, y=0))
    action = ParsedAction(
        actor="thorin",
        verb="move",
        raw_text="I shuffle sideways",
        params={"path": [{"x": 1, "y": 1}]},
    )
    resolve_action(state, action, _FixedRandom([]))  # type: ignore[arg-type]

    assert not any(e.type == "attack_roll" for e in state.events)


def test_disengaging_then_moving_on_a_later_own_turn_avoids_opportunity_attack() -> None:
    # This is the regression test for a real bug caught while implementing
    # 9H: an early draft reset disengaged_this_turn/bonus_action_used at
    # the top of every resolve_action call (mirroring is_dodging), which
    # silently wiped disengaged_this_turn before this exact scenario could
    # ever see it as True, and would have let a second bonus-action spell
    # slip through in the same turn too. Thorin disengages (his whole turn,
    # per this engine's one-action-per-turn model), the goblin takes its
    # own turn in between (attacks Thorin, missing - a natural 1 always
    # misses), and only THEN does Thorin move away on his own next turn -
    # still protected, since disengaged_this_turn survives every other
    # actor's turn and is only consumed by Thorin's own next move.
    state = _build_oa_state(Position(x=0, y=0), Position(x=1, y=0))
    disengage_action = ParsedAction(actor="thorin", verb="disengage", raw_text="I disengage")
    resolve_action(state, disengage_action, _FixedRandom([]))  # type: ignore[arg-type]
    assert state.characters["thorin"].disengaged_this_turn is True
    assert state.turn_order[state.current_turn] == "goblin_1"

    goblin_attack = ParsedAction(
        actor="goblin_1", verb="attack", target="thorin", raw_text="the goblin attacks"
    )
    resolve_action(state, goblin_attack, _FixedRandom([1]))  # type: ignore[arg-type]
    assert state.turn_order[state.current_turn] == "thorin"
    # Surviving another actor's turn in between doesn't clear it - only
    # Thorin's own next move does.
    assert state.characters["thorin"].disengaged_this_turn is True

    attack_roll_count_before_the_move = sum(1 for e in state.events if e.type == "attack_roll")
    move_action = ParsedAction(
        actor="thorin",
        verb="move",
        raw_text="I walk away, safely",
        params={"path": [{"x": 1, "y": 0}, {"x": 2, "y": 0}, {"x": 3, "y": 0}]},
    )
    resolve_action(state, move_action, _FixedRandom([]))  # type: ignore[arg-type]

    # No NEW attack_roll event from this move specifically - the goblin's
    # own earlier attack (which missed) is still in the log from before.
    attack_roll_count_after_the_move = sum(1 for e in state.events if e.type == "attack_roll")
    assert attack_roll_count_after_the_move == attack_roll_count_before_the_move
    assert state.characters["thorin"].disengaged_this_turn is False  # consumed
    assert state.characters["thorin"].hp == state.characters["thorin"].max_hp


def test_reaction_used_this_round_caps_a_reactor_at_one_opportunity_attack() -> None:
    # A single goblin can't get two opportunity attacks in the same round
    # even if two different allies each disengage-free their way out of its
    # reach - the SRD rule is one reaction per round, not one per mover.
    thorin = create_character(
        character_id="thorin",
        name="Thorin",
        race_index="human",
        class_index="fighter",
        background_index="acolyte",
        base_ability_scores={"STR": 15, "DEX": 14, "CON": 13, "INT": 12, "WIS": 10, "CHA": 8},
        chosen_skills=["skill-athletics", "skill-perception"],
        chosen_equipment=["longsword"],
        position=Position(x=0, y=0),
    )
    elrond = create_character(
        character_id="elrond",
        name="Elrond",
        race_index="elf",
        class_index="wizard",
        background_index="acolyte",
        base_ability_scores={"STR": 8, "DEX": 14, "CON": 12, "INT": 15, "WIS": 13, "CHA": 10},
        chosen_skills=["skill-arcana", "skill-history"],
        chosen_prepared_spells=["magic-missile", "burning-hands", "mage-armor"],
        position=Position(x=1, y=1),
    )
    srd = load_srd()
    goblin = monster_to_character(srd.monsters["goblin"], "goblin_1", Position(x=1, y=0))
    state = GameState(
        encounter_id="oa_cap_test",
        characters={thorin.id: thorin, elrond.id: elrond, goblin.id: goblin},
        turn_order=[thorin.id, elrond.id, goblin.id],
        current_turn=0,
        round=1,
        battle_map=_open_map(10, 10),
    )
    # Both Thorin (0,0) and Elrond (1,1) start 5ft from goblin_1 (1,0).
    thorin_move = ParsedAction(
        actor="thorin",
        verb="move",
        raw_text="I back away",
        params={"path": [{"x": 1, "y": 0}, {"x": 2, "y": 0}, {"x": 3, "y": 0}]},
    )
    resolve_action(state, thorin_move, _FixedRandom([15, 3]))  # type: ignore[arg-type]
    assert len([e for e in state.events if e.type == "attack_roll"]) == 1
    assert state.characters["goblin_1"].reaction_used_this_round is True

    # "move" no longer ends the turn (found live) - explicitly end Thorin's
    # so it's Elrond's turn next, matching this test's actual point (two
    # different movers, one reactor).
    resolve_action(
        state,
        ParsedAction(actor="thorin", verb="end_turn", raw_text="I hold there"),
        _FixedRandom([]),  # type: ignore[arg-type]
    )

    elrond_move = ParsedAction(
        actor="elrond",
        verb="move",
        raw_text="I also back away",
        params={"path": [{"x": 1, "y": 2}, {"x": 1, "y": 3}]},
    )
    resolve_action(state, elrond_move, _FixedRandom([]))  # type: ignore[arg-type]
    # Still only the one attack_roll from Thorin's move - the goblin has no
    # reaction left for Elrond's.
    assert len([e for e in state.events if e.type == "attack_roll"]) == 1


# --- action_used_this_turn (UX affordance) ----------------------------------


def test_plain_move_leaves_action_used_this_turn_false() -> None:
    # Movement spends the movement budget, never the main action - mirrors
    # bonus_action_used's own "move doesn't end the turn" exception, but for
    # the opposite reason (nothing was spent at all, not a bonus action).
    state = _bonus_action_state()
    mira_pos = state.characters["mira"].position
    move_action = ParsedAction(
        actor="mira",
        verb="move",
        raw_text="I step forward",
        params={"path": [{"x": mira_pos.x + 1, "y": mira_pos.y}]},
    )
    resolve_action(state, move_action, _FixedRandom([]))  # type: ignore[arg-type]
    assert state.characters["mira"].action_used_this_turn is False
    assert state.turn_order[state.current_turn] == "mira"  # still her turn


def test_bonus_action_spell_leaves_action_used_this_turn_false_until_the_main_action() -> None:
    state = _bonus_action_state()
    state.characters["mira"].hp = 1
    state.characters["goblin_1"].position = state.characters["mira"].position
    heal_action = ParsedAction(
        actor="mira",
        verb="cast_spell",
        target="mira",
        item_or_spell="healing word",
        raw_text="I speak a word of healing over myself",
    )
    resolve_action(state, heal_action, _FixedRandom([3]))  # type: ignore[arg-type]
    assert state.characters["mira"].action_used_this_turn is False

    attack_action = ParsedAction(
        actor="mira", verb="attack", target="goblin_1", raw_text="I punch the goblin"
    )
    resolve_action(state, attack_action, _FixedRandom([15]))  # type: ignore[arg-type]
    assert state.characters["mira"].action_used_this_turn is True


def test_ordinary_action_spell_sets_action_used_this_turn_true() -> None:
    state = _bonus_action_state()
    state.characters["mira"].hp = 1
    action = ParsedAction(
        actor="mira",
        verb="cast_spell",
        target="mira",
        item_or_spell="cure wounds",
        raw_text="I cast cure wounds on myself",
    )
    resolve_action(state, action, _FixedRandom([6]))  # type: ignore[arg-type]
    assert state.characters["mira"].action_used_this_turn is True


def test_action_used_this_turn_resets_on_the_actors_own_next_turn() -> None:
    # Same "only when the turn actually advances TO this character" schedule
    # as bonus_action_used - poke it True directly (a real resolve_action
    # call earlier this test file already proves it gets set correctly) and
    # confirm a full round-trip through another actor's turn clears it.
    state = _bonus_action_state()
    state.characters["mira"].action_used_this_turn = True
    # The demo encounter's turn order is mira + 2 goblins - end every other
    # actor's turn in sequence until it's genuinely mira's turn again.
    while True:
        resolve_action(
            state,
            ParsedAction(
                actor=state.turn_order[state.current_turn], verb="end_turn", raw_text="..."
            ),
            _FixedRandom([]),  # type: ignore[arg-type]
        )
        if state.turn_order[state.current_turn] == "mira":
            break
    assert state.characters["mira"].action_used_this_turn is False
