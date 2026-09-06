import pytest

from src.cli.play import build_demo_encounter
from src.engine.actions import ParsedAction
from src.engine.character_creation import create_character
from src.engine.encounter import build_encounter_state, monster_to_character
from src.engine.position import Position
from src.engine.srd_loader import load_srd
from src.engine.state import Character
from src.engine.turn_engine import (
    TurnEngineError,
    _monster_attack_params,
    _pc_attack_params,
    parse_dice_notation,
    resolve_action,
)


class _FixedRandom:
    def __init__(self, values: list[int]) -> None:
        self._values = list(values)

    def randint(self, a: int, b: int) -> int:
        return self._values.pop(0)


def _two_person_party() -> list[Character]:
    thorin = create_character(
        character_id="thorin",
        name="Thorin",
        race_index="human",
        class_index="fighter",
        background_index="acolyte",
        base_ability_scores={"STR": 15, "DEX": 14, "CON": 13, "INT": 12, "WIS": 10, "CHA": 8},
        chosen_skills=["skill-athletics", "skill-perception"],
        chosen_equipment=["longsword"],
    )
    elrond = create_character(
        character_id="elrond",
        name="Elrond",
        race_index="elf",
        class_index="wizard",
        background_index="acolyte",
        base_ability_scores={"STR": 8, "DEX": 14, "CON": 12, "INT": 15, "WIS": 13, "CHA": 10},
        chosen_skills=["skill-arcana", "skill-history"],
        chosen_equipment=["dagger"],
    )
    return [thorin, elrond]


def test_parse_dice_notation_variants() -> None:
    assert parse_dice_notation("1d6+2") == (1, 6, 2)
    assert parse_dice_notation("2d8") == (2, 8, 0)
    assert parse_dice_notation("1d4-1") == (1, 4, -1)


def test_parse_dice_notation_rejects_garbage() -> None:
    with pytest.raises(TurnEngineError):
        parse_dice_notation("not dice")


def test_pc_attack_params_non_finesse_melee_uses_strength() -> None:
    srd = load_srd()
    fighter = _two_person_party()[0]
    # STR16 -> mod3, proficiency_bonus2 -> attack_bonus 5; damage 1d8, bonus = STR mod 3
    params = _pc_attack_params(fighter, "longsword", srd)
    assert params.attack_bonus == 5
    assert (params.damage_dice_count, params.damage_dice_sides, params.damage_bonus) == (1, 8, 3)
    assert params.damage_type == "slashing"


def test_pc_attack_params_finesse_uses_better_of_str_or_dex() -> None:
    srd = load_srd()
    wizard = _two_person_party()[1]
    # DEX16 -> mod3 beats STR8 -> mod-1; attack_bonus = 3+2=5, damage bonus = 3
    params = _pc_attack_params(wizard, "dagger", srd)
    assert params.attack_bonus == 5
    assert params.damage_bonus == 3
    assert params.damage_type == "piercing"


def test_pc_attack_params_falls_back_to_unarmed_strike() -> None:
    srd = load_srd()
    fighter = _two_person_party()[0]
    fighter.inventory = []  # strip the longsword to force the unarmed path
    params = _pc_attack_params(fighter, None, srd)
    assert params.source_name == "unarmed strike"
    assert params.damage_dice_count == 0
    assert params.damage_type == "bludgeoning"


def test_monster_attack_params_defaults_to_first_action() -> None:
    srd = load_srd()
    goblin = monster_to_character(srd.monsters["goblin"], "goblin_1", Position(x=0, y=0))
    params = _monster_attack_params(goblin, None, srd)
    assert params.source_name == "Scimitar"
    assert params.attack_bonus == 4
    assert (params.damage_dice_count, params.damage_dice_sides, params.damage_bonus) == (1, 6, 2)


def test_monster_attack_params_can_select_named_action() -> None:
    srd = load_srd()
    goblin = monster_to_character(srd.monsters["goblin"], "goblin_1", Position(x=0, y=0))
    params = _monster_attack_params(goblin, "Shortbow", srd)
    assert params.source_name == "Shortbow"
    assert params.damage_type == "piercing"


