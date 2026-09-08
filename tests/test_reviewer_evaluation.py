"""Offline tests for the atomic reviewer-classification harness."""

import json

import pytest

from security_response_generator import reviewer_evaluation
from security_response_generator.reviewer_evaluation import (
    EVALUATION_REVIEW_SCHEMA,
    PROFILES,
    CritiqueRecord,
    ReviewerEvaluationInterrupted,
    ReviewerEvaluationResult,
    assemble_evaluation_review_messages,
    detection_grid,
    render_summary,
    run_evaluation,
    score_record,
    summarize_role,
)
from security_response_generator.reviewer_evaluation_scoring import EXPECTED_CLASSIFICATIONS

CASES = PROFILES["smoke"].load_cases()


@pytest.fixture(autouse=True)
def _no_real_models(monkeypatch):
    monkeypatch.setattr(reviewer_evaluation, "unload_models", lambda models: None)
    monkeypatch.setattr(reviewer_evaluation, "embed_query", lambda text: [0.0])
    monkeypatch.setattr(reviewer_evaluation, "load_model", lambda model: None)
    monkeypatch.setattr(
        reviewer_evaluation,
        "residency_snapshots",
        lambda models: {model: {"size_bytes": 1024**3} for model in models},
    )


def _reply_for(case_id, condition):
    return {
        "classification": EXPECTED_CLASSIFICATIONS[condition],
        "constructive_feedback": f"Human-only explanation for {case_id} {condition}.",
    }


def _review_stub(reply_for=_reply_for):
    seen = []

    def review(messages, response_format=None, *, model, seed, num_predict=None, on_response=None):
        payload = json.loads(messages[1]["content"])
        case = next(case for case in CASES if case.control_id == payload["control_id"])
        item = next(
            item
            for item in case.drafts
            if item.mock_generated_draft == payload["mock_generated_draft"]
        )
        seen.append((model, seed, case.id, item.condition, response_format, messages, num_predict))
        if on_response is not None:
            on_response({"model": model, "total_duration": 1_000_000_000})
        return json.dumps(reply_for(case.id, item.condition))

    review.seen = seen
    return review


def _run(tmp_path, review):
    return run_evaluation(
        "candidate:test",
        "comparison:test",
        tmp_path,
        critique=review,
    )


def test_run_covers_five_statements_per_case_for_both_reviewers(tmp_path):
    review = _review_stub()
    result = _run(tmp_path, review)
    expected = PROFILES["smoke"].item_count(CASES)
    assert expected == 10
    assert len(result.for_role("candidate")) == expected
    assert len(result.for_role("comparison")) == expected
    assert len(review.seen) == 20


def test_each_reviewer_uses_its_model_and_four_way_schema(tmp_path):
    review = _review_stub()
    _run(tmp_path, review)
    assert {model for model, *_ in review.seen} == {"candidate:test", "comparison:test"}
    assert all(call[4] == EVALUATION_REVIEW_SCHEMA for call in review.seen)
    assert EVALUATION_REVIEW_SCHEMA["required"] == ["classification"]
    assert EVALUATION_REVIEW_SCHEMA["properties"]["classification"]["enum"] == [
        "supported",
        "missing_required_information",
        "contradicts_requirement",
        "adds_unsupported_information",
    ]
    assert "constructive_feedback" in EVALUATION_REVIEW_SCHEMA["properties"]


def test_prompt_uses_one_requirement_and_one_statement(tmp_path):
    review = _review_stub()
    _run(tmp_path, review)
    messages = review.seen[0][5]
    instruction = messages[0]["content"]
    assert "Classify the relationship" in instruction
    assert "Use no outside knowledge and assess nothing else" in instruction
    assert "An unrelated statement is adds_unsupported_information" in instruction
    assert "constructive_feedback" in instruction
    payload = json.loads(messages[1]["content"])
    assert set(payload) == {"control_id", "grounding_information", "mock_generated_draft"}
    assert payload["grounding_information"].count(".") == 1
    assert payload["mock_generated_draft"].count(".") == 1
    assert "analyst" not in messages[1]["content"].casefold()


def test_generation_and_embedding_models_are_loaded_only_for_coexistence(tmp_path, monkeypatch):
    loaded = []
    embedded = []
    unloaded = []
    monkeypatch.setattr(reviewer_evaluation, "unload_models", unloaded.append)
    monkeypatch.setattr(reviewer_evaluation, "load_model", loaded.append)
    monkeypatch.setattr(reviewer_evaluation, "embed_query", embedded.append)
    _run(tmp_path, _review_stub())
    assert loaded and set(loaded) == {reviewer_evaluation.config.GENERATION_MODEL}
    assert embedded
    assert unloaded
    assert all(reviewer_evaluation.config.GENERATION_MODEL in models for models in unloaded)
    assert all(reviewer_evaluation.config.EMBEDDING_MODEL not in models for models in unloaded)


