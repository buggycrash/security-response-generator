"""Task-balanced descriptive statistics for the standard evaluation profile.

Every function here is descriptive evidence, not a pass/fail qualification
gate: calibrated qualification thresholds are explicit future work (see
docs/model-evaluation-standard-profile.md) and must not be inferred from this
module alone.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any

from security_response_generator.model_evaluation import (
    CaseMetadata,
    EvaluationResult,
    GradeRecord,
    TrialRecord,
    _aggregate_assessment,
    _preference_rank,
    _role_assessment,
)

STANDARD_BOOTSTRAP_SEED = 20260101
STANDARD_BOOTSTRAP_ITERATIONS = 2000

ASSESSMENTS = ("viable", "material_edits", "not_viable", "inconclusive")
HARD_FAILURE_REASONS = ("analyst_missing", "customer_none", "repeated_placeholder")
_ROLES = ("candidate", "comparison")


@dataclass
class ModelAssessmentStats:
    total_trials: int
    assessment_counts: dict[str, int]
    assessment_rates: dict[str, float]
    analyst_missing_rate: float
    analyst_unverified_rate: float
    customer_none_rate: float
    customer_partial_rate: float
    private_none_or_partial_rate: float
    placeholder_rate: float
    repeated_placeholder_rate: float
    forced_completion_rate: float
    scope_material_drift_rate: float


@dataclass
class TaskAssessmentStats:
    case_id: str
    control_id: str
    per_model: dict[str, ModelAssessmentStats]
    task_aggregate: dict[str, str]
    seed_consistent: dict[str, bool]
    paired_outcomes: dict[int, str]


@dataclass
class PairedSummary:
    wins: int
    losses: int
    ties: int
    win_rate: float
    loss_rate: float
    tie_rate: float
    macro_task_win_rate: float
    bootstrap_win_rate_ci: tuple[float, float]
    bootstrap_iterations: int
    bootstrap_seed: int


@dataclass
class StandardStats:
    suite_version: str
    task_stats: list[TaskAssessmentStats]
    macro_assessment_rates: dict[str, dict[str, float]]
    paired: PairedSummary
    hard_failure_counts: dict[str, dict[str, int]] = field(default_factory=dict)


def paired_outcome(candidate_assessment: str, comparison_assessment: str) -> str:
    """win/loss/tie from the candidate's perspective, reusing the existing severity order."""
    candidate_rank = _preference_rank([candidate_assessment])
    comparison_rank = _preference_rank([comparison_assessment])
    if candidate_rank == comparison_rank:
        return "tie"
    return "win" if candidate_rank > comparison_rank else "loss"


def _rate(count: int, total: int) -> float:
    return count / total if total else 0.0


def _finding(grade: GradeRecord, role: str) -> dict[str, Any] | None:
    if grade.parsed is None:
        return None
    label = "response_a" if grade.response_a_role == role else "response_b"
    finding = grade.parsed.get(label)
    return finding if isinstance(finding, dict) else None


def _matching_trial(
    trials: list[TrialRecord], *, role: str, case_id: str, trial_number: int
) -> TrialRecord | None:
    return next(
        (
            trial
            for trial in trials
            if trial.role == role
            and trial.case_id == case_id
            and trial.trial_number == trial_number
        ),
        None,
    )


def _model_assessment_stats(
    grades: list[GradeRecord], trials: list[TrialRecord], *, role: str
) -> ModelAssessmentStats:
    total = len(grades)
    assessments = [_role_assessment(grade, role) for grade in grades]
    assessment_counts = {value: assessments.count(value) for value in ASSESSMENTS}
    findings = [_finding(grade, role) for grade in grades]
    matched_trials = [
        _matching_trial(trials, role=role, case_id=grade.case_id, trial_number=grade.trial_number)
        for grade in grades
    ]

    analyst_missing = sum(
        1 for finding in findings if finding and finding.get("analyst_context_included") is False
    )
    analyst_unverified = sum(
        1 for finding in findings if finding and finding.get("analyst_context_included") is None
    )
    customer_none = sum(
        1 for finding in findings if finding and finding.get("customer_standard_coverage") == "none"
    )
    customer_partial = sum(
        1
        for finding in findings
        if finding and finding.get("customer_standard_coverage") == "partial"
    )
    private_none_or_partial = sum(
        1
        for finding in findings
        if finding and finding.get("private_context_coverage") in {"none", "partial"}
    )
    placeholder_present = sum(
        1 for trial in matched_trials if trial is not None and trial.placeholder_count >= 1
    )
    repeated_placeholder = sum(
        1 for trial in matched_trials if trial is not None and trial.placeholder_count >= 2
    )
    forced_completion = sum(
        1 for trial in matched_trials if trial is not None and trial.forced_completion
    )
    scope_material_drift = sum(
        1 for finding in findings if finding and finding.get("scope") == "material_drift"
    )

    return ModelAssessmentStats(
        total_trials=total,
        assessment_counts=assessment_counts,
        assessment_rates={key: _rate(value, total) for key, value in assessment_counts.items()},
        analyst_missing_rate=_rate(analyst_missing, total),
        analyst_unverified_rate=_rate(analyst_unverified, total),
        customer_none_rate=_rate(customer_none, total),
        customer_partial_rate=_rate(customer_partial, total),
        private_none_or_partial_rate=_rate(private_none_or_partial, total),
        placeholder_rate=_rate(placeholder_present, total),
        repeated_placeholder_rate=_rate(repeated_placeholder, total),
        forced_completion_rate=_rate(forced_completion, total),
        scope_material_drift_rate=_rate(scope_material_drift, total),
    )