def test_monster_attack_params_rejects_unknown_action_name() -> None:
    srd = load_srd()
    goblin = monster_to_character(srd.monsters["goblin"], "goblin_1", Position(x=0, y=0))
    with pytest.raises(TurnEngineError):
        _monster_attack_params(goblin, "Fireball", srd)


def _build_demo_state(rng_values: list[int]):  # type: ignore[no-untyped-def]
    encounter = build_demo_encounter()
    party = _two_person_party()
    return build_encounter_state(encounter, party, _FixedRandom(rng_values))  # type: ignore[arg-type]


def test_resolve_action_rejects_action_from_the_wrong_actor() -> None:
    state = _build_demo_state([18, 10, 8, 3])
    off_turn_actor = state.turn_order[1]
    action = ParsedAction(actor=off_turn_actor, verb="end_turn", raw_text="out of turn")
    with pytest.raises(TurnEngineError):
        resolve_action(state, action, _FixedRandom([]))  # type: ignore[arg-type]


def test_resolve_action_rejects_a_verb_outside_the_known_set() -> None:
    # Every verb in ParsedAction's Literal is dispatched as of Day 14, so
    # this fallback is only reachable by bypassing pydantic validation
    # (model_construct) - still worth a test as a safety net for whenever a
    # future verb is added to the schema before the engine handles it.
    state = _build_demo_state([18, 10, 8, 3])
    current_actor = state.turn_order[0]
    action = ParsedAction.model_construct(actor=current_actor, verb="teleport", raw_text="?")
    with pytest.raises(NotImplementedError):
        resolve_action(state, action, _FixedRandom([]))  # type: ignore[arg-type]


def test_resolve_action_invalid_does_not_advance_the_turn() -> None:
    # The DM not understanding an action isn't a system error and doesn't
    # cost the actor their turn - they can just try again.
    state = _build_demo_state([18, 10, 8, 3])
    current_actor = state.turn_order[0]
    action = ParsedAction(actor=current_actor, verb="invalid", raw_text="gibberish")
    resolve_action(state, action, _FixedRandom([]))  # type: ignore[arg-type]

    assert state.turn_order[state.current_turn] == current_actor
    assert state.events[-1].type == "action_invalid"
    assert state.events[-1].payload["raw_text"] == "gibberish"


def test_resolve_action_rejects_a_downed_actor() -> None:
    state = _build_demo_state([18, 10, 8, 3])
    current_actor_id = state.turn_order[0]
    state.characters[current_actor_id].hp = 0
    action = ParsedAction(actor=current_actor_id, verb="end_turn", raw_text="downed")
    with pytest.raises(TurnEngineError):
        resolve_action(state, action, _FixedRandom([]))  # type: ignore[arg-type]


def test_resolve_action_rejects_a_pc_attacking_another_pc() -> None:
    # Regression guard: found live on Day 15's first autoplay run, a
    # companion's free-text turn named an ally as its attack target.
    state = _build_demo_state([18, 10, 8, 3])
    assert state.turn_order[0] == "thorin"
    action = ParsedAction(
        actor="thorin", verb="attack", target="elrond", raw_text="I attack Elrond"
    )
    with pytest.raises(TurnEngineError, match="same side"):
        resolve_action(state, action, _FixedRandom([]))  # type: ignore[arg-type]


def test_resolve_action_rejects_a_monster_attacking_another_monster() -> None:
    state = _build_demo_state([18, 10, 8, 3])
    state.current_turn = state.turn_order.index("goblin_1")
    action = ParsedAction(
        actor="goblin_1", verb="attack", target="goblin_2", raw_text="the goblin attacks its ally"
    )
    with pytest.raises(TurnEngineError, match="same side"):
        resolve_action(state, action, _FixedRandom([]))  # type: ignore[arg-type]
