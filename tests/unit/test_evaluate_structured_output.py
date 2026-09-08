"""Day 24: evaluate_structured_output is pure logic, no LLM involved -
exact-match scalars, order-independent list comparison, recursive descent
into nested objects. Exercised against a small nested toy schema, not
ParsedAction, to keep it honest about being generic."""

from __future__ import annotations

from pydantic import BaseModel

from src.training.evaluate_structured_output import (
    FieldAccuracy,
    score_dataset,
    score_fields,
    score_models,
)


class _Nested(BaseModel):
    skill: str
    dc: int


class _Toy(BaseModel):
    verb: str
    targets: list[str]
    params: _Nested


def test_field_accuracy_records_and_reports_per_field_and_overall() -> None:
    acc = FieldAccuracy()
    acc.record("verb", True)
    acc.record("verb", False)
    acc.record("dc", True)

    assert acc.accuracy("verb") == 0.5
    assert acc.accuracy("dc") == 1.0
    assert acc.accuracy("never_recorded") == 0.0
    assert acc.overall_accuracy() == 2 / 3


def test_field_accuracy_merge_combines_counts() -> None:
    a = FieldAccuracy()
    a.record("verb", True)
    b = FieldAccuracy()
    b.record("verb", False)
    b.record("dc", True)

    a.merge(b)

    assert a.total["verb"] == 2
    assert a.correct["verb"] == 1
    assert a.total["dc"] == 1


def test_score_fields_exact_match_for_scalars() -> None:
    expected = {"verb": "attack", "dc": 12}
    actual = {"verb": "attack", "dc": 13}

    acc = score_fields(expected, actual)

    assert acc.accuracy("verb") == 1.0
    assert acc.accuracy("dc") == 0.0
    assert acc.overall_accuracy() == 0.5


def test_score_fields_list_comparison_is_order_independent() -> None:
    expected = {"targets": ["goblin_1", "goblin_2"]}
    actual = {"targets": ["goblin_2", "goblin_1"]}

    acc = score_fields(expected, actual)

    assert acc.accuracy("targets") == 1.0


def test_score_fields_list_comparison_catches_a_real_mismatch() -> None:
    expected = {"targets": ["goblin_1", "goblin_2"]}
    actual = {"targets": ["goblin_1", "goblin_3"]}

    acc = score_fields(expected, actual)

    assert acc.accuracy("targets") == 0.0


def test_score_fields_recurses_into_nested_objects_with_dotted_paths() -> None:
    expected = {"verb": "skill_check", "params": {"skill": "stealth", "dc": 12}}
    actual = {"verb": "skill_check", "params": {"skill": "stealth", "dc": 15}}

    acc = score_fields(expected, actual)

    assert acc.accuracy("verb") == 1.0
    assert acc.accuracy("params.skill") == 1.0
    assert acc.accuracy("params.dc") == 0.0
    assert acc.overall_accuracy() == 2 / 3


def test_score_fields_missing_nested_object_scores_every_leaf_wrong() -> None:
    expected = {"verb": "skill_check", "params": {"skill": "stealth", "dc": 12}}
    actual = {"verb": "skill_check"}  # params missing entirely

    acc = score_fields(expected, actual)

    assert acc.accuracy("verb") == 1.0
    assert acc.accuracy("params.skill") == 0.0
    assert acc.accuracy("params.dc") == 0.0


def test_score_models_wraps_pydantic_instances() -> None:
    expected = _Toy(verb="attack", targets=["a"], params=_Nested(skill="athletics", dc=10))
    actual = _Toy(verb="attack", targets=["a"], params=_Nested(skill="athletics", dc=99))

    acc = score_models(expected, actual)

    assert acc.accuracy("verb") == 1.0
    assert acc.accuracy("targets") == 1.0
    assert acc.accuracy("params.dc") == 0.0


def test_score_dataset_aggregates_across_many_pairs() -> None:
    pairs = [
        (
            _Toy(verb="attack", targets=["a"], params=_Nested(skill="athletics", dc=10)),
            _Toy(verb="attack", targets=["a"], params=_Nested(skill="athletics", dc=10)),
        ),
        (
            _Toy(verb="attack", targets=["a"], params=_Nested(skill="athletics", dc=10)),
            _Toy(verb="dodge", targets=["b"], params=_Nested(skill="stealth", dc=1)),
        ),
    ]

    acc = score_dataset(pairs)

    assert acc.accuracy("verb") == 0.5
    assert acc.accuracy("targets") == 0.5
    assert acc.accuracy("params.skill") == 0.5
    assert acc.accuracy("params.dc") == 0.5
