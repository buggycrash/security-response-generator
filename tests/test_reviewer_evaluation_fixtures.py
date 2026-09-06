"""Integrity of the hand-authored reviewer-critique corpus.

The corpus is the ground truth of the whole command. An authoring slip -- a
marker that never appears, a variant identical to the clean draft -- would not
crash anything; it would silently produce a run whose numbers mean nothing.
These tests exist to make that class of error loud.
"""

import pytest

from security_response_generator.generation.prompt import RESPONSE_SCHEMA
from security_response_generator.model_evaluation import count_narrative_validations
from security_response_generator.reviewer_evaluation import (
    CLEAN_CONDITION,
    CONDITIONS,
    OMISSION_CONDITIONS,
    PROFILES,
)
from security_response_generator.reviewer_evaluation_scoring import normalize

CASES = PROFILES["smoke"].load_cases()
ITEMS = [(case, item) for case in CASES for item in case.drafts]


def _full_text(item):
    return normalize(item.response_text + " " + " ".join(item.draft.get("validations") or []))


def _clean(case):
    return next(item for item in case.drafts if item.condition == CLEAN_CONDITION)


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.id)
def test_every_case_carries_the_full_condition_taxonomy(case):
    assert {item.condition for item in case.drafts} == set(CONDITIONS)


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.id)
def test_conditions_are_not_duplicated(case):
    conditions = [item.condition for item in case.drafts]
    assert len(conditions) == len(set(conditions))


@pytest.mark.parametrize(
    ("case", "item"), ITEMS, ids=[f"{case.id}-{item.condition}" for case, item in ITEMS]
)
def test_every_draft_matches_the_generator_response_schema(case, item):
    assert set(item.draft) == set(RESPONSE_SCHEMA["required"])
    assert item.draft["needs_info"] is False
    assert item.draft["question"] is None
    assert item.draft["response"].strip()
    assert all(isinstance(entry, str) and entry.strip() for entry in item.draft["validations"])


@pytest.mark.parametrize(
    ("case", "item"), ITEMS, ids=[f"{case.id}-{item.condition}" for case, item in ITEMS]
)
def test_each_variant_actually_differs_from_the_clean_draft(case, item):
    if item.condition == CLEAN_CONDITION:
        return
    assert _full_text(item) != _full_text(_clean(case))


@pytest.mark.parametrize(
    ("case", "item"), ITEMS, ids=[f"{case.id}-{item.condition}" for case, item in ITEMS]
)
def test_omission_markers_are_absent_from_the_defective_draft(case, item):
    """The point of an omission variant is that the content is gone.

    If a marker still appeared in the draft, a reviewer could "detect" the
    defect by quoting text that was never removed.
    """
    if item.condition not in OMISSION_CONDITIONS:
        return
    text = _full_text(item)
    for group in item.detect_markers:
        assert not all(normalize(token) in text for token in group), (
            f"{case.id}/{item.condition}: marker group {group} survives in the draft"
        )


@pytest.mark.parametrize(
    ("case", "item"), ITEMS, ids=[f"{case.id}-{item.condition}" for case, item in ITEMS]
)
def test_omission_markers_describe_content_the_clean_draft_has(case, item):
    if item.condition not in OMISSION_CONDITIONS:
        return
    clean_text = _full_text(_clean(case))
    assert any(
        all(normalize(token) in clean_text for token in group) for group in item.detect_markers
    ), f"{case.id}/{item.condition}: no marker group appears in the clean draft"


@pytest.mark.parametrize(
    ("case", "item"), ITEMS, ids=[f"{case.id}-{item.condition}" for case, item in ITEMS]
)
def test_only_the_clean_condition_carries_must_not_flag(case, item):
    """must_not_flag is a restraint measure and is meaningless elsewhere.

    On a seeded draft a correct critique quotes the right value while naming
    the wrong one, which would score as a false alarm.
    """
    if item.condition == CLEAN_CONDITION:
        assert item.must_not_flag
    else:
        assert item.must_not_flag == []


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.id)
def test_must_not_flag_content_is_really_present_and_correct(case):
    text = _full_text(_clean(case))
    for fact in _clean(case).must_not_flag:
        assert normalize(fact) in text


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.id)
def test_clean_drafts_carry_no_detect_markers(case):
    assert _clean(case).detect_markers == []


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.id)
def test_must_keep_facts_survive_in_the_clean_draft(case):
    """Authored for a possible future effect measurement; unused today."""
    text = _full_text(_clean(case))
    for fact in case.must_keep:
        assert normalize(fact) in text


@pytest.mark.parametrize(
    ("case", "item"), ITEMS, ids=[f"{case.id}-{item.condition}" for case, item in ITEMS]
)
def test_only_the_narrative_validations_variant_leaves_a_heading_in_the_narrative(case, item):
    """Cross-checks the seeded defect against SRG's own structural detector.

    Uses the bold heading form rather than `[Validations]`, which
    `_response_sections` splits off before the detector ever sees it.
    """
    expected = 1 if item.condition == "narrative_validations" else 0
    assert count_narrative_validations(item.response_text) == expected


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.id)
def test_cases_supply_the_grounding_the_conditions_depend_on(case):
    assert case.context.strip()
    assert case.customer_chunks, "wrong_customer_parameter needs a customer standard"
    assert case.baseline_chunks, "omitted_control_clause needs baseline clauses"
    assert case.private_chunks, "unsupported_claim is judged against private context"