def test_failed_preparation_preserves_diagnostics(tmp_path, monkeypatch):
    monkeypatch.setattr(
        reviewer_evaluation,
        "unload_models",
        lambda models: (_ for _ in ()).throw(OSError("Ollama did not unload model(s): x")),
    )
    with pytest.raises(OSError, match="partial artifacts are in"):
        _run(tmp_path, _review_stub())
    run_dir = next(tmp_path.iterdir())
    payload = json.loads(run_dir.joinpath("results.json").read_text())
    assert payload["status"] == "failed"
    assert payload["incomplete_operation"]["stage"] == "preparation"
    assert "Ollama did not unload model(s): x" in run_dir.joinpath("ERROR.txt").read_text()


def test_reviewer_retention_keeps_newest_twenty_and_ignores_unrelated(tmp_path):
    created = []
    for index in range(22):
        run_dir = tmp_path / f"20260101_0000{index:02d}_model"
        run_dir.mkdir()
        run_dir.joinpath(reviewer_evaluation._RUN_MARKER).touch()
        created.append(run_dir)
    unrelated = tmp_path / "personal-notes"
    unrelated.mkdir()
    removed = reviewer_evaluation.prune_reviewer_evaluation_runs(tmp_path)
    assert len(removed) == 2
    assert not created[0].exists()
    assert not created[1].exists()
    assert all(path.exists() for path in created[2:])
    assert unrelated.is_dir()


def test_interrupt_preserves_completed_work(tmp_path):
    calls = []

    def interrupting(
        messages, response_format=None, *, model, seed, num_predict=None, on_response=None
    ):
        calls.append(model)
        if len(calls) == 5:
            raise KeyboardInterrupt
        return json.dumps({"classification": "supported"})

    with pytest.raises(ReviewerEvaluationInterrupted) as excinfo:
        _run(tmp_path, interrupting)
    payload = json.loads((excinfo.value.output_dir / "results.json").read_text())
    assert payload["status"] == "interrupted"
    assert len(payload["records"]) == 4


def test_expected_classifications_score_full_accuracy(tmp_path):
    result = _run(tmp_path, _review_stub())
    summary = summarize_role(result, "candidate", "candidate:test")
    assert summary.correct == summary.items == 10
    assert summary.incorrect == summary.invalid_decisions == 0


def test_constructive_feedback_never_affects_scoring(tmp_path):
    def wild_feedback(case_id, condition):
        return {
            "classification": EXPECTED_CLASSIFICATIONS[condition],
            "constructive_feedback": "Invented nonsense that must remain human-only.",
        }

    result = _run(tmp_path, _review_stub(wild_feedback))
    summary = summarize_role(result, "candidate", "candidate:test")
    assert summary.correct == 10
    worksheet = result.output_dir.joinpath("critiques.md").read_text()
    assert "Invented nonsense that must remain human-only." in worksheet
    assert "retained, not scored" in worksheet
    assert "Invented nonsense" not in render_summary(result)


def test_wrong_classification_is_counted_directly(tmp_path):
    result = _run(
        tmp_path,
        _review_stub(
            lambda case_id, condition: {
                "classification": "supported",
                "constructive_feedback": "Always the same.",
            }
        ),
    )
    summary = summarize_role(result, "candidate", "candidate:test")
    assert summary.correct == 2
    assert summary.incorrect == 8
    assert summary.distinctness == pytest.approx(0)


def test_invalid_outputs_are_excluded_from_distinctness(tmp_path):
    calls = 0

    def mixed(messages, response_format=None, *, model, seed, num_predict=None, on_response=None):
        nonlocal calls
        calls += 1
        return "not json" if calls % 2 else json.dumps({"classification": "supported"})

    result = _run(tmp_path, mixed)
    summary = summarize_role(result, "candidate", "candidate:test")
    assert summary.distinctness_samples == 5
    assert summary.distinctness == 0


def test_coexistence_peak_requires_all_three_models_to_be_resident(tmp_path, monkeypatch):
    monkeypatch.setattr(
        reviewer_evaluation,
        "residency_snapshots",
        lambda models: {
            model: (
                None if model == reviewer_evaluation.config.EMBEDDING_MODEL else {"size_bytes": 1}
            )
            for model in models
        },
    )
    summary = summarize_role(_run(tmp_path, _review_stub()), "candidate", "candidate:test")
    assert summary.coexistence_intact == 0
    assert summary.peak_coexistence_bytes is None
    assert "FAILED 0/10" in render_summary(_run(tmp_path, _review_stub()))


def test_every_call_has_a_token_ceiling(tmp_path):
    review = _review_stub()
    _run(tmp_path, review)
    assert all(call[6] == reviewer_evaluation.REVIEWER_MAX_TOKENS for call in review.seen)


