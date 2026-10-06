"""Issue #99 (a self-cast with no target) and #107 (a charge whose move the
parser dropped): two deterministic post-passes in the intent parser. Pure and
offline; the real model's phrasing is live-verified separately."""

from __future__ import annotations

from src.engine.actions import ParsedAction
from src.engine.encounter import monster_to_character
from src.engine.position import BattleMap, Position
from src.engine.srd_loader import load_srd
from src.engine.state import Character, GameState
from src.graph.nodes.intent_parser import (
    _default_self_target,
    _insert_charge_move,
)


def _barbarian(weapon: str = "greataxe") -> Character:
    return Character(
        id="qaf",
        name="Qaf",
        race="Human",
        class_="Barbarian",
        class_index="barbarian",
        background="Acolyte",
        is_pc=True,
        hp=15,
        max_hp=15,
        ac=13,
        position=Position(x=0, y=0),
        stats={"STR": 16, "DEX": 12, "CON": 15, "INT": 8, "WIS": 10, "CHA": 8},
        speed=30,
        proficiency_bonus=2,
        equipped_weapons=[weapon],
    )


def _state(actor: Character, goblin_at: Position | None = None) -> GameState:
    goblin = monster_to_character(
        load_srd().monsters["goblin"], "goblin_3", goblin_at or Position(x=2, y=0)
    )
    return GameState(
        encounter_id="charge_test",
        characters={actor.id: actor, goblin.id: goblin},
        turn_order=[actor.id, goblin.id],
        current_turn=0,
        round=1,
        battle_map=BattleMap(
            width=10,
            height=10,
            terrain=[["floor"] * 10 for _ in range(10)],
            spawn_points={},
        ),
    )


def _rage() -> ParsedAction:
    return ParsedAction(actor="qaf", verb="rage", raw_text="x")


def _attack(weapon: str | None = "greataxe") -> ParsedAction:
    return ParsedAction(
        actor="qaf", verb="attack", target="goblin_3", item_or_spell=weapon, raw_text="x"
    )


SENTENCE = "I rage and charge the nearest goblin, then chop it with my greataxe"


def test_a_charge_with_the_move_dropped_gets_the_approach_inserted() -> None:
    state = _state(_barbarian())  # the goblin is 10ft away, the greataxe reaches 5ft
    out = _insert_charge_move([_rage(), _attack()], state, "qaf", SENTENCE)

    assert [a.verb for a in out] == ["rage", "move", "attack"]
    path = out[1].params["path"]
    assert path and path[-1] == {"x": 1, "y": 0}  # stops adjacent to the goblin


def test_nothing_is_inserted_when_the_sequence_already_moves() -> None:
    state = _state(_barbarian())
    move = ParsedAction(actor="qaf", verb="move", target="goblin_3", raw_text="x")
    actions = [_rage(), move, _attack()]
    assert _insert_charge_move(actions, state, "qaf", SENTENCE) == actions


def test_nothing_is_inserted_when_the_target_is_already_in_reach() -> None:
    state = _state(_barbarian(), goblin_at=Position(x=1, y=0))
    actions = [_rage(), _attack()]
    assert _insert_charge_move(actions, state, "qaf", SENTENCE) == actions


def test_nothing_is_inserted_without_charge_wording() -> None:
    # A plain attack at range is still rejected honestly, not silently a move.
    state = _state(_barbarian())
    actions = [_rage(), _attack()]
    assert _insert_charge_move(actions, state, "qaf", "I rage and chop the goblin") == actions


def test_a_ranged_weapon_does_not_close_in() -> None:
    archer = _barbarian("longbow")
    state = _state(archer)
    actions = [_attack("longbow")]
    assert _insert_charge_move(actions, state, "qaf", "I rush the goblin and shoot it") == actions


def _cast(target: str | None) -> ParsedAction:
    return ParsedAction(
        actor="qaf", verb="cast_spell", target=target, item_or_spell="cure wounds", raw_text="x"
    )


def test_a_self_cast_with_no_target_defaults_to_the_caster() -> None:
    state = _state(_barbarian())
    fixed = _default_self_target(_cast(None), state, "I cast cure wounds on myself")
    assert fixed.target == "qaf"
    assert _default_self_target(_cast(None), state, "cast cure wounds on me").target == "qaf"


def test_no_self_default_without_the_words_or_when_a_target_is_named() -> None:
    state = _state(_barbarian())
    assert _default_self_target(_cast(None), state, "I cast cure wounds").target is None
    assert _default_self_target(_cast(None), state, "let me cast sleep").target is None
    assert _default_self_target(_cast("goblin_3"), state, "on myself").target == "goblin_3"


def test_an_attack_with_no_target_defaults_to_the_nearest_enemy() -> None:
    from src.graph.nodes.intent_parser import _default_attack_target

    state = _state(_barbarian())
    bare = ParsedAction(actor="qaf", verb="attack", item_or_spell="greataxe", raw_text="x")
    assert _default_attack_target(bare, state).target == "goblin_3"
    named = _attack()
    assert _default_attack_target(named, state) == named  # a named target is untouched


def test_no_default_attack_target_when_there_is_no_enemy() -> None:
    from src.graph.nodes.intent_parser import _default_attack_target

    barb = _barbarian()
    lone = GameState(
        encounter_id="lone",
        characters={barb.id: barb},
        turn_order=[barb.id],
        current_turn=0,
        round=1,
    )
    bare = ParsedAction(actor="qaf", verb="attack", raw_text="x")
    assert _default_attack_target(bare, lone).target is None


def _rage_only() -> list[ParsedAction]:
    return [_rage()]


def test_a_follow_up_attack_the_parser_dropped_is_appended() -> None:
    from src.graph.nodes.intent_parser import _append_dropped_attack

    state = _state(_barbarian())
    out = _append_dropped_attack(_rage_only(), state, "qaf", "I rage and bash the goblin")
    assert [a.verb for a in out] == ["rage", "attack"]
    assert out[1].target == "goblin_3"
    out = _append_dropped_attack(_rage_only(), state, "qaf", "I rage, then I smash it")
    assert [a.verb for a in out] == ["rage", "attack"]


def test_no_attack_is_appended_without_the_first_person_follow_up() -> None:
    from src.graph.nodes.intent_parser import _append_dropped_attack

    state = _state(_barbarian())
    for text in ["I rage", "I rage so my friend can hit it", "I rage and cheer loudly"]:
        assert _append_dropped_attack(_rage_only(), state, "qaf", text) == _rage_only()


def test_no_attack_is_appended_when_the_sequence_already_uses_the_action() -> None:
    from src.graph.nodes.intent_parser import _append_dropped_attack

    state = _state(_barbarian())
    actions = [_rage(), _attack()]
    assert _append_dropped_attack(actions, state, "qaf", "I rage and bash the goblin") == actions
