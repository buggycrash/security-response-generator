"""Integrity checks for the atomic reviewer-classification corpus."""

import pytest

from security_response_generator.reviewer_evaluation import CLEAN_CONDITION, CONDITIONS, PROFILES
from security_response_generator.reviewer_evaluation_scoring import (
    EXPECTED_CLASSIFICATIONS,
    normalize,
)

CASES = PROFILES["smoke"].load_cases()
ITEMS = [(case, item) for case in CASES for item in case.drafts]


def _clean(case):
    return next(item for item in case.drafts if item.condition == CLEAN_CONDITION)


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.id)
def test_every_case_has_only_the_targeted_condition_set(case):
    assert {item.condition for item in case.drafts} == set(CONDITIONS)
    assert len(case.drafts) == 5


@pytest.mark.parametrize(
    ("case", "item"), ITEMS, ids=[f"{case.id}-{item.condition}" for case, item in ITEMS]
)
def test_every_mock_generated_draft_is_one_logical_sentence(case, item):
    statement = item.mock_generated_draft
    assert statement.strip()
    assert statement.count(".") == 1
    assert "\n" not in statement
    assert ", and " not in statement.casefold()


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.id)
def test_cases_supply_exactly_one_authoritative_requirement_sentence(case):
    assert case.grounding_information.strip()
    assert case.grounding_information.count(".") == 1
    assert "\n" not in case.grounding_information


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.id)
def test_clean_statement_is_a_nonverbatim_completion_paraphrase(case):
    clean = _clean(case).mock_generated_draft
    assert normalize(clean) != normalize(case.grounding_information)
    assert "complete" in normalize(clean)
    assert EXPECTED_CLASSIFICATIONS[CLEAN_CONDITION] == "supported"


@pytest.mark.parametrize(
    ("case", "item"), ITEMS, ids=[f"{case.id}-{item.condition}" for case, item in ITEMS]
)
def test_every_fixture_condition_has_a_frozen_classification(case, item):
    assert item.condition in EXPECTED_CLASSIFICATIONS