def test_runaway_empty_and_schema_failures_are_reported(tmp_path):
    def broken(messages, response_format=None, *, model, seed, num_predict=None, on_response=None):
        if on_response is not None:
            on_response(
                {"model": model, "eval_count": num_predict, "message": {"thinking": "x" * 4700}}
            )
        return ""

    result = _run(tmp_path, broken)
    summary = summarize_role(result, "candidate", "candidate:test")
    assert summary.empty_outputs == summary.schema_failures == summary.items
    assert summary.token_ceiling_hits == summary.items
    assert summary.correct == 0
    assert summary.mean_thinking_chars == 4700
    assert summary.distinctness is None
    report = render_summary(result)
    assert "Token" in report
    assert "10/10" in report


def test_scoring_never_consults_a_model():
    case = CASES[0]
    item = case.drafts[2]
    record = CritiqueRecord(
        model="m",
        role="candidate",
        case_id=case.id,
        control_id=case.control_id,
        condition=item.condition,
        seed=42,
        raw=json.dumps({"classification": "contradicts_requirement"}),
        wall_seconds=0,
    )
    scored = score_record(record, case, item)
    assert scored.classification_correct
    assert scored.expected_classification == "contradicts_requirement"


def test_artifacts_preserve_inputs_decisions_and_unscored_feedback(tmp_path):
    result = _run(tmp_path, _review_stub())
    payload = json.loads((result.output_dir / "results.json").read_text())
    assert payload["suite_version"] == reviewer_evaluation.REVIEWER_SUITE_VERSION == 4
    assert payload["evaluation_cases"][0]["grounding_information"] == (
        CASES[0].grounding_information
    )
    assert all("raw" in record for record in payload["records"])
    worksheet = (result.output_dir / "critiques.md").read_text()
    assert "Authoritative requirement" in worksheet
    assert "Mock generated statement" in worksheet
    assert "Expected classification" in worksheet
    assert "Reviewer classification" in worksheet
    assert "Constructive feedback (retained, not scored)" in worksheet
    assert "Human-only explanation" in worksheet
    key = (result.output_dir / "answer-key.md").read_text()
    assert "`clean` → `supported`" in key
    assert "`unsupported_claim` → `adds_unsupported_information`" in key


def test_invalid_reviewer_output_is_preserved_for_human_diagnosis(tmp_path):
    result = _run(tmp_path, lambda *args, **kwargs: "not valid json")
    worksheet = result.output_dir.joinpath("critiques.md").read_text()
    assert "Invalid or empty reviewer response" in worksheet
    assert "Raw response (could not be rendered as a classification)" in worksheet
    assert "```text\nnot valid json\n```" in worksheet


def test_summary_is_classification_focused(tmp_path):
    summary = render_summary(_run(tmp_path, _review_stub()))
    assert "Classification accuracy" in summary
    assert "10/10" in summary
    assert "Correct" in summary
    assert "Wrong" in summary
    assert "Invalid" in summary
    assert "Classification detail" in summary
    assert "Only the four-way classification is scored" in summary
    assert "feedback" in summary
    assert "SERIOUS" not in summary
    assert "unusable reviewer fix" not in summary
    assert "Failure profile" not in summary


def test_interrupted_empty_result_still_renders(tmp_path):
    empty = ReviewerEvaluationResult(
        candidate_model="candidate:test",
        comparison_model="comparison:test",
        profile="smoke",
        output_dir=tmp_path,
        generation_model="generator:test",
        embedding_model="embedder:test",
        records=[],
        cases=[],
        status="interrupted",
    )
    summary = render_summary(empty)
    assert "Run status: interrupted" in summary
    assert "unknown" in summary


def test_classification_grid_includes_clean_and_expected_answer(tmp_path):
    rows = detection_grid(_run(tmp_path, _review_stub()))
    assert len(rows) == 10
    assert any(row["condition"] == "clean" and row["expected"] == "supported" for row in rows)


def test_atomic_prompt_is_built_from_frozen_corpus_without_retrieval():
    messages = assemble_evaluation_review_messages(CASES[0], CASES[0].drafts[0])
    payload = json.loads(messages[1]["content"])
    assert payload["control_id"] == "SI-5"
    assert payload["grounding_information"] == CASES[0].grounding_information
    assert payload["mock_generated_draft"] == CASES[0].drafts[0].mock_generated_draft


def test_only_smoke_profile_is_offered():
    assert set(PROFILES) == {"smoke"}
    assert reviewer_evaluation.DEFAULT_PROFILE == "smoke"


def test_result_has_no_generation_model_assessment(tmp_path):
    payload = json.loads((_run(tmp_path, _review_stub()).output_dir / "results.json").read_text())
    assert "grades" not in payload
    assert "trials" not in payload
    for record in payload["records"]:
        assert "assessment" not in record
        assert "reviewer_assessment" not in record
