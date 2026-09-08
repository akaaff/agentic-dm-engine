"""Generic field-level accuracy scoring for structured output (Day 24) -
works against any pydantic model's dumped dict, not just ParsedAction:
exact match for scalars/literals, order-independent (set/multiset) match
for lists, recursive descent into nested objects. Field paths are dotted
(e.g. "params.skill") so a mistake inside a nested object is attributed to
the specific field, not just "the object was wrong."
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel


@dataclass
class FieldAccuracy:
    correct: dict[str, int] = field(default_factory=dict)
    total: dict[str, int] = field(default_factory=dict)

    def record(self, path: str, is_correct: bool) -> None:
        self.total[path] = self.total.get(path, 0) + 1
        if is_correct:
            self.correct[path] = self.correct.get(path, 0) + 1

    def accuracy(self, path: str) -> float:
        total = self.total.get(path, 0)
        return self.correct.get(path, 0) / total if total else 0.0

    def overall_accuracy(self) -> float:
        total = sum(self.total.values())
        correct = sum(self.correct.values())
        return correct / total if total else 0.0

    def merge(self, other: FieldAccuracy) -> None:
        for path, count in other.total.items():
            self.total[path] = self.total.get(path, 0) + count
        for path, count in other.correct.items():
            self.correct[path] = self.correct.get(path, 0) + count


def _values_match(expected: Any, actual: Any) -> bool:
    if isinstance(expected, list) and isinstance(actual, list):
        try:
            return set(expected) == set(actual)
        except TypeError:
            # Unhashable elements (e.g. nested dicts) - order-independent
            # multiset comparison via repeated membership/removal instead.
            remaining = list(actual)
            for item in expected:
                if item not in remaining:
                    return False
                remaining.remove(item)
            return not remaining
    return bool(expected == actual)


def score_fields(
    expected: dict[str, Any], actual: dict[str, Any], prefix: str = ""
) -> FieldAccuracy:
    """The recursive core - operates on plain dicts (e.g. from
    BaseModel.model_dump()) so it has no pydantic-specific behavior to keep
    in sync with any particular schema."""
    result = FieldAccuracy()
    for key, expected_value in expected.items():
        path = f"{prefix}{key}"
        if isinstance(expected_value, dict):
            actual_nested = actual.get(key) if isinstance(actual, dict) else None
            # A missing/wrong-typed nested object scores every leaf beneath
            # it as wrong (comparing against {}), rather than one flat miss -
            # keeps field-level accuracy meaningful even for a badly-shaped response.
            result.merge(score_fields(expected_value, actual_nested or {}, prefix=f"{path}."))
        else:
            actual_value = actual.get(key) if isinstance(actual, dict) else None
            result.record(path, _values_match(expected_value, actual_value))
    return result


def score_models(expected: BaseModel, actual: BaseModel) -> FieldAccuracy:
    """Convenience wrapper for the common case of comparing two instances of
    the same pydantic schema (e.g. a test split's known-correct label vs. a
    model's prediction for the same input)."""
    return score_fields(expected.model_dump(mode="json"), actual.model_dump(mode="json"))


def score_dataset(pairs: Sequence[tuple[BaseModel, BaseModel]]) -> FieldAccuracy:
    """`pairs` is (expected, actual) model instances for every example in a
    held-out split - the usual "base vs. finetuned vs. teacher" comparison
    this toolkit exists to produce (Day 26) just calls this three times,
    once per candidate model's predictions."""
    result = FieldAccuracy()
    for expected, actual in pairs:
        result.merge(score_models(expected, actual))
    return result
