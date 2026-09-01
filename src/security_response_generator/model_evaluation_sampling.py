"""Deterministic blinded human-review sampling for the standard evaluation profile.

Human review is a small spot-check, not an exhaustive audit: the sample is
capped at a realistic number of documents a person will actually read. It
prioritizes the pairs most likely to need a human's judgment (disagreements
and flagged findings) and spreads them across as many different tasks as
possible before falling back to a random spot check.

Selection never touches Python's global `random` module state: every call
uses a local `random.Random` instance seeded explicitly, so the manifest is
reproducible given identical inputs and a fixed seed.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from itertools import zip_longest

from security_response_generator.model_evaluation import (
    EvaluationResult,
    GradeRecord,
    _role_assessment,
)

STANDARD_SAMPLING_SEED = 20260831
STANDARD_MAX_SAMPLE_SIZE = 5

RULE_DESCRIPTION = (
    f"Blinded human-review sample is capped at {STANDARD_MAX_SAMPLE_SIZE} response "
    "pairs, since a person will not realistically read more than a handful. Pairs "
    "where the two models' automated assessments disagreed, or where either "
    "response was flagged (not_viable, missing or unverified analyst context, or a "
    "contradictory grader adjustment), are selected first and spread across as many "
    "different tasks as possible. If flagged pairs do not fill the sample, remaining "
    "slots are filled with a deterministic random spot check of the unflagged pairs, "
    "using a locally seeded random-number generator. Every pair not selected is "
    "recorded as excluded, not discarded silently."
)


@dataclass
class SampledPair:
    case_id: str
    seed: int
    candidate_trial_number: int
    comparison_trial_number: int
    reasons: list[str]


@dataclass
class SamplingManifest:
    rule_description: str
    rng_seed: int
    selected: list[SampledPair]
    excluded: list[tuple[str, int]]


def _finding(grade: GradeRecord, role: str) -> dict | None:
    if grade.parsed is None:
        return None
    label = "response_a" if grade.response_a_role == role else "response_b"
    finding = grade.parsed.get(label)
    return finding if isinstance(finding, dict) else None


def _is_high_risk(grade: GradeRecord) -> bool:
    for role in ("candidate", "comparison"):
        finding = _finding(grade, role)
        if finding is None:
            continue
        if finding.get("assessment") == "not_viable":
            return True
        if finding.get("analyst_context_included") in (False, None):
            return True
        if finding.get("policy_adjustment") or finding.get("completeness_adjustment"):
            return True
    return False


def _sampled_pair(grade: GradeRecord, reasons: list[str]) -> SampledPair:
    trial_number = grade.trial_number
    return SampledPair(
        case_id=grade.case_id,
        seed=grade.seed if grade.seed is not None else trial_number,
        candidate_trial_number=trial_number,
        comparison_trial_number=trial_number,
        reasons=list(reasons),
    )


def _round_robin_by_case(grades: list[GradeRecord]) -> list[GradeRecord]:
    """Interleave grades across case_id so no single task dominates a short list."""
    buckets: dict[str, list[GradeRecord]] = {}
    for grade in grades:
        buckets.setdefault(grade.case_id, []).append(grade)
    interleaved = []
    for group in zip_longest(*buckets.values()):
        interleaved.extend(grade for grade in group if grade is not None)
    return interleaved


def build_human_review_sample(
    result: EvaluationResult,
    *,
    seed: int = STANDARD_SAMPLING_SEED,
    max_sample_size: int = STANDARD_MAX_SAMPLE_SIZE,
) -> SamplingManifest:
    grades = sorted(
        (grade for grade in result.grades if grade.seed is not None),
        key=lambda grade: (grade.case_id, grade.seed),
    )

    reason_map: dict[tuple[str, int], list[str]] = {}
    for grade in grades:
        key = (grade.case_id, grade.seed)
        reasons = []
        if _role_assessment(grade, "candidate") != _role_assessment(grade, "comparison"):
            reasons.append("automated_disagreement")
        if _is_high_risk(grade):
            reasons.append("high_risk_finding")
        if reasons:
            reason_map[key] = reasons

    # Most informative pairs first (both flags beat one flag), spread across tasks.
    flagged = [grade for grade in grades if (grade.case_id, grade.seed) in reason_map]
    flagged.sort(key=lambda grade: -len(reason_map[(grade.case_id, grade.seed)]))
    flagged = _round_robin_by_case(flagged)

    selected_grades = flagged[:max_sample_size]
    selected_keys = {(grade.case_id, grade.seed) for grade in selected_grades}

    remaining_slots = max_sample_size - len(selected_grades)
    if remaining_slots > 0:
        unflagged = [grade for grade in grades if (grade.case_id, grade.seed) not in selected_keys]
        rng = random.Random(seed)
        shuffled = unflagged[:]
        rng.shuffle(shuffled)
        fill = _round_robin_by_case(shuffled)[:remaining_slots]
        for grade in fill:
            reason_map[(grade.case_id, grade.seed)] = ["random_spot_check"]
        selected_grades = selected_grades + fill
        selected_keys |= {(grade.case_id, grade.seed) for grade in fill}

    excluded = [
        (grade.case_id, grade.seed)
        for grade in grades
        if (grade.case_id, grade.seed) not in selected_keys
    ]

    selected_grades.sort(key=lambda grade: (grade.case_id, grade.seed))
    selected = [
        _sampled_pair(grade, reason_map[(grade.case_id, grade.seed)]) for grade in selected_grades
    ]

    return SamplingManifest(
        rule_description=RULE_DESCRIPTION,
        rng_seed=seed,
        selected=selected,
        excluded=excluded,
    )
