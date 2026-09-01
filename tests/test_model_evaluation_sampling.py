import random

from security_response_generator import model_evaluation
from security_response_generator import model_evaluation_sampling as sampling


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


def _grade(case_id, seed, candidate_assessment, comparison_assessment, **kwargs):
    return model_evaluation.GradeRecord(
        case_id=case_id,
        response_a_role="candidate",
        response_b_role="comparison",
        parsed={
            "response_a": _finding(candidate_assessment, **kwargs.get("candidate_overrides", {})),
            "response_b": _finding(comparison_assessment, **kwargs.get("comparison_overrides", {})),
        },
        raw="{}",
        trial_number=[42, 43, 44].index(seed) + 1,
        seed=seed,
    )


def _result(grades):
    return model_evaluation.EvaluationResult(
        candidate_model="candidate:latest",
        comparison_model="comparison:latest",
        grader_model="grader:latest",
        profile="standard",
        output_dir="/tmp/unused",
        trials=[],
        grades=grades,
    )


def _many_task_grades(num_tasks=10, seeds=(42, 43, 44), assessment="viable"):
    grades = []
    for task_index in range(num_tasks):
        case_id = f"task-{task_index}"
        for seed in seeds:
            grades.append(_grade(case_id, seed, assessment, assessment))
    return grades


def test_sample_is_capped_at_max_size_even_with_many_flagged_pairs():
    # Every pair disagrees, so an uncapped rule set would select all 30.
    grades = _many_task_grades(num_tasks=10, assessment="not_viable")
    for grade in grades:
        grade.parsed["response_b"] = _finding("viable")

    manifest = sampling.build_human_review_sample(_result(grades), max_sample_size=5)

    assert len(manifest.selected) == 5
    assert len(manifest.excluded) == 25


def test_flagged_pairs_are_selected_before_unflagged_ones():
    grades = _many_task_grades(num_tasks=8, assessment="viable")
    flagged_case = "task-3"
    for grade in grades:
        if grade.case_id == flagged_case and grade.seed == 42:
            grade.parsed["response_a"] = _finding("not_viable")

    manifest = sampling.build_human_review_sample(_result(grades), max_sample_size=5)

    selected_keys = {(pair.case_id, pair.seed) for pair in manifest.selected}
    assert (flagged_case, 42) in selected_keys
    flagged_pair = next(p for p in manifest.selected if p.case_id == flagged_case and p.seed == 42)
    assert "high_risk_finding" in flagged_pair.reasons


def test_sample_spreads_across_distinct_tasks_before_repeating_one():
    grades = _many_task_grades(num_tasks=8, assessment="viable")
    # Flag two pairs each in three different tasks: 6 flagged pairs total, cap of 5.
    flagged_cases = ["task-0", "task-1", "task-2"]
    for grade in grades:
        if grade.case_id in flagged_cases and grade.seed in (42, 43):
            grade.parsed["response_a"] = _finding("not_viable")

    manifest = sampling.build_human_review_sample(_result(grades), max_sample_size=5)

    selected_tasks = {pair.case_id for pair in manifest.selected}
    # All three flagged tasks should appear before any task gets a second pick.
    assert flagged_cases[0] in selected_tasks
    assert flagged_cases[1] in selected_tasks
    assert flagged_cases[2] in selected_tasks


def test_fills_remaining_slots_with_random_spot_checks_when_few_pairs_flagged():
    grades = _many_task_grades(num_tasks=10, assessment="viable")
    grades[0].parsed["response_a"] = _finding("not_viable")

    manifest = sampling.build_human_review_sample(_result(grades), max_sample_size=5)

    assert len(manifest.selected) == 5
    spot_checks = [p for p in manifest.selected if "random_spot_check" in p.reasons]
    assert len(spot_checks) == 4


def test_small_result_selects_everything_without_padding():
    grades = _many_task_grades(num_tasks=2, assessment="viable")  # 6 total pairs

    manifest = sampling.build_human_review_sample(_result(grades), max_sample_size=5)

    assert len(manifest.selected) == 5
    assert len(manifest.excluded) == 1


def test_sampling_is_deterministic_across_repeated_calls():
    grades = _many_task_grades(num_tasks=10)
    result = _result(grades)

    first = sampling.build_human_review_sample(result)
    second = sampling.build_human_review_sample(result)

    assert first.selected == second.selected
    assert first.excluded == second.excluded


def test_sampling_does_not_touch_global_random_state():
    grades = _many_task_grades(num_tasks=10)
    result = _result(grades)

    before = random.getstate()
    sampling.build_human_review_sample(result)
    after = random.getstate()

    assert before == after
