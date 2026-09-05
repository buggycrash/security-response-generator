"""Tests for `derive_assessment`, which assigns every evaluation result.

The reviewer model's own verdict is recorded but never decides the result; the
category comes from its structured observations plus the checks SRG owns
outright. Both profiles use this single path.
"""

import json

import pytest

from security_response_generator import model_evaluation


def _case(**overrides):
    defaults = {
        "id": "si5-context",
        "control_id": "SI-5",
        "context": "CISA alerts are received by the State SOC and forwarded to system owners.",
        "customer_chunks": ["The State requires alerts reviewed within 24 hours of receipt."],
        "baseline_chunks": ["Receive system security alerts from external organizations."],
        "private_chunks": ["DEMO-ECMS forwards audit logs to Example Sentinel for monitoring."],
        "rubric": ["Evaluate coverage of each supplied source separately."],
    }
    defaults.update(overrides)
    return model_evaluation.EvaluationCase(**defaults)


def _trial(placeholder_count=0, narrative_validations=0):
    return model_evaluation.TrialRecord(
        role="candidate",
        model="candidate:latest",
        case_id="si5-context",
        control_id="SI-5",
        seed=42,
        phase="warm",
        wall_seconds=1.0,
        response_text="draft",
        model_calls=[],
        forced_completion=False,
        residency=None,
        embedding_residency=None,
        placeholder_count=placeholder_count,
        narrative_validations=narrative_validations,
    )


def _finding(**overrides):
    finding = {
        "assessment": "viable",
        "strengths": [],
        "issues": [],
        "customer_standard_coverage": "full",
        "private_context_coverage": "full",
        "scope": "focused",
        "human_review_focus": [],
        "analyst_context_included": True,
    }
    finding.update(overrides)
    return finding


def _derive(finding=None, case=None, trial=None):
    return model_evaluation.derive_assessment(
        finding if finding is not None else _finding(),
        case or _case(),
        trial or _trial(),
    )


def test_clean_observations_produce_viable():
    result = _derive()

    assert result["assessment"] == "viable"
    assert "reviewer_divergence" not in result


def test_a_harsh_reviewer_verdict_alone_no_longer_fails_a_response():
    """The regression this work exists to fix.

    The old policy started from the reviewer's verdict and only escalated, so a
    not_viable could never be walked back even when every structured
    observation the same call returned was clean.
    """
    result = _derive(finding=_finding(assessment="not_viable"))

    assert result["assessment"] == "viable"
    assert result["reviewer_assessment"] == "not_viable"
    assert (
        "reviewer model reported not_viable; SRG assigned viable" in (result["reviewer_divergence"])
    )


def test_the_reviewer_verdict_never_influences_the_category():
    categories = {
        _derive(finding=_finding(assessment=verdict))["assessment"]
        for verdict in ("viable", "material_edits", "not_viable", "inconclusive")
    }

    assert categories == {"viable"}


def test_reviewer_assessment_is_always_preserved_for_calibration():
    result = _derive(finding=_finding(assessment="not_viable"))

    assert result["reviewer_assessment"] == "not_viable"
    assert result["assessment"] != result["reviewer_assessment"]


def test_total_customer_omission_is_not_viable():
    result = _derive(finding=_finding(customer_standard_coverage="none"))

    assert result["assessment"] == "not_viable"
    assert "no supplied customer-standard requirements" in result["decision_trace"][0]


def test_missing_analyst_context_is_not_viable():
    result = _derive(finding=_finding(analyst_context_included=False))

    assert result["assessment"] == "not_viable"
    assert "analyst precheck" in result["decision_trace"][0]


def test_unverified_analyst_context_is_inconclusive():
    assert _derive(finding=_finding(analyst_context_included=None))["assessment"] == "inconclusive"


def test_a_validation_heading_in_the_narrative_is_an_automatic_failure():
    """The analyst reads and pastes the narrative first, so evidence suggestions
    embedded in it mislead them and inflate the reviewer's coverage judgment."""
    result = _derive(trial=_trial(narrative_validations=1))

    assert result["assessment"] == "not_viable"
    assert "validation heading(s)" in result["decision_trace"][0]


def test_partial_customer_coverage_requires_edits():
    assert (
        _derive(finding=_finding(customer_standard_coverage="partial"))["assessment"]
        == "material_edits"
    )


@pytest.mark.parametrize("coverage", ["none", "partial"])
def test_incomplete_private_coverage_requires_edits_but_is_not_fatal(coverage):
    assert _derive(finding=_finding(private_context_coverage=coverage))["assessment"] == (
        "material_edits"
    )


def test_material_scope_drift_requires_edits():
    assert _derive(finding=_finding(scope="material_drift"))["assessment"] == "material_edits"


