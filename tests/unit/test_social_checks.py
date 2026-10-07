"""Issue #102: Persuasion / Intimidation / Deception aimed at an enemy are contested by
its passive Insight (10 + WIS mod, a goblin's is 9) instead of the flat default DC, and a
success has a one-round effect: frightened, charmed or distracted. The speaker is a
human barbarian with Intimidation, Persuasion and Deception proficiency and CHA 10
(+0), so the check modifier is +2."""

from __future__ import annotations

import pytest

from src.engine.actions import ParsedAction
from src.engine.character_creation import create_character
from src.engine.conditions import has_condition
from src.engine.encounter import monster_to_character
from src.engine.position import BattleMap, Position
from src.engine.rules import passive_insight
from src.engine.srd_loader import load_srd
from src.engine.state import Character, GameState
from src.engine.turn_engine import DEFAULT_SKILL_CHECK_DC, TurnEngineError, resolve_action


class _FixedRandom:
    def __init__(self, values: list[int]) -> None:
        self._values = list(values)

    def randint(self, a: int, b: int) -> int:
        return self._values.pop(0)


def _speaker() -> Character:
    barbarian = create_character(
        character_id="grunna",
        name="Grunna",
        race_index="human",
        class_index="barbarian",
        background_index="acolyte",
        base_ability_scores={"STR": 15, "DEX": 13, "CON": 14, "INT": 8, "WIS": 12, "CHA": 10},
        chosen_skills=["skill-athletics", "skill-intimidation"],
        position=Position(x=0, y=0),
    )
    # CHA 10 + the human +1 = 11 (+0); give her the other two social skills too.
    barbarian.skill_proficiencies += ["skill-persuasion", "skill-deception"]
    return barbarian


def _monster(index: str, char_id: str = "goblin_1", x: int = 2) -> Character:
    monster = monster_to_character(load_srd().monsters[index], char_id, Position(x=x, y=0))
    monster.hp = monster.max_hp = 100
    return monster


def _state(speaker: Character, *others: Character) -> GameState:
    chars = {speaker.id: speaker, **{c.id: c for c in others}}
    return GameState(
        encounter_id="social_test",
        characters=chars,
        turn_order=list(chars),
        current_turn=0,
        round=1,
        battle_map=BattleMap(
            width=10, height=10, terrain=[["floor"] * 10 for _ in range(10)], spawn_points={}
        ),
    )


def _check(skill: str, target: str | None = "goblin_1") -> ParsedAction:
    return ParsedAction(
        actor="grunna",
        verb="skill_check",
        target=target,
        params={"skill": skill},
        raw_text=f"I use {skill}",
    )


def test_a_goblins_passive_insight_is_nine() -> None:
    assert passive_insight(_monster("goblin"), load_srd()) == 9  # 10 + WIS 8 (-1)


def test_a_pcs_passive_insight_adds_proficiency() -> None:
    srd = load_srd()
    speaker = _speaker()  # WIS 12 + human 1 = 13 (+1); the Acolyte background trains Insight
    assert "skill-insight" in speaker.skill_proficiencies
    assert passive_insight(speaker, srd) == 13  # 10 + 1 + 2
    speaker.skill_proficiencies.remove("skill-insight")
    assert passive_insight(speaker, srd) == 11


def test_the_check_is_rolled_against_the_targets_passive_insight() -> None:
    speaker = _speaker()
    state = _state(speaker, _monster("goblin"))
    resolve_action(state, _check("intimidation"), _FixedRandom([7]))  # type: ignore[arg-type]
    event = next(e for e in state.events if e.type == "skill_check")
    assert event.payload["dc"] == 9
    assert event.payload["target"] == "goblin_1"
    assert event.payload["roll_total"] == 9  # 7 + 2: exactly the DC
    assert event.payload["success"] is True


@pytest.mark.parametrize(
    ("skill", "condition"),
    [("intimidation", "frightened"), ("persuasion", "charmed"), ("deception", "distracted")],
)
def test_a_success_applies_a_one_round_condition(skill: str, condition: str) -> None:
    speaker = _speaker()
    goblin = _monster("goblin")
    state = _state(speaker, goblin)
    resolve_action(state, _check(skill), _FixedRandom([15]))  # type: ignore[arg-type]
    assert has_condition(goblin, condition)  # type: ignore[arg-type]
    applied = next(e for e in state.events if e.type == "condition_applied")
    assert applied.payload["condition"] == condition
    assert goblin.conditions[0].duration_rounds == 1
    assert goblin.conditions[0].source == "grunna"


