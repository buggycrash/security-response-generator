from pathlib import Path

from typer.testing import CliRunner

from security_response_generator import cli

runner = CliRunner()


def _fake_result(tmp_path: Path):
    return cli.model_evaluation.EvaluationResult(
        candidate_model="candidate:latest",
        comparison_model=cli.config.DEFAULT_GENERATION_MODEL,
        grader_model=cli.config.REVIEW_MODEL,
        profile="smoke",
        output_dir=tmp_path / "run",
        trials=[],
        grades=[],
    )


def _patch_preflight(monkeypatch):
    monkeypatch.setattr(cli.model_evaluation, "validate_preflight", lambda *args: None)


def test_evaluate_model_shows_plan_and_defaults_confirmation_to_no(monkeypatch, tmp_path):
    _patch_preflight(monkeypatch)
    monkeypatch.setattr(
        cli.model_evaluation,
        "run_smoke_evaluation",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not run")),
    )

    result = runner.invoke(
        cli.app,
        ["evaluate-model", "candidate:latest", "--output-dir", str(tmp_path)],
        input="\n",
    )

    assert result.exit_code == 0, result.output
    assert "Candidate model:  candidate:latest" in result.output
    assert cli.config.DEFAULT_GENERATION_MODEL in result.output
    assert "10 total" in result.output
    assert "20 total (analyst check + assessment per response)" in result.output
    assert "memory/GPU residency" in result.output
    assert "Missing analyst context or no customer coverage = not viable" in result.output
    assert "Partial customer or incomplete private coverage = edits" in result.output
    assert "2+ = not viable; 1 = edits" in result.output
    assert "does not score prose quality or writing style" in result.output
    assert "No active engagement data" in result.output
    assert "Models larger than SRG's default" in result.output
    assert "mixture-of-experts (MoE) models" in result.output
    assert "\n    Hardware note:" in result.output
    assert "the run is fully noninteractive" in result.output
    assert "SRG automatically makes one final call" in result.output
    assert "Proceed? [y/N]" in result.output
    assert "Evaluation cancelled" in result.output


def test_evaluate_model_uses_shipped_default_without_compare_flag(monkeypatch, tmp_path):
    _patch_preflight(monkeypatch)
    captured = {}

    def fake_run(candidate, comparison, **kwargs):
        captured["models"] = (candidate, comparison)
        return _fake_result(tmp_path)

    monkeypatch.setattr(cli.model_evaluation, "run_smoke_evaluation", fake_run)
    monkeypatch.setattr(cli.model_evaluation, "render_summary", lambda result, **kwargs: "summary")

    result = runner.invoke(
        cli.app,
        [
            "evaluate-model",
            "candidate:latest",
            "--output-dir",
            str(tmp_path),
            "--yes",
        ],
    )

    assert result.exit_code == 0, result.output
    assert captured["models"] == (
        "candidate:latest",
        cli.config.DEFAULT_GENERATION_MODEL,
    )
    assert "Starting smoke evaluation..." in result.output
    assert "summary" in result.output


def test_evaluate_model_rejects_unknown_profile(monkeypatch):
    result = runner.invoke(
        cli.app,
        ["evaluate-model", "candidate:latest", "--profile", "standard"],
    )

    assert result.exit_code == 2
    assert "Only '--profile smoke'" in result.output


def test_evaluate_model_prints_heading_before_slow_preflight(monkeypatch, tmp_path):
    def fail_preflight(*args):
        raise ConnectionError("Ollama unavailable")

    monkeypatch.setattr(cli.model_evaluation, "validate_preflight", fail_preflight)

    result = runner.invoke(
        cli.app,
        ["evaluate-model", "candidate:latest", "--output-dir", str(tmp_path)],
    )

    assert result.exit_code == 1
    assert result.output.index("SRG generation-model evaluation") < result.output.index(
        "Model evaluation preflight failed"
    )


def test_evaluate_model_reports_preserved_artifacts_on_interrupt(monkeypatch, tmp_path):
    _patch_preflight(monkeypatch)
    artifact_dir = tmp_path / "run"

    def interrupt(*args, **kwargs):
        raise cli.model_evaluation.EvaluationInterrupted(artifact_dir)

    monkeypatch.setattr(cli.model_evaluation, "run_smoke_evaluation", interrupt)

    result = runner.invoke(
        cli.app,
        ["evaluate-model", "candidate:latest", "--output-dir", str(tmp_path), "--yes"],
    )

    assert result.exit_code == 130
    assert "completed work was preserved" in result.output
    assert f"Partial artifacts: {artifact_dir.resolve()}" in result.output
