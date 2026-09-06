"""Deterministic scoring for `srg evaluate-reviewer`.

Every check maps to a clause of REVIEW_SYSTEM_INSTRUCTION, so these tests
double as the executable statement of SRG's reviewer contract.
"""

import pytest

from security_response_generator.reviewer_evaluation_scoring import (
    DIRECTIVE_IMPERATIVE,
    DIRECTIVE_MODAL,
    REWRITE_CHAR_THRESHOLD,
    containment_violations,
    critique_distinctness,
    detects,
    extract_directives,
    false_alarms,
    foreign_control_ids,
    parse_failed,
    reports_no_issues,
    suggests_other_control,
)


def _texts(critique):
    return [directive.text for directive in extract_directives(critique)]


# --- Directive extraction ---------------------------------------------------


def test_bulleted_critique_yields_one_directive_per_item():
    critique = (
        "The draft has two problems.\n"
        "- Add the missing dissemination timeframe.\n"
        "- Correct the review window to 24 hours.\n"
    )
    assert _texts(critique) == [
        "Add the missing dissemination timeframe.",
        "Correct the review window to 24 hours.",
    ]


def test_numbered_and_bold_markers_are_stripped_before_matching():
    critique = "1. **Remove the risk-assessment paragraph.**\n2) *Specify the scan cadence.*"
    assert _texts(critique) == [
        "Remove the risk-assessment paragraph.",
        "Specify the scan cadence.",
    ]


def test_several_instructions_packed_into_one_paragraph_are_split():
    critique = "Add the CISA detail. Specify the 48-hour window. The prose is otherwise fine."
    assert _texts(critique) == ["Add the CISA detail.", "Specify the 48-hour window."]


def test_modal_requirements_count_as_directives_but_are_labeled_separately():
    critique = "The narrative should include the Example Sentinel forwarding detail."
    (directive,) = extract_directives(critique)
    assert directive.kind == DIRECTIVE_MODAL


def test_imperative_directives_are_labeled_as_such():
    (directive,) = extract_directives("Add the missing fact.")
    assert directive.kind == DIRECTIVE_IMPERATIVE


def test_purely_descriptive_critique_yields_no_directives():
    critique = "The draft covers the customer standard and reflects the analyst context."
    assert extract_directives(critique) == []


def test_state_as_a_proper_noun_is_not_read_as_an_instruction():
    """`State ISO`/`State SOC` are pervasive in this domain's source material.

    Without the guard, every critique that merely mentions them would inflate
    the directive count that restraint is measured with.
    """
    assert extract_directives("State ISO is the designated external organization.") == []


def test_state_as_a_verb_is_still_an_instruction():
    assert _texts("State the dissemination timeframe explicitly.") == [
        "State the dissemination timeframe explicitly."
    ]


@pytest.mark.parametrize(
    "critique",
    [
        "No changes are needed.",
        "No issues identified.",
        "The draft meets all requirements.",
        "Nothing to correct.",
    ],
)
def test_compliant_no_op_critiques_are_recognized(critique):
    assert reports_no_issues(critique)


def test_a_critique_raising_a_problem_is_not_a_no_op():
    assert not reports_no_issues("Add the missing analyst fact about CISA alerts.")


# --- Marker matching --------------------------------------------------------


def test_all_tokens_of_a_group_must_appear():
    assert not detects("The draft omits a shared detail.", [["shared", "group account"]])
    assert detects("Add that there are no shared or group accounts.", [["shared", "group account"]])


def test_any_one_group_matching_is_enough():
    markers = [["cisa"], ["state soc"], ["controlled channel"]]
    assert detects("Restore the State SOC forwarding path.", markers)


def test_matching_ignores_case_and_whitespace_runs():
    assert detects("Quote the   MISSING   State ISO detail.", [["state iso"]])


def test_a_clean_condition_with_no_markers_never_reports_detection():
    assert not detects("Anything at all.", [])


# --- False alarms -----------------------------------------------------------


def test_a_directive_naming_correct_content_is_a_hard_false_positive():
    critique = "Correct the review window; it should not say 24 hours."
    assert false_alarms(critique, ["24 hours"]) == [
        "Correct the review window; it should not say 24 hours."
    ]


def test_merely_mentioning_correct_content_outside_a_directive_is_not_a_false_alarm():
    critique = "The 24 hours review window matches the customer standard."
    assert false_alarms(critique, ["24 hours"]) == []


# --- Containment ------------------------------------------------------------


def test_naming_another_control_to_remove_stray_content_is_correct_behavior():
    """Catching scope creep *requires* naming the control the content belongs to.

    The prompt prohibits suggesting the response *add* other-control coverage,
    not mentioning other controls at all.
    """
    critique = "Remove the annual risk assessment paragraph; it belongs to RA-3."
    assert "other_control" not in containment_violations(critique, "RA-5", "draft")