def test_a_failure_does_nothing() -> None:
    speaker = _speaker()
    goblin = _monster("goblin")
    state = _state(speaker, goblin)
    resolve_action(state, _check("intimidation"), _FixedRandom([2]))  # type: ignore[arg-type]
    assert goblin.conditions == []


def test_a_frightened_target_attacks_with_disadvantage() -> None:
    speaker = _speaker()
    goblin = _monster("goblin", x=1)
    state = _state(speaker, goblin)
    resolve_action(state, _check("intimidation"), _FixedRandom([15]))  # type: ignore[arg-type]
    attack = ParsedAction(
        actor="goblin_1",
        verb="attack",
        target="grunna",
        item_or_spell="Scimitar",
        raw_text="the goblin lunges",
    )
    resolve_action(state, attack, _FixedRandom([18, 3]))  # type: ignore[arg-type]
    assert next(e for e in state.events if e.type == "attack_roll").payload["natural"] == 3


def test_a_charmed_target_cannot_attack_the_speaker() -> None:
    speaker = _speaker()
    goblin = _monster("goblin", x=1)
    state = _state(speaker, goblin)
    resolve_action(state, _check("persuasion"), _FixedRandom([15]))  # type: ignore[arg-type]
    attack = ParsedAction(
        actor="goblin_1", verb="attack", target="grunna", item_or_spell="Scimitar", raw_text="x"
    )
    with pytest.raises(TurnEngineError, match="charmed"):
        resolve_action(state, attack, _FixedRandom([]))  # type: ignore[arg-type]


def test_a_distracted_target_loses_the_condition_with_its_next_attack() -> None:
    speaker = _speaker()
    goblin = _monster("goblin", x=1)
    state = _state(speaker, goblin)
    resolve_action(state, _check("deception"), _FixedRandom([15]))  # type: ignore[arg-type]
    attack = ParsedAction(
        actor="goblin_1", verb="attack", target="grunna", item_or_spell="Scimitar", raw_text="x"
    )
    resolve_action(state, attack, _FixedRandom([18, 3]))  # type: ignore[arg-type]
    assert not has_condition(goblin, "distracted")


def test_a_creature_immune_to_the_condition_is_unaffected() -> None:
    speaker = _speaker()
    ghoul = _monster("ghoul", "ghoul_1")  # immune to charm
    state = _state(speaker, ghoul)
    resolve_action(state, _check("persuasion", "ghoul_1"), _FixedRandom([20]))  # type: ignore[arg-type]
    assert next(e for e in state.events if e.type == "skill_check").payload["success"] is True
    assert ghoul.conditions == []


def test_other_checks_keep_the_flat_dc_and_have_no_effect() -> None:
    speaker = _speaker()
    goblin = _monster("goblin")
    state = _state(speaker, goblin)
    resolve_action(state, _check("athletics", "goblin_1"), _FixedRandom([20]))  # type: ignore[arg-type]
    assert next(e for e in state.events if e.type == "skill_check").payload["dc"] == (
        DEFAULT_SKILL_CHECK_DC
    )
    assert goblin.conditions == []


def test_a_social_check_with_no_enemy_target_keeps_the_flat_dc() -> None:
    speaker = _speaker()
    state = _state(speaker, _monster("goblin"))
    resolve_action(state, _check("persuasion", None), _FixedRandom([20]))  # type: ignore[arg-type]
    event = next(e for e in state.events if e.type == "skill_check")
    assert event.payload["dc"] == DEFAULT_SKILL_CHECK_DC
    assert "target" not in event.payload


def test_a_social_check_with_no_target_aims_at_the_enemy_the_words_name() -> None:
    from src.graph.nodes.intent_parser import _default_social_target

    speaker = _speaker()
    state = _state(speaker, _monster("goblin", "goblin_1", 2), _monster("wolf", "wolf_1", 1))
    bare = ParsedAction(
        actor="grunna", verb="skill_check", params={"skill": "intimidation"}, raw_text="x"
    )
    fixed = _default_social_target(bare, state, "I roar at the goblin to intimidate it")
    assert fixed.target == "goblin_1"  # named, even though the wolf is nearer
    # Two enemies and no hint: left untargeted. One enemy: that one.
    assert _default_social_target(bare, state, "I try to scare them").target is None
    lone = _state(speaker, _monster("goblin"))
    assert _default_social_target(bare, lone, "I try to scare them").target == "goblin_1"


def test_a_non_social_check_never_gets_a_default_target() -> None:
    from src.graph.nodes.intent_parser import _default_social_target

    state = _state(_speaker(), _monster("goblin"))
    check = ParsedAction(
        actor="grunna", verb="skill_check", params={"skill": "athletics"}, raw_text="x"
    )
    assert _default_social_target(check, state, "I climb the wall").target is None
