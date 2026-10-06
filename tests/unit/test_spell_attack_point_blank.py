"""Issue #80 (class playtest): a ranged *spell* attack made with a hostile
creature within 5 feet has disadvantage, exactly like a ranged weapon attack -
`_cast_attack_spell_at_target` never applied it, so every cornered caster's
Fire Bolt / Eldritch Blast rolled straight."""

from src.cli.play import build_demo_encounter
from src.engine.actions import ParsedAction
from src.engine.character_creation import create_character
from src.engine.encounter import build_encounter_state
from src.engine.position import Position
from src.engine.rules import spell_attack_is_ranged
from src.engine.srd_loader import load_srd
from src.engine.state import Character, GameState
from src.engine.turn_engine import resolve_action


class _FixedRandom:
    def __init__(self, values: list[int]) -> None:
        self._values = list(values)

    def randint(self, a: int, b: int) -> int:
        return self._values.pop(0)


def _wizard() -> Character:
    return create_character(
        character_id="elrond",
        name="Elrond",
        race_index="elf",
        class_index="wizard",
        background_index="acolyte",
        base_ability_scores={"STR": 8, "DEX": 14, "CON": 12, "INT": 15, "WIS": 13, "CHA": 10},
        chosen_skills=["skill-arcana", "skill-history"],
        chosen_prepared_spells=["magic-missile", "burning-hands", "mage-armor"],
        chosen_equipment=["dagger"],
    )


def _state(elrond_at: Position | None) -> GameState:
    fighter = create_character(
        character_id="thorin",
        name="Thorin",
        race_index="human",
        class_index="fighter",
        background_index="acolyte",
        base_ability_scores={"STR": 15, "DEX": 14, "CON": 13, "INT": 12, "WIS": 10, "CHA": 8},
        chosen_skills=["skill-athletics", "skill-perception"],
        chosen_equipment=["longsword"],
    )
    state = build_encounter_state(
        build_demo_encounter(),
        [fighter, _wizard()],
        _FixedRandom([18, 10, 8, 3]),  # type: ignore[arg-type]
    )
    if elrond_at is not None:
        state.characters["elrond"].position = elrond_at
    state.current_turn = state.turn_order.index("elrond")
    return state


def _cast(state: GameState, spell: str, rng: list[int]) -> dict[str, object]:
    resolve_action(
        state,
        ParsedAction(
            actor="elrond",
            verb="cast_spell",
            target="goblin_1",
            item_or_spell=spell,
            raw_text="x",
        ),
        _FixedRandom(rng),  # type: ignore[arg-type]
    )
    return next(e for e in state.events if e.type == "spell_cast").payload


def test_ranged_spell_attack_at_point_blank_rolls_with_disadvantage() -> None:
    # Elrond (1,2) is 5ft from goblin_1 (2,1). Attack bonus 2 + 2 = 4.
    # d20s [18, 3] with disadvantage keep the 3 -> total 7 vs AC 15: a miss.
    state = _state(None)
    payload = _cast(state, "fire bolt", [18, 3])
    assert payload["roll_total"] == 7
    assert payload["hit"] is False


def test_ranged_spell_attack_with_no_hostile_adjacent_rolls_straight() -> None:
    # Moved to (0,0): goblin_1 (2,1) is 10ft away, goblin_2 (2,2) 10ft - no
    # hostile within 5ft, so one d20 (18 -> 22, hit) then the 1d10 damage die.
    state = _state(Position(x=0, y=0))
    payload = _cast(state, "fire bolt", [18, 6])
    assert payload["roll_total"] == 22
    assert payload["hit"] is True


def test_melee_spell_attack_is_not_penalised_for_an_adjacent_hostile() -> None:
    # Shocking Grasp is a *melee* spell attack - being in melee is the point.
    state = _state(None)
    payload = _cast(state, "shocking grasp", [18, 5])
    assert payload["roll_total"] == 22
    assert payload["hit"] is True


def test_spell_attack_is_ranged_classification() -> None:
    srd = load_srd()
    assert spell_attack_is_ranged(srd.spells["fire-bolt"])
    assert spell_attack_is_ranged(srd.spells["eldritch-blast"])
    assert spell_attack_is_ranged(
        srd.spells["scorching-ray"]
    )  # attack_type missing in the SRD data
    assert not spell_attack_is_ranged(srd.spells["shocking-grasp"])
    assert not spell_attack_is_ranged(srd.spells["inflict-wounds"])
    assert not spell_attack_is_ranged(srd.spells["flame-blade"])
