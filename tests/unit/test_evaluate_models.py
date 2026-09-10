"""Day 26: evaluate_models' actual model runs need a GPU + Ollama, covered
by manual live verification (the saved eval table). This tests the pure
logic: pulling JSON out of a raw model completion, and scoring/formatting
predictions against expected labels."""

from __future__ import annotations

from src.training.evaluate_models import (
    _extract_json,
    score_predictions,
    scores_to_markdown,
)


def test_extract_json_parses_a_bare_object() -> None:
    assert _extract_json('{"verb": "attack", "target": "goblin_1"}') == {
        "verb": "attack",
        "target": "goblin_1",
    }


def test_extract_json_pulls_the_object_out_of_surrounding_prose() -> None:
    text = 'Sure! Here is the action:\n```json\n{"verb": "dodge"}\n```\nHope that helps.'
    assert _extract_json(text) == {"verb": "dodge"}


def test_extract_json_returns_none_when_there_is_no_object() -> None:
    assert _extract_json("I am not sure what to do here.") is None


def test_score_predictions_counts_valid_json_and_valid_action_rates() -> None:
    expected = [
        {"actor": "thorin", "verb": "attack", "target": "goblin_1", "raw_text": "hit it"},
        {"actor": "thorin", "verb": "dodge", "raw_text": "duck"},
        {"actor": "thorin", "verb": "move", "raw_text": "go"},
    ]
    predicted = [
        # valid JSON, valid ParsedAction, correct verb + target
        {"actor": "thorin", "verb": "attack", "target": "goblin_1", "raw_text": "hit it"},
        # valid JSON but NOT a valid ParsedAction (missing required raw_text), wrong verb
        {"actor": "thorin", "verb": "attack"},
        # unparseable
        None,
    ]

    score = score_predictions("student", expected, predicted)

    assert score.valid_json_rate == 2 / 3
    assert score.valid_action_rate == 1 / 3
    # verb is in all 3 expected labels: row 1 matches, row 2 is wrong (attack vs
    # dodge), row 3's prediction is None so every field scores wrong -> 1/3.
    assert score.field_accuracy.accuracy("verb") == 1 / 3
    # target is only in row 1's expected label (score_fields scores the fields
    # the ground truth actually has), and row 1's prediction matched it.
    assert score.field_accuracy.accuracy("target") == 1.0


def test_scores_to_markdown_has_a_row_per_model_and_a_column_per_field() -> None:
    expected = [{"verb": "attack", "target": "g1"}]
    scores = [
        score_predictions("base", expected, [None]),
        score_predictions("fine-tuned", expected, [{"verb": "attack", "target": "g1"}]),
    ]

    md = scores_to_markdown(scores, n_examples=1)

    assert "| base |" in md
    assert "| fine-tuned |" in md
    assert "verb" in md and "target" in md
    assert "100.0%" in md  # fine-tuned got everything right on the single example
