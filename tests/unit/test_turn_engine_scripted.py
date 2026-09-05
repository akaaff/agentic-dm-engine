"""The Day 7 MVP gate: runs the scripted demo skirmish (src/cli/play.py)
end-to-end with zero LLM calls, and asserts the final state against a
hand-computed fixture. Full hand trace (dex mods, attack bonuses, damage) is
documented in play.py's SCRIPTED_ACTIONS/demo_action_rng docstrings.
"""

from src.cli.play import run_scripted
from src.engine.position import Position


def test_scripted_skirmish_reaches_hand_computed_victory_state() -> None:
    state = run_scripted(verbose=False)

    assert state.status == "victory"
    assert state.round == 3

    assert state.characters["thorin"].hp == 6
    assert state.characters["thorin"].position == Position(x=1, y=1)
    assert state.characters["elrond"].hp == 2
    assert state.characters["elrond"].position == Position(x=1, y=2)
    assert state.characters["goblin_1"].hp == 0
    assert state.characters["goblin_2"].hp == 0


def test_scripted_skirmish_event_log_matches_hand_trace() -> None:
    state = run_scripted(verbose=False)

    event_summary = [(e.round, e.turn_index, e.actor, e.type) for e in state.events]

    assert event_summary == [
        (1, 0, "thorin", "move"),
        (1, 1, "elrond", "attack_roll"),
        (1, 1, "elrond", "damage_dealt"),
        (1, 2, "goblin_1", "attack_roll"),
        (1, 2, "goblin_1", "damage_dealt"),
        (1, 3, "goblin_2", "attack_roll"),  # miss - no damage_dealt event
        (2, 0, "thorin", "attack_roll"),
        (2, 0, "thorin", "damage_dealt"),
        (2, 0, "goblin_1", "death"),
        (2, 1, "elrond", "attack_roll"),  # miss - no damage_dealt event
        # goblin_1's round-2 turn (turn_index 2) is absent: skipped, already dead
        (2, 3, "goblin_2", "attack_roll"),
        (2, 3, "goblin_2", "damage_dealt"),
        (3, 0, "thorin", "attack_roll"),
        (3, 0, "thorin", "damage_dealt"),
        (3, 0, "goblin_2", "death"),
    ]


def test_dead_actor_never_gets_prompted_for_its_own_turn() -> None:
    state = run_scripted(verbose=False)
    # goblin_1's own "death" event legitimately has actor=goblin_1 (it records
    # who died, not who acted) even though it fires on Thorin's turn - so this
    # checks specifically for attack_roll events (goblin_1 *acting*), not any
    # event mentioning it.
    goblin_1_attacks = [
        e for e in state.events if e.actor == "goblin_1" and e.type == "attack_roll"
    ]
    assert len(goblin_1_attacks) == 1
    assert goblin_1_attacks[0].round == 1  # its only turn, before it died in round 2
