import random

from security_response_generator import model_evaluation
from security_response_generator import model_evaluation_stats as stats


def _finding(assessment, **overrides):
    finding = {
        "assessment": assessment,
        "analyst_context_included": True,
        "customer_standard_coverage": "full",
        "private_context_coverage": "full",
        "scope": "focused",
    }
    finding.update(overrides)
    return finding


def _trial(role, case_id, trial_number, *, placeholder_count=0, forced_completion=False):
    return model_evaluation.TrialRecord(
        role=role,
        model=f"{role}:latest",
        case_id=case_id,
        control_id="SI-5",
        seed=trial_number,
        phase="warm",
        wall_seconds=1.0,
        response_text="draft",
        model_calls=[],
        forced_completion=forced_completion,
        residency=None,
        embedding_residency=None,
        trial_number=trial_number,
        placeholder_count=placeholder_count,
    )


def _grade(case_id, seed, trial_number, candidate_assessment, comparison_assessment, **kwargs):
    return model_evaluation.GradeRecord(
        case_id=case_id,
        response_a_role="candidate",
        response_b_role="comparison",
        parsed={
            "response_a": _finding(candidate_assessment, **kwargs.get("candidate_overrides", {})),
            "response_b": _finding(comparison_assessment, **kwargs.get("comparison_overrides", {})),
        },
        raw="{}",
        trial_number=trial_number,
        seed=seed,
    )


def test_paired_outcome_matches_preference_rank_severity_order():
    assert stats.paired_outcome("viable", "material_edits") == "win"
    assert stats.paired_outcome("material_edits", "viable") == "loss"
    assert stats.paired_outcome("viable", "viable") == "tie"
    assert stats.paired_outcome("material_edits", "inconclusive") == "win"
    assert stats.paired_outcome("inconclusive", "not_viable") == "win"
    assert stats.paired_outcome("not_viable", "viable") == "loss"


def test_bootstrap_ci_is_deterministic_for_a_fixed_seed():
    deltas = [1, 1, 0, -1, 1, 0, -1, 1]

    first = stats._bootstrap_ci(deltas, seed=42, iterations=500)
    second = stats._bootstrap_ci(deltas, seed=42, iterations=500)

    assert first == second
    assert -1.0 <= first[0] <= first[1] <= 1.0


def test_bootstrap_ci_empty_deltas_returns_zero_interval():
    assert stats._bootstrap_ci([], seed=1, iterations=100) == (0.0, 0.0)


def test_compute_standard_stats_on_two_task_result():
    trials = [
        _trial("candidate", "task-a", 1),
        _trial("comparison", "task-a", 1),
        _trial("candidate", "task-a", 2, placeholder_count=2),
        _trial("comparison", "task-a", 2),
        _trial("candidate", "task-b", 1),
        _trial("comparison", "task-b", 1),
    ]
    grades = [
        _grade("task-a", 42, 1, "viable", "viable"),
        _grade(
            "task-a",
            43,
            2,
            "not_viable",
            "viable",
            candidate_overrides={"analyst_context_included": False},
        ),
        _grade("task-b", 42, 1, "material_edits", "not_viable"),
    ]
    result = model_evaluation.EvaluationResult(
        candidate_model="candidate:latest",
        comparison_model="comparison:latest",
        grader_model="grader:latest",
        profile="standard",
        output_dir="/tmp/unused",
        trials=trials,
        grades=grades,
    )
    metadata = {
        "task-a": model_evaluation.CaseMetadata(
            case_id="task-a",
            suite_version="standard-v1",
            tags=(),
            description="",
            customer_chunks_present=True,
            private_chunks_present=True,
        ),
        "task-b": model_evaluation.CaseMetadata(
            case_id="task-b",
            suite_version="standard-v1",
            tags=(),
            description="",
            customer_chunks_present=True,
            private_chunks_present=True,
        ),
    }

    result_stats = stats.compute_standard_stats(result, metadata)

    assert result_stats.suite_version == "standard-v1"
    assert {task.case_id for task in result_stats.task_stats} == {"task-a", "task-b"}

    task_a = next(task for task in result_stats.task_stats if task.case_id == "task-a")
    assert task_a.task_aggregate["candidate"] == "not_viable"
    assert task_a.task_aggregate["comparison"] == "viable"
    assert task_a.seed_consistent["candidate"] is False
    assert task_a.seed_consistent["comparison"] is True
    assert task_a.paired_outcomes == {42: "tie", 43: "loss"}
    assert task_a.per_model["candidate"].repeated_placeholder_rate == 0.5

    assert result_stats.paired.wins == 1  # task-b: candidate material_edits beats not_viable
    assert result_stats.paired.losses == 1  # task-a seed 43
    assert result_stats.paired.ties == 1  # task-a seed 42
    assert result_stats.hard_failure_counts["candidate"]["analyst_missing"] == 1
    assert result_stats.hard_failure_counts["candidate"]["repeated_placeholder"] == 1
    assert result_stats.hard_failure_counts["comparison"]["analyst_missing"] == 0

    lower, upper = result_stats.paired.bootstrap_win_rate_ci
    # The interval must land on the same 0-1 win-rate scale as paired.win_rate,
    # not the -1..1 net-score scale a naive win/loss/tie delta would produce.
    assert 0.0 <= lower <= upper <= 1.0


def test_macro_assessment_rates_weight_tasks_equally_despite_unbalanced_trial_counts():
    trials = (
        [_trial("candidate", "big-task", n) for n in range(1, 5)]
        + [_trial("comparison", "big-task", n) for n in range(1, 5)]
        + [_trial("candidate", "small-task", 1), _trial("comparison", "small-task", 1)]
    )
    grades = [_grade("big-task", 40 + n, n, "viable", "viable") for n in range(1, 5)] + [
        _grade("small-task", 42, 1, "not_viable", "viable")
    ]
    result = model_evaluation.EvaluationResult(
        candidate_model="candidate:latest",
        comparison_model="comparison:latest",
        grader_model="grader:latest",
        profile="standard",
        output_dir="/tmp/unused",
        trials=trials,
        grades=grades,
    )
    metadata = {
        "big-task": model_evaluation.CaseMetadata(
            case_id="big-task",
            suite_version="standard-v1",
            tags=(),
            description="",
            customer_chunks_present=True,
            private_chunks_present=True,
        ),
        "small-task": model_evaluation.CaseMetadata(
            case_id="small-task",
            suite_version="standard-v1",
            tags=(),
            description="",
            customer_chunks_present=True,
            private_chunks_present=True,
        ),
    }

    result_stats = stats.compute_standard_stats(result, metadata)

    # A naive micro-average across all 5 candidate trials would give not_viable a
    # 1/5 = 0.2 rate. Equal per-task weighting gives (0 + 1)/2 = 0.5 instead,
    # so the single small task's failure isn't diluted by the larger task.
    assert result_stats.macro_assessment_rates["candidate"]["not_viable"] == 0.5
    assert result_stats.macro_assessment_rates["candidate"]["viable"] == 0.5


def test_compute_standard_stats_does_not_touch_global_random_state():
    result = model_evaluation.EvaluationResult(
        candidate_model="candidate:latest",
        comparison_model="comparison:latest",
        grader_model="grader:latest",
        profile="standard",
        output_dir="/tmp/unused",
        trials=[],
        grades=[],
    )

    before = random.getstate()
    stats.compute_standard_stats(result, {})
    after = random.getstate()

    assert before == after
