from pathlib import Path

from typer.testing import CliRunner

from security_response_generator import cli

runner = CliRunner()


def _fake_result(tmp_path: Path):
    return cli.reviewer_evaluation.ReviewerEvaluationResult(
        candidate_model="candidate:latest",
        comparison_model=cli.config.REVIEW_MODEL,
        profile="smoke",
        output_dir=tmp_path / "run",
        generation_model=cli.config.GENERATION_MODEL,
        embedding_model=cli.config.EMBEDDING_MODEL,
        records=[],
        cases=[],
    )


def _patch_preflight(monkeypatch):
    monkeypatch.setattr(cli.reviewer_evaluation, "validate_preflight", lambda *args, **kwargs: None)


def _invoke(tmp_path, *extra, input_text="\n"):
    return runner.invoke(
        cli.app,
        ["evaluate-reviewer", "candidate:latest", "--output-dir", str(tmp_path), *extra],
        input=input_text,
    )


def test_the_plan_defaults_to_no_and_does_not_run(monkeypatch, tmp_path):
    _patch_preflight(monkeypatch)
    monkeypatch.setattr(
        cli.reviewer_evaluation,
        "run_evaluation",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not run")),
    )

    result = _invoke(tmp_path)

    assert result.exit_code == 1, result.output
    assert "Proceed with this reviewer evaluation? [y/N]" in result.output
    assert "Aborted." in result.output


def test_the_plan_states_what_is_and_is_not_measured(monkeypatch, tmp_path):
    _patch_preflight(monkeypatch)
    result = _invoke(tmp_path)

    assert "Candidate reviewer:  candidate:latest" in result.output
    assert "Atomic requirement/statement review, not full-response critique" in result.output
    assert "20 total (10 per reviewer)" in result.output
    assert "no model grades the reviewer" in result.output
    assert "supported, missing, contradicts, or adds unsupported info" in result.output
    assert "Only an exact match to the frozen classification is scored" in result.output
    assert "Optional constructive feedback is retained but never scored" in result.output
    assert "Prose quality and stylistic preferences are not judged" in result.output
    assert "secondary diagnostic" in result.output
    assert "srg generate --review" in result.output
    assert "SMOKE - development feedback, not qualification evidence" in result.output
    assert "newest 20 recognized runs kept" in result.output
    assert "No active engagement data will be used" in result.output
    assert "Environment:         Settings are not modified" in result.output


def test_the_plan_names_every_draft_condition(monkeypatch, tmp_path):
    _patch_preflight(monkeypatch)
    result = _invoke(tmp_path)
    for condition in cli.reviewer_evaluation.CONDITIONS:
        assert condition in result.output


def test_the_plan_explains_why_other_models_are_loaded(monkeypatch, tmp_path):
    """Loading the generator without prompting it is surprising; say why."""
    _patch_preflight(monkeypatch)
    result = _invoke(tmp_path)
    assert "loaded but" in result.output
    assert "never prompted" in result.output
    assert "coexistence" in result.output


def test_plan_says_statements_are_fixed_and_only_reviewers_must_differ(monkeypatch, tmp_path):
    _patch_preflight(monkeypatch)
    result = _invoke(tmp_path)
    assert "one fixed mock generated" in result.output
    assert "no analyst or engagement context" in result.output
    assert "candidate and comparison reviewers must be different" in result.output
    assert "candidate may be any installed local model" in result.output


def test_a_profile_other_than_smoke_is_refused(monkeypatch, tmp_path):
    _patch_preflight(monkeypatch)
    result = _invoke(tmp_path, "--profile", "standard")

    assert result.exit_code == 2, result.output
    assert "Only '--profile smoke' is currently available" in result.output
    assert "not yet calibrated" in result.output


def test_a_preflight_failure_stops_before_any_model_call(monkeypatch, tmp_path):
    def fail(*args, **kwargs):
        raise ValueError("Required local model(s) are not installed:\n  ollama pull x")

    monkeypatch.setattr(cli.reviewer_evaluation, "validate_preflight", fail)
    monkeypatch.setattr(
        cli.reviewer_evaluation,
        "run_evaluation",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not run")),
    )

    result = _invoke(tmp_path)

    assert result.exit_code == 1, result.output
    assert "Reviewer evaluation preflight failed" in result.output


def test_confirming_runs_the_evaluation_and_prints_the_summary(monkeypatch, tmp_path):
    _patch_preflight(monkeypatch)
    monkeypatch.setattr(
        cli.reviewer_evaluation, "run_evaluation", lambda *a, **k: _fake_result(tmp_path)
    )
    monkeypatch.setattr(cli.reviewer_evaluation, "render_summary", lambda *a, **k: "SUMMARY BODY")

    result = _invoke(tmp_path, "--yes")

    assert result.exit_code == 0, result.output
    assert "SUMMARY BODY" in result.output


def test_confirmation_accepts_y_or_yes_case_insensitively(monkeypatch, tmp_path):
    _patch_preflight(monkeypatch)
    monkeypatch.setattr(
        cli.reviewer_evaluation, "run_evaluation", lambda *a, **k: _fake_result(tmp_path)
    )
    monkeypatch.setattr(cli.reviewer_evaluation, "render_summary", lambda *a, **k: "SUMMARY BODY")

    for answer in ("y", "Y", "yes", "YES", "Yes"):
        result = _invoke(tmp_path, input_text=f"{answer}\n")
        assert result.exit_code == 0, result.output
        assert "SUMMARY BODY" in result.output


def test_any_other_confirmation_answer_aborts_without_reprompting(monkeypatch, tmp_path):
    _patch_preflight(monkeypatch)
    monkeypatch.setattr(
        cli.reviewer_evaluation,
        "run_evaluation",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not run")),
    )

    result = _invoke(tmp_path, input_text="maybe\n")

    assert result.exit_code == 1, result.output
    assert result.output.count("Proceed with this reviewer evaluation?") == 1
    assert "Aborted." in result.output


def test_an_interrupt_reports_the_preserved_artifacts(monkeypatch, tmp_path):
    _patch_preflight(monkeypatch)

    def interrupt(*args, **kwargs):
        raise cli.reviewer_evaluation.ReviewerEvaluationInterrupted(tmp_path / "partial")

    monkeypatch.setattr(cli.reviewer_evaluation, "run_evaluation", interrupt)

    result = _invoke(tmp_path, "--yes")

    assert result.exit_code == 130, result.output
    assert "completed work was preserved" in result.output
    assert "Partial artifacts:" in result.output


def test_the_default_comparison_is_the_configured_reviewer(monkeypatch, tmp_path):
    _patch_preflight(monkeypatch)
    result = _invoke(tmp_path)
    assert f"{cli.config.REVIEW_MODEL} (SRG configured reviewer)" in result.output


def test_an_explicit_comparison_override_is_labeled(monkeypatch, tmp_path):
    _patch_preflight(monkeypatch)
    result = _invoke(tmp_path, "--compare-to", "other:latest")
    assert "other:latest (explicit override)" in result.output