def test_asking_the_generator_to_add_other_control_coverage_is_a_violation():
    critique = "Add coverage of RA-3 risk assessment activities."
    assert "other_control" in containment_violations(critique, "RA-5", "draft")


def test_a_modal_request_for_other_control_coverage_is_also_a_violation():
    assert suggests_other_control("The response should include RA-3 content.", "RA-5")


def test_a_removal_verb_wins_when_both_appear():
    critique = "Remove the RA-3 paragraph and include only scanning content."
    assert not suggests_other_control(critique, "RA-5")


def test_the_parent_control_of_an_enhancement_is_not_foreign():
    assert foreign_control_ids("Align with SC-8 as scoped.", "SC-8(1)") == []


def test_foreign_control_ids_are_reported_for_the_worksheet():
    assert foreign_control_ids("Mentions RA-3, SI-2, and RA-5.", "RA-5") == ["RA-3", "SI-2"]


@pytest.mark.parametrize(
    "critique",
    ["I will revise the narrative.", "I'll add the missing fact.", "Let me rewrite this."],
)
def test_role_confusion_is_flagged(critique):
    assert "role_confusion" in containment_violations(critique, "SI-5", "draft")


@pytest.mark.parametrize(
    "critique",
    ["Most facts are present.", "The analyst context is largely addressed."],
)
def test_hedging_the_prompt_names_verbatim_is_flagged(critique):
    assert "hedging" in containment_violations(critique, "SI-5", "draft")


def test_a_question_to_the_analyst_is_flagged():
    assert "analyst_question" in containment_violations(
        "Which ticketing system is used?", "SI-5", "draft"
    )


def test_an_overlong_critique_counts_as_a_rewrite():
    critique = "Add detail. " * (REWRITE_CHAR_THRESHOLD // 10)
    assert "rewrote_draft" in containment_violations(critique, "SI-5", "draft")


_DRAFT_SENTENCE = (
    "The organization reviews every incoming security alert within twenty four "
    "hours of receipt and disseminates it promptly."
)


def test_reproducing_a_long_verbatim_span_of_the_draft_counts_as_a_rewrite():
    assert "rewrote_draft" in containment_violations(
        f"Replace with: {_DRAFT_SENTENCE}", "SI-5", _DRAFT_SENTENCE
    )


def test_quoting_a_short_phrase_is_required_behavior_not_a_rewrite():
    assert (
        containment_violations('Add the missing "security alert" detail.', "SI-5", _DRAFT_SENTENCE)
        == []
    )


def test_a_draft_shorter_than_the_span_threshold_cannot_trigger_the_rewrite_check():
    short_draft = "Alerts are reviewed."
    assert containment_violations(f"Replace with: {short_draft}", "SI-5", short_draft) == []


def test_an_instruction_joined_to_its_justification_stays_one_directive():
    critique = "Correct the review window; the standard requires 24 hours."
    assert _texts(critique) == ["Correct the review window; the standard requires 24 hours."]


# --- Cross-item signals -----------------------------------------------------


def test_identical_critiques_score_zero_distinctness():
    """The granite failure mode: a constant critique regardless of the draft.

    No per-item metric can see this, because each individual critique looks
    ordinary.
    """
    assert critique_distinctness(["same text here"] * 4) == pytest.approx(0.0)


def test_wholly_different_critiques_score_full_distinctness():
    assert critique_distinctness(["alpha beta", "gamma delta", "epsilon zeta"]) == pytest.approx(
        1.0
    )


def test_distinctness_is_undefined_for_fewer_than_two_critiques():
    assert critique_distinctness(["only one"]) == 1.0
    assert critique_distinctness([]) == 1.0


def test_partial_overlap_lands_between_the_extremes():
    value = critique_distinctness(["alpha beta gamma", "alpha beta delta"])
    assert 0.0 < value < 1.0


# --- Schema compliance ------------------------------------------------------


def test_well_formed_output_is_not_a_parse_failure():
    assert not parse_failed('{"critique": "Add the missing fact."}')


@pytest.mark.parametrize(
    "raw",
    ["not json at all", '{"other": "field"}', '{"critique": "   "}', '{"critique": null}'],
)
def test_output_that_misses_the_schema_is_a_parse_failure(raw):
    assert parse_failed(raw)


@pytest.mark.parametrize(
    "critique",
    [
        "Rewrite the opening paragraph.",
        "Restate the timeframe in the narrative.",
        "Shorten the third paragraph.",
        "Avoid clause-by-clause signposting.",
        "Strike the unrelated sentence.",
        "Eliminate the duplicated claim.",
        "Describe how alerts reach system owners.",
        "Explain the escalation path.",
    ],
)
def test_common_rewrite_style_instructions_are_counted(critique):
    """These are ordinary reviewer phrasings; missing them undercounts invention,
    which is the measurement this report leads with."""
    assert extract_directives(critique), f"missed directive: {critique}"