def _bootstrap_ci(
    values: list[int], *, seed: int, iterations: int, alpha: float = 0.05
) -> tuple[float, float]:
    """Percentile bootstrap over the mean of arbitrary numeric per-trial values; stdlib only."""
    if not values:
        return (0.0, 0.0)
    rng = random.Random(seed)
    n = len(values)
    means = sorted(sum(values[rng.randrange(n)] for _ in range(n)) / n for _ in range(iterations))
    lower = int((alpha / 2) * iterations)
    upper = max(int((1 - alpha / 2) * iterations) - 1, lower)
    return (means[lower], means[upper])


def compute_standard_stats(
    result: EvaluationResult,
    metadata: dict[str, CaseMetadata],
    *,
    bootstrap_seed: int = STANDARD_BOOTSTRAP_SEED,
    bootstrap_iterations: int = STANDARD_BOOTSTRAP_ITERATIONS,
) -> StandardStats:
    case_ids = list(dict.fromkeys(grade.case_id for grade in result.grades))
    task_stats: list[TaskAssessmentStats] = []
    task_win_rates: list[float] = []
    all_outcomes: list[str] = []
    hard_failure_counts: dict[str, dict[str, int]] = {
        role: dict.fromkeys(HARD_FAILURE_REASONS, 0) for role in _ROLES
    }

    for case_id in case_ids:
        case_grades = sorted(
            (grade for grade in result.grades if grade.case_id == case_id),
            key=lambda grade: grade.seed if grade.seed is not None else grade.trial_number,
        )
        control_id = next(
            (trial.control_id for trial in result.trials if trial.case_id == case_id),
            "",
        )
        per_model = {
            role: _model_assessment_stats(case_grades, result.trials, role=role) for role in _ROLES
        }
        task_aggregate = {
            role: _aggregate_assessment([_role_assessment(grade, role) for grade in case_grades])
            for role in _ROLES
        }
        seed_consistent = {
            role: len({_role_assessment(grade, role) for grade in case_grades}) <= 1
            for role in _ROLES
        }

        paired_outcomes: dict[int, str] = {}
        for grade in case_grades:
            candidate_assessment = _role_assessment(grade, "candidate")
            comparison_assessment = _role_assessment(grade, "comparison")
            outcome = paired_outcome(candidate_assessment, comparison_assessment)
            if grade.seed is not None:
                paired_outcomes[grade.seed] = outcome
            all_outcomes.append(outcome)

            for role in _ROLES:
                finding = _finding(grade, role)
                trial = _matching_trial(
                    result.trials, role=role, case_id=grade.case_id, trial_number=grade.trial_number
                )
                if finding and finding.get("analyst_context_included") is False:
                    hard_failure_counts[role]["analyst_missing"] += 1
                if finding and finding.get("customer_standard_coverage") == "none":
                    hard_failure_counts[role]["customer_none"] += 1
                if trial is not None and trial.placeholder_count >= 2:
                    hard_failure_counts[role]["repeated_placeholder"] += 1

        task_win_rate = _rate(
            sum(1 for outcome in paired_outcomes.values() if outcome == "win"),
            len(paired_outcomes),
        )
        task_win_rates.append(task_win_rate)

        task_stats.append(
            TaskAssessmentStats(
                case_id=case_id,
                control_id=control_id,
                per_model=per_model,
                task_aggregate=task_aggregate,
                seed_consistent=seed_consistent,
                paired_outcomes=paired_outcomes,
            )
        )

    macro_assessment_rates = {
        role: {
            assessment: (
                sum(task.per_model[role].assessment_rates[assessment] for task in task_stats)
                / len(task_stats)
                if task_stats
                else 0.0
            )
            for assessment in ASSESSMENTS
        }
        for role in _ROLES
    }

    wins = all_outcomes.count("win")
    losses = all_outcomes.count("loss")
    ties = all_outcomes.count("tie")
    total_pairs = len(all_outcomes)
    win_indicators = [1 if outcome == "win" else 0 for outcome in all_outcomes]
    paired = PairedSummary(
        wins=wins,
        losses=losses,
        ties=ties,
        win_rate=_rate(wins, total_pairs),
        loss_rate=_rate(losses, total_pairs),
        tie_rate=_rate(ties, total_pairs),
        macro_task_win_rate=(sum(task_win_rates) / len(task_win_rates) if task_win_rates else 0.0),
        # A percentile bootstrap over the win/non-win indicator (not the raw
        # -1/0/+1 outcome), so this lands on the same 0-1 win-rate scale as
        # win_rate above rather than a differently-scaled net score.
        bootstrap_win_rate_ci=_bootstrap_ci(
            win_indicators, seed=bootstrap_seed, iterations=bootstrap_iterations
        ),
        bootstrap_iterations=bootstrap_iterations,
        bootstrap_seed=bootstrap_seed,
    )

    suite_version = next(iter(metadata.values())).suite_version if metadata else ""

    return StandardStats(
        suite_version=suite_version,
        task_stats=task_stats,
        macro_assessment_rates=macro_assessment_rates,
        paired=paired,
        hard_failure_counts=hard_failure_counts,
    )
