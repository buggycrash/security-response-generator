import json

import pytest

from security_response_generator import model_evaluation


def _grade_reply() -> str:
    return json.dumps(
        {
            "assessment": "viable",
            "strengths": ["grounded"],
            "issues": [],
            "customer_standard_coverage": "full",
            "private_context_coverage": "full",
            "scope": "focused",
            "human_review_focus": [],
        }
    )


def _analyst_reply(evidence: str = "completed draft") -> str:
    return json.dumps({"included": True, "evidence_quote": evidence})


def _resident_snapshots(models):
    return {
        model: {
            "model": model,
            "size_bytes": 3 * 1024**3,
            "size_vram_bytes": 3 * 1024**3,
            "context_length": 16384,
        }
        for model in models
    }


def _fake_review(messages, response_format=None, **kwargs):
    if response_format == model_evaluation.ANALYST_INCLUSION_SCHEMA:
        return _analyst_reply()
    return _grade_reply()


def _fake_generate(case, model, seed, phase):
    return model_evaluation.GenerationOutput(
        response_text=f"# {case.control_id}\n\nFictional narrative for seed {seed}.",
        model_calls=[{"model": model, "total_duration": 10}],
        forced_completion=False,
    )


def _patch_common(monkeypatch):
    monkeypatch.setattr(model_evaluation, "unload_models", lambda models: None)
    monkeypatch.setattr(model_evaluation, "embed_query", lambda text: [0.0])
    monkeypatch.setattr(model_evaluation, "residency_snapshots", _resident_snapshots)
    monkeypatch.setattr(model_evaluation, "review_messages", _fake_review)


def test_standard_run_produces_sixty_responses_and_thirty_grades(monkeypatch, tmp_path):
    _patch_common(monkeypatch)

    result = model_evaluation.run_evaluation(
        "standard",
        "candidate:latest",
        "default:latest",
        generate=_fake_generate,
        output_root=tmp_path / "runs",
    )

    assert result.profile == "standard"
    assert len(result.trials) == 60
    assert len(result.grades) == 30
    assert result.model_block_order == ["candidate", "comparison"]


def test_standard_run_schedule_cold_warm_assignment_across_full_block(monkeypatch, tmp_path):
    _patch_common(monkeypatch)

    result = model_evaluation.run_evaluation(
        "standard",
        "candidate:latest",
        "default:latest",
        generate=_fake_generate,
        output_root=tmp_path / "runs",
    )

    candidate_trials = [trial for trial in result.trials if trial.role == "candidate"]
    comparison_trials = [trial for trial in result.trials if trial.role == "comparison"]
    assert len(candidate_trials) == 30
    assert len(comparison_trials) == 30
    assert [trial.phase for trial in candidate_trials].count("cold") == 1
    assert [trial.phase for trial in candidate_trials].count("warm") == 29
    assert [trial.phase for trial in comparison_trials].count("cold") == 1
    assert [trial.phase for trial in comparison_trials].count("warm") == 29


def test_standard_run_trial_numbers_and_seeds_are_consistent_per_case(monkeypatch, tmp_path):
    _patch_common(monkeypatch)

    result = model_evaluation.run_evaluation(
        "standard",
        "candidate:latest",
        "default:latest",
        generate=_fake_generate,
        output_root=tmp_path / "runs",
    )

    cases = model_evaluation.load_standard_cases()
    for case in cases:
        for role in ("candidate", "comparison"):
            case_trials = sorted(
                (
                    trial
                    for trial in result.trials
                    if trial.case_id == case.id and trial.role == role
                ),
                key=lambda trial: trial.trial_number,
            )
            assert [trial.trial_number for trial in case_trials] == [1, 2, 3]
            assert [trial.seed for trial in case_trials] == [42, 43, 44]


def test_standard_run_writes_artifacts_and_retains_pool_with_smoke(monkeypatch, tmp_path):
    _patch_common(monkeypatch)
    output_root = tmp_path / "runs"

    smoke_result = model_evaluation.run_smoke_evaluation(
        "candidate:latest",
        "default:latest",
        generate=_fake_generate,
        output_root=output_root,
    )
    standard_result = model_evaluation.run_evaluation(
        "standard",
        "candidate:latest",
        "default:latest",
        generate=_fake_generate,
        output_root=output_root,
    )

    assert smoke_result.output_dir.joinpath("results.json").is_file()
    assert standard_result.output_dir.joinpath("results.json").is_file()
    assert len(list(output_root.iterdir())) == 2


def test_standard_run_interruption_preserves_partial_work_at_scale(monkeypatch, tmp_path):
    _patch_common(monkeypatch)
    generation_calls = 0

    def interrupt_after_five(case, model, seed, phase):
        nonlocal generation_calls
        generation_calls += 1
        if generation_calls == 6:
            raise KeyboardInterrupt
        return model_evaluation.GenerationOutput(
            response_text="completed draft", model_calls=[], forced_completion=False
        )

    with pytest.raises(model_evaluation.EvaluationInterrupted) as exc_info:
        model_evaluation.run_evaluation(
            "standard",
            "candidate:latest",
            "default:latest",
            generate=interrupt_after_five,
            output_root=tmp_path / "runs",
        )

    output_dir = exc_info.value.output_dir
    saved = json.loads(output_dir.joinpath("results.json").read_text())
    assert saved["status"] == "interrupted"
    assert len(saved["trials"]) == 5
    assert "Completed response trials: 5/60" in output_dir.joinpath("INTERRUPTED.txt").read_text()


def test_standard_run_model_ejection_hard_fails_at_scale(monkeypatch, tmp_path):
    monkeypatch.setattr(model_evaluation, "unload_models", lambda models: None)
    monkeypatch.setattr(model_evaluation, "embed_query", lambda text: [0.0])
    monkeypatch.setattr(model_evaluation, "review_messages", _fake_review)
    monkeypatch.setattr(
        model_evaluation,
        "residency_snapshots",
        lambda models: {
            model: (
                None
                if model == model_evaluation.config.EMBEDDING_MODEL
                else _resident_snapshots([model])[model]
            )
            for model in models
        },
    )

    with pytest.raises(OSError, match="Ollama ejected required model"):
        model_evaluation.run_evaluation(
            "standard",
            "candidate:latest",
            "default:latest",
            generate=_fake_generate,
            output_root=tmp_path / "runs",
        )