def test_placeholder_thresholds_are_preserved():
    assert _derive(trial=_trial(placeholder_count=1))["assessment"] == "material_edits"
    assert _derive(trial=_trial(placeholder_count=2))["assessment"] == "not_viable"


def test_empty_source_tiers_carry_no_coverage_penalty():
    result = _derive(
        finding=_finding(customer_standard_coverage="none", private_context_coverage="none"),
        case=_case(customer_chunks=[], private_chunks=[]),
    )

    assert result["assessment"] == "viable"
    assert result["customer_standard_coverage"] == "not_provided"
    assert result["private_context_coverage"] == "not_provided"


def test_unparseable_grader_output_passes_through_untouched():
    assert model_evaluation.derive_assessment(None, _case(), _trial()) is None


# --- integration with the run loop ------------------------------------------


def _smoke_run(monkeypatch, tmp_path, response_text=None):
    monkeypatch.setattr(model_evaluation, "unload_models", lambda models: None)
    monkeypatch.setattr(model_evaluation, "embed_query", lambda text: [0.0])
    monkeypatch.setattr(
        model_evaluation,
        "residency_snapshots",
        lambda models: {
            model: {
                "model": model,
                "size_bytes": 3 * 1024**3,
                "size_vram_bytes": 3 * 1024**3,
                "context_length": 16384,
            }
            for model in models
        },
    )

    def fake_review(messages, response_format=None, **kwargs):
        if response_format == model_evaluation.ANALYST_INCLUSION_SCHEMA:
            payload = json.loads(messages[1]["content"])
            return json.dumps(
                {"included": True, "evidence_quote": payload["narrative"].splitlines()[0]}
            )
        return json.dumps(
            {
                "assessment": "not_viable",
                "strengths": [],
                "issues": ["reviewer model is habitually harsh"],
                "customer_standard_coverage": "full",
                "private_context_coverage": "full",
                "scope": "focused",
                "human_review_focus": [],
            }
        )

    monkeypatch.setattr(model_evaluation, "review_messages", fake_review)

    return model_evaluation.run_smoke_evaluation(
        "candidate:latest",
        "default:latest",
        generate=lambda case, model, seed, phase: model_evaluation.GenerationOutput(
            response_text=(
                response_text
                or "Alerts are reviewed within 24 hours and disseminated to system owners."
            ),
            model_calls=[],
            forced_completion=False,
        ),
        output_root=tmp_path / "runs",
    )


def test_smoke_run_overrides_a_harsh_reviewer_and_records_both_verdicts(monkeypatch, tmp_path):
    result = _smoke_run(monkeypatch, tmp_path)

    finding = result.grades[0].parsed["response_a"]
    assert finding["reviewer_assessment"] == "not_viable"
    assert finding["assessment"] == "viable"

    findings_text = result.output_dir.joinpath("grader-findings.md").read_text()
    assert "Reviewer model's own verdict (recorded, not used): not_viable" in findings_text
    assert "How SRG assigned this category:" in findings_text


def test_a_validation_heading_fails_the_response_during_a_run(monkeypatch, tmp_path):
    narrative = (
        "Alerts are reviewed within 24 hours of receipt.\n"
        "**Validations**: - Screenshot of the alert dashboard.\n"
    )

    result = _smoke_run(monkeypatch, tmp_path, response_text=narrative)

    assert result.trials[0].narrative_validations == 1
    assert result.grades[0].parsed["response_a"]["assessment"] == "not_viable"


def test_the_standard_profile_derives_categories_the_same_way(monkeypatch, tmp_path):
    """Both profiles share one categorization path, so a harsh reviewer verdict
    is overridden identically at qualification scale."""
    monkeypatch.setattr(model_evaluation, "unload_models", lambda models: None)
    monkeypatch.setattr(model_evaluation, "embed_query", lambda text: [0.0])
    monkeypatch.setattr(
        model_evaluation,
        "residency_snapshots",
        lambda models: {
            model: {
                "model": model,
                "size_bytes": 1,
                "size_vram_bytes": 1,
                "context_length": 16384,
            }
            for model in models
        },
    )
    monkeypatch.setattr(
        model_evaluation,
        "review_messages",
        lambda messages, response_format=None, **kwargs: (
            json.dumps({"included": True, "evidence_quote": "Alerts are reviewed"})
            if response_format == model_evaluation.ANALYST_INCLUSION_SCHEMA
            else json.dumps(
                {
                    "assessment": "not_viable",
                    "strengths": [],
                    "issues": [],
                    "customer_standard_coverage": "full",
                    "private_context_coverage": "full",
                    "scope": "focused",
                    "human_review_focus": [],
                }
            )
        ),
    )

    result = model_evaluation.run_evaluation(
        "standard",
        "candidate:latest",
        "default:latest",
        generate=lambda case, model, seed, phase: model_evaluation.GenerationOutput(
            response_text="Alerts are reviewed within 24 hours and disseminated to owners.",
            model_calls=[],
            forced_completion=False,
        ),
        output_root=tmp_path / "runs",
    )

    for grade in result.grades:
        for finding in grade.parsed.values():
            assert finding["reviewer_assessment"] == "not_viable"
            assert finding["assessment"] != "not_viable"
            assert finding["decision_trace"]


