"""Pure deterministic scoring for reviewer classification."""

import json

import pytest

from security_response_generator.reviewer_evaluation_scoring import (
    CLASSIFICATIONS,
    EXPECTED_CLASSIFICATIONS,
    critique_distinctness,
    normalize,
    parse_failed,
    parse_review,
)


def _reply(classification="supported", feedback=None):
    payload = {"classification": classification}
    if feedback is not None:
        payload["constructive_feedback"] = feedback
    return json.dumps(payload)


def test_schema_valid_classification_is_parsed():
    parsed = parse_review(_reply("contradicts_requirement", "The deadline conflicts."))
    assert parsed is not None
    assert parsed.classification == "contradicts_requirement"
    assert parsed.constructive_feedback == "The deadline conflicts."


def test_constructive_feedback_is_optional():
    parsed = parse_review(_reply("supported"))
    assert parsed is not None
    assert parsed.constructive_feedback == ""


@pytest.mark.parametrize(
    "raw",
    [
        "not json",
        "{}",
        '{"classification":"maybe"}',
        '{"classification":"supported","constructive_feedback":[]}',
        '{"classification":"supported","extra":true}',
        "[]",
    ],
)
def test_invalid_classification_is_rejected(raw):
    assert parse_review(raw) is None
    assert parse_failed(raw)


def test_four_classifications_cover_the_five_fixture_conditions():
    assert set(CLASSIFICATIONS) == {
        "supported",
        "missing_required_information",
        "contradicts_requirement",
        "adds_unsupported_information",
    }
    assert EXPECTED_CLASSIFICATIONS == {
        "clean": "supported",
        "missing_required_information": "missing_required_information",
        "incorrect_required_information": "contradicts_requirement",
        "unsupported_claim": "adds_unsupported_information",
        "wrong_control_content": "adds_unsupported_information",
    }


def test_normalize_remains_available_for_fixture_integrity_checks():
    assert normalize("  Within  24 HOURS ") == "within 24 hours"


def test_identical_classifications_score_zero_distinctness():
    assert critique_distinctness(["supported"] * 4) == pytest.approx(0.0)


def test_different_classifications_are_distinct():
    assert critique_distinctness(["supported", "contradicts_requirement"]) == pytest.approx(1.0)


def test_feedback_cannot_affect_distinctness_because_only_classifications_are_passed():
    decisions = [
        parse_review(_reply("supported", "First explanation.")),
        parse_review(_reply("supported", "Completely different explanation.")),
    ]
    assert all(decisions)
    assert critique_distinctness([decision.classification for decision in decisions]) == 0.0