def test_neither_profile_retains_the_superseded_escalation_policy():
    """The old reviewer-verdict escalation is gone, not merely bypassed."""
    assert not hasattr(model_evaluation, "_apply_finding_policy")
    assert not hasattr(model_evaluation, "_apply_completeness_policy")
    assert not hasattr(model_evaluation.PROFILES["standard"], "derives_assessment")


def test_reviewer_divergence_is_treated_as_high_risk_for_human_review():
    """Divergent trials are the calibration evidence for deriving the category,
    so the blinded sample must prioritize them."""
    from security_response_generator import model_evaluation_sampling as sampling

    grade = model_evaluation.GradeRecord(
        case_id="si5-context",
        response_a_role="candidate",
        response_b_role="comparison",
        parsed={
            "response_a": _finding(
                analyst_context_included=True,
                reviewer_divergence="reviewer model reported not_viable; SRG assigned viable",
            ),
            "response_b": _finding(analyst_context_included=True),
        },
        raw={},
        seed=42,
    )
    clean = model_evaluation.GradeRecord(
        case_id="si5-context",
        response_a_role="candidate",
        response_b_role="comparison",
        parsed={
            "response_a": _finding(analyst_context_included=True),
            "response_b": _finding(analyst_context_included=True),
        },
        raw={},
        seed=43,
    )

    assert sampling._is_high_risk(grade) is True
    assert sampling._is_high_risk(clean) is False
    assert "reviewer model's verdict" in sampling.RULE_DESCRIPTION


def test_standard_stats_count_reviewer_divergence():
    from security_response_generator import model_evaluation_stats as stats_module

    grades = [
        model_evaluation.GradeRecord(
            case_id="si5-context",
            response_a_role="candidate",
            response_b_role="comparison",
            parsed={
                "response_a": _finding(reviewer_divergence="differed"),
                "response_b": _finding(),
            },
            raw={},
            trial_number=1,
            seed=42,
        )
    ]

    candidate = stats_module._model_assessment_stats(grades, [], role="candidate")
    comparison = stats_module._model_assessment_stats(grades, [], role="comparison")

    assert candidate.reviewer_divergence_rate == 1.0
    assert comparison.reviewer_divergence_rate == 0.0


# --- model preference ranking ------------------------------------------------


def _rank(*assessments):
    return model_evaluation._preference_rank(list(assessments))


def test_unusable_trials_outweigh_a_single_good_one():
    """Regression: a model that failed two of three trials was reported as
    preferred over one that merely needed edits on all three, because the
    viable count used to dominate the ranking."""
    mostly_failed = _rank("viable", "not_viable", "not_viable")
    all_editable = _rank("material_edits", "material_edits", "material_edits")

    assert all_editable > mostly_failed


def test_one_unusable_trial_outweighs_one_good_trial():
    """The other real-run case: 1 viable + 1 not_viable + 1 inconclusive was
    preferred over a distribution containing no unusable trial at all."""
    with_failure = _rank("viable", "not_viable", "inconclusive")
    without_failure = _rank("material_edits", "inconclusive", "inconclusive")

    assert without_failure > with_failure


def test_viable_still_decides_when_unusable_counts_match():
    assert _rank("viable", "viable", "viable") > _rank(
        "material_edits", "material_edits", "material_edits"
    )
    assert _rank("viable", "material_edits") > _rank("material_edits", "material_edits")


def test_a_measurement_failure_never_outranks_a_real_result():
    assert _rank("viable", "viable", "material_edits") > _rank(
        "inconclusive", "inconclusive", "inconclusive"
    )
    assert _rank("material_edits") > _rank("inconclusive")


def test_identical_distributions_tie():
    assert _rank("viable", "material_edits") == _rank("material_edits", "viable")


@pytest.mark.parametrize(
    ("better", "worse"),
    [
        ("viable", "material_edits"),
        ("material_edits", "inconclusive"),
        ("inconclusive", "not_viable"),
    ],
)
def test_single_trial_severity_order_is_preserved(better, worse):
    """paired_outcome ranks one trial against one trial, so this ordering must
    not drift or head-to-head statistics stop being comparable across runs."""
    from security_response_generator import model_evaluation_stats as stats_module

    assert _rank(better) > _rank(worse)
    assert stats_module.paired_outcome(better, worse) == "win"
    assert stats_module.paired_outcome(worse, better) == "loss"
    assert stats_module.paired_outcome(better, better) == "tie"
