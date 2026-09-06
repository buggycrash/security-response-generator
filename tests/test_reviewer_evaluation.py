"""Reviewer-evaluation harness, driven entirely offline.

Every model call is stubbed: no Ollama daemon, no downloaded weights.
"""

import json

import pytest

from security_response_generator import reviewer_evaluation
from security_response_generator.reviewer_evaluation import (
    CLEAN_CONDITION,
    PROFILES,
    CritiqueRecord,
    ReviewerEvaluationInterrupted,
    ReviewerEvaluationResult,
    case_prompt,
    detection_grid,
    render_summary,
    run_evaluation,
    score_record,
    summarize_role,
)

CASES = PROFILES["smoke"].load_cases()


@pytest.fixture(autouse=True)
def _no_real_models(monkeypatch):
    """Neutralize every Ollama touchpoint the run loop makes."""
    monkeypatch.setattr(reviewer_evaluation, "unload_models", lambda models: None)
    monkeypatch.setattr(reviewer_evaluation, "embed_query", lambda text: [0.0])
    monkeypatch.setattr(reviewer_evaluation, "load_model", lambda model: None)
    monkeypatch.setattr(
        reviewer_evaluation,
        "residency_snapshots",
        lambda models: {model: {"size_bytes": 1024**3} for model in models},
    )


def _critique_stub(text_for=lambda case_id, condition: "No changes are needed."):
    """Build a stub that answers based on which draft it was handed."""
    seen = []

    def critique(
        messages, response_format=None, *, model, seed, num_predict=None, on_response=None
    ):
        payload = messages[1]["content"]
        case_id = "si5-alert-handling" if "SI-5" in payload else "ra5-scan-scope"
        condition = next(
            (
                item.condition
                for case in CASES
                if case.id == case_id
                for item in case.drafts
                if item.draft_json in payload
            ),
            CLEAN_CONDITION,
        )
        seen.append((model, seed, case_id, condition))
        if on_response is not None:
            on_response({"model": model, "total_duration": 1_000_000_000})
        return json.dumps({"critique": text_for(case_id, condition)})

    critique.seen = seen
    return critique


def _run(tmp_path, critique):
    return run_evaluation(
        "candidate:test",
        "comparison:test",
        "GENERATOR INSTRUCTIONS",
        tmp_path,
        critique=critique,
    )


# --- Run loop ---------------------------------------------------------------


def test_run_covers_every_draft_for_both_reviewers(tmp_path):
    critique = _critique_stub()
    result = _run(tmp_path, critique)

    expected = PROFILES["smoke"].item_count(CASES)
    assert len(result.for_role("candidate")) == expected
    assert len(result.for_role("comparison")) == expected
    assert len(critique.seen) == expected * 2


def test_each_reviewer_is_called_with_its_own_model_name(tmp_path):
    critique = _critique_stub()
    _run(tmp_path, critique)
    assert {model for model, *_ in critique.seen} == {"candidate:test", "comparison:test"}


def test_the_reviewer_receives_the_production_prompt_shape(tmp_path):
    """The reviewer's prompt embeds the generator's own instructions verbatim."""
    captured = {}

    def critique(
        messages, response_format=None, *, model, seed, num_predict=None, on_response=None
    ):
        captured.setdefault("messages", messages)
        return json.dumps({"critique": "No changes are needed."})

    _run(tmp_path, critique)
    system, user = captured["messages"]
    assert system["role"] == "system"
    assert "quality reviewer" in system["content"]
    assert "GENERATOR SYSTEM INSTRUCTIONS AND ANALYST FACTS:" in user["content"]
    assert "DRAFT RESPONSE (structured JSON):" in user["content"]
    assert "GENERATOR INSTRUCTIONS" in user["content"]


def test_the_generation_and_embedding_models_are_made_resident(tmp_path, monkeypatch):
    """Coexistence is measured, not estimated, so both must really be loaded."""
    loaded = []
    embedded = []
    monkeypatch.setattr(reviewer_evaluation, "load_model", loaded.append)
    monkeypatch.setattr(reviewer_evaluation, "embed_query", lambda text: embedded.append(text))
    _run(tmp_path, _critique_stub())
    assert loaded and all(model == reviewer_evaluation.config.GENERATION_MODEL for model in loaded)
    assert embedded


def test_an_interrupt_preserves_the_completed_work(tmp_path):
    calls = []

    def critique(
        messages, response_format=None, *, model, seed, num_predict=None, on_response=None
    ):
        calls.append(model)
        if len(calls) == 5:
            raise KeyboardInterrupt
        return json.dumps({"critique": "Add the missing detail."})

    with pytest.raises(ReviewerEvaluationInterrupted) as excinfo:
        _run(tmp_path, critique)

    payload = json.loads((excinfo.value.output_dir / "results.json").read_text())
    assert payload["status"] == "interrupted"
    assert len(payload["records"]) == 4
    assert payload["incomplete_operation"]["stage"] == "critique"


# --- Scoring integration ----------------------------------------------------


def test_a_silent_reviewer_detects_nothing_and_stays_restrained(tmp_path):
    result = _run(tmp_path, _critique_stub())
    summary = summarize_role(result, "candidate", "candidate:test")

    assert summary.detected == 0
    assert summary.clean_directives == 0
    assert summary.clean_silent == summary.clean_items
    assert summary.hard_false_alarms == 0


def test_a_reviewer_that_names_each_seeded_defect_scores_full_detection(tmp_path):
    answers = {
        "dropped_analyst_fact": {
            "si5-alert-handling": "Add the missing CISA alert detail.",
            "ra5-scan-scope": "State the weekly scan cadence explicitly.",
        },
        "wrong_customer_parameter": {
            "si5-alert-handling": "Correct 72 hours to match the standard.",
            "ra5-scan-scope": "Correct 45 days to match the standard.",
        },
        "unsupported_claim": {
            "si5-alert-handling": "Remove the Example ThreatFeed Pro claim.",
            "ra5-scan-scope": "Remove the Example Asset Discovery claim.",
        },
        "wrong_control_content": {
            "si5-alert-handling": "Remove the incident response paragraph.",
            "ra5-scan-scope": "Remove the annual risk assessment paragraph.",
        },
        "omitted_control_clause": {
            "si5-alert-handling": "Add that the issuing organization is notified.",
            "ra5-scan-scope": "Add that results are shared with designated personnel.",
        },
        "weak_validation": {
            "si5-alert-handling": "Replace the access review screenshot.",
            "ra5-scan-scope": "Replace the penetration test screenshot.",
        },
        "narrative_validations": {
            "si5-alert-handling": "Remove the validation heading from the narrative.",
            "ra5-scan-scope": "Remove the validation heading from the narrative.",
        },
    }
    result = _run(
        tmp_path,
        _critique_stub(
            lambda case_id, condition: answers.get(condition, {}).get(
                case_id, "No changes are needed."
            )
        ),
    )
    summary = summarize_role(result, "candidate", "candidate:test")
    assert summary.detected == summary.detectable
    assert summary.detection_rate == pytest.approx(1.0)


def test_a_hostile_reviewer_is_penalized_for_attacking_a_clean_draft(tmp_path):
    """The granite failure mode, and the reason the clean condition exists."""
    result = _run(
        tmp_path,
        _critique_stub(lambda case_id, condition: "Correct the 24 hours review window."),
    )
    summary = summarize_role(result, "candidate", "candidate:test")

    si5_clean = next(
        record
        for record in result.for_role("candidate")
        if record.case_id == "si5-alert-handling" and record.condition == CLEAN_CONDITION
    )
    assert si5_clean.hard_false_alarms
    assert summary.hard_false_alarms > 0
    assert summary.clean_silent == 0


def test_a_constant_reviewer_is_caught_by_distinctness_not_by_any_item(tmp_path):
    result = _run(tmp_path, _critique_stub(lambda case_id, condition: "Add more detail."))
    summary = summarize_role(result, "candidate", "candidate:test")
    assert summary.distinctness == pytest.approx(0.0)


def test_detection_is_undefined_rather_than_false_on_clean_drafts(tmp_path):
    result = _run(tmp_path, _critique_stub())
    clean = [
        record for record in result.for_role("candidate") if record.condition == CLEAN_CONDITION
    ]
    assert clean and all(record.detected is None for record in clean)


def test_every_critique_call_carries_a_token_ceiling(tmp_path):
    """Without one, a reviewer that fails to stop hangs the entire batch.

    Observed with phi4-mini: 59,000 tokens over 15m49s on a normal draft,
    Ollama shifting the context window rather than ever returning.
    """
    seen = []

    def critique(
        messages, response_format=None, *, model, seed, num_predict=None, on_response=None
    ):
        seen.append(num_predict)
        return json.dumps({"critique": "No changes are needed."})

    _run(tmp_path, critique)
    assert seen and all(value == reviewer_evaluation.REVIEWER_MAX_TOKENS for value in seen)


def test_a_reviewer_that_never_stops_is_recorded_as_a_finding(tmp_path):
    def critique(
        messages, response_format=None, *, model, seed, num_predict=None, on_response=None
    ):
        if on_response is not None:
            on_response({"model": model, "eval_count": num_predict})
        return json.dumps({"critique": "Add detail. " * 200})

    result = _run(tmp_path, critique)
    summary = summarize_role(result, "candidate", "candidate:test")
    assert summary.token_ceiling_hits == summary.items
    assert "Ran to token ceiling" in render_summary(result)


def test_a_healthy_critique_is_not_flagged_as_runaway(tmp_path):
    def critique(
        messages, response_format=None, *, model, seed, num_predict=None, on_response=None
    ):
        if on_response is not None:
            on_response({"model": model, "eval_count": 381})
        return json.dumps({"critique": "Add the missing detail."})

    result = _run(tmp_path, critique)
    assert summarize_role(result, "candidate", "candidate:test").token_ceiling_hits == 0


def test_an_empty_response_is_never_scored_as_restraint(tmp_path):
    """A thinking model can burn the whole token budget and return nothing.

    Ollama bills hidden reasoning against num_predict but reports only content
    in eval_count, so this looks like a reviewer that calmly requested no
    changes. Crediting it would reward the failure and invert the comparison --
    which is exactly what happened to gemma4:e2b-it-qat on 10 of 16 drafts.
    """

    def critique(
        messages, response_format=None, *, model, seed, num_predict=None, on_response=None
    ):
        if on_response is not None:
            on_response(
                {"model": model, "eval_count": num_predict, "message": {"thinking": "x" * 4700}}
            )
        return ""

    result = _run(tmp_path, critique)
    summary = summarize_role(result, "candidate", "candidate:test")

    assert summary.empty_outputs == summary.items
    assert summary.clean_silent == 0
    assert summary.mean_thinking_chars == 4700
    assert "No output at all" in render_summary(result)


def test_a_reviewer_that_says_nothing_is_needed_does_earn_restraint(tmp_path):
    """The affirmative case, to prove the empty-output rule is not overbroad."""
    result = _run(tmp_path, _critique_stub())
    summary = summarize_role(result, "candidate", "candidate:test")

    assert summary.empty_outputs == 0
    assert summary.clean_silent == summary.clean_items


def test_hidden_reasoning_is_recorded_even_though_it_never_reaches_the_generator(tmp_path):
    def critique(
        messages, response_format=None, *, model, seed, num_predict=None, on_response=None
    ):
        if on_response is not None:
            on_response({"model": model, "eval_count": 200, "message": {"thinking": "y" * 900}})
        return json.dumps({"critique": "Add the missing detail."})

    result = _run(tmp_path, critique)
    assert summarize_role(result, "candidate", "candidate:test").mean_thinking_chars == 900
    assert "Avg hidden reasoning" in render_summary(result)


def test_the_token_ceiling_leaves_room_for_a_deliberative_reviewer(tmp_path):
    """gemma4:e2b-it-qat needs ~1,400 tokens of reasoning before its first
    content token; a ceiling at or below that starves it into silence."""
    assert reviewer_evaluation.REVIEWER_MAX_TOKENS >= 3072


def test_malformed_reviewer_output_is_counted_not_fatal(tmp_path):
    def critique(
        messages, response_format=None, *, model, seed, num_predict=None, on_response=None
    ):
        return "this is not JSON"

    result = _run(tmp_path, critique)
    summary = summarize_role(result, "candidate", "candidate:test")
    assert summary.schema_failures == summary.items


def test_scoring_never_consults_a_model():
    """No LLM grades the reviewer; that would reintroduce the measured defect."""
    case = CASES[0]
    item = next(draft for draft in case.drafts if draft.condition == "wrong_customer_parameter")
    record = CritiqueRecord(
        model="m",
        role="candidate",
        case_id=case.id,
        control_id=case.control_id,
        condition=item.condition,
        seed=42,
        raw=json.dumps({"critique": "Correct 72 hours to 24 hours."}),
        critique="Correct 72 hours to 24 hours.",
        wall_seconds=0.0,
    )
    assert score_record(record, case, item).detected is True


# --- Reporting --------------------------------------------------------------


def test_results_json_keeps_every_critique_verbatim(tmp_path):
    result = _run(tmp_path, _critique_stub(lambda case_id, condition: f"Add {condition} detail."))
    payload = json.loads((result.output_dir / "results.json").read_text())
    critiques = {record["critique"] for record in payload["records"]}
    assert any("dropped_analyst_fact" in critique for critique in critiques)
    assert payload["candidate_model"] == "candidate:test"


def test_the_worksheet_pairs_each_critique_with_its_seeded_defect(tmp_path):
    result = _run(tmp_path, _critique_stub(lambda case_id, condition: f"Add {condition} detail."))
    worksheet = (result.output_dir / "critiques.md").read_text()
    assert "### Condition: wrong_customer_parameter" in worksheet
    assert "Add wrong_customer_parameter detail." in worksheet
    assert "hard false positive" in worksheet


def test_the_answer_key_names_every_condition(tmp_path):
    result = _run(tmp_path, _critique_stub())
    key = (result.output_dir / "answer-key.md").read_text()
    for condition in reviewer_evaluation.CONDITIONS:
        assert f"`{condition}`" in key


def test_the_summary_reports_every_measurement_area(tmp_path):
    result = _run(tmp_path, _critique_stub())
    summary = render_summary(result)
    for heading in ("Bottom line", "Cost", "Invention", "Detection", "Containment", "Consistency"):
        assert heading in summary
    assert "critique quality only" in summary


def test_invention_is_reported_before_detection(tmp_path):
    """Leading with detection invites ranking reviewers by defects found,
    which is backwards for a role where the generator applies every demand."""
    summary = render_summary(_run(tmp_path, _critique_stub()))
    assert summary.index("Invention:") < summary.index("Detection:")


def test_the_summary_states_the_asymmetry_before_any_table(tmp_path):
    summary = render_summary(_run(tmp_path, _critique_stub()))
    assert summary.index("Invention is disqualifying") < summary.index("Bottom line")
    assert "Do not trade invention for detection." in summary


def test_the_summary_states_the_smoke_scale_caveat(tmp_path):
    summary = render_summary(_run(tmp_path, _critique_stub()))
    assert "one observation per defect" in summary
    assert "uncalibrated" in summary


def test_a_run_interrupted_before_any_critique_still_renders(tmp_path):
    """An immediate Ctrl-C leaves zero records; the summary must not divide by it."""
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


def test_detection_grid_excludes_the_clean_condition(tmp_path):
    result = _run(tmp_path, _critique_stub())
    conditions = {row["condition"] for row in detection_grid(result)}
    assert CLEAN_CONDITION not in conditions
    assert len(conditions) == len(reviewer_evaluation.CONDITIONS) - 1


def test_older_runs_are_pruned_but_this_one_survives(tmp_path):
    first = _run(tmp_path, _critique_stub())
    second = _run(tmp_path, _critique_stub())
    assert first.output_dir.exists()
    assert second.output_dir.exists()


# --- Isolation from the generation-model harness ----------------------------


def test_the_prompt_is_rebuilt_from_the_frozen_corpus_without_retrieval():
    prompt = case_prompt(CASES[0], "INSTRUCTIONS")
    assert prompt.system == "INSTRUCTIONS"
    assert "Control ID: SI-5" in prompt.user
    assert "Analyst-Provided Facts (Must Use)" in prompt.user


def test_only_the_smoke_profile_is_offered():
    """A standard profile waits until the metrics are shown to discriminate."""
    assert set(PROFILES) == {"smoke"}
    assert reviewer_evaluation.DEFAULT_PROFILE == "smoke"


def test_the_result_carries_no_generation_model_assessment(tmp_path):
    """This command measures reviewers; it must not emit evaluate-model fields."""
    result = _run(tmp_path, _critique_stub())
    payload = json.loads((result.output_dir / "results.json").read_text())
    assert "grades" not in payload
    assert "trials" not in payload
    for record in payload["records"]:
        assert "assessment" not in record
        assert "reviewer_assessment" not in record


def test_summaries_are_computed_per_reviewer(tmp_path):
    result = _run(
        tmp_path,
        _critique_stub(lambda case_id, condition: "Add the missing CISA alert detail."),
    )
    assert isinstance(result, ReviewerEvaluationResult)
    candidate = summarize_role(result, "candidate", "candidate:test")
    comparison = summarize_role(result, "comparison", "comparison:test")
    assert candidate.items == comparison.items
    assert candidate.model != comparison.model


# --- The asymmetry between invention and omission ---------------------------


def test_unwarranted_changes_counts_everything_beyond_the_seeded_defect(tmp_path):
    """Exact ground truth is what makes this measurable: each draft carries at
    most one defect, so one demand is warranted at most."""
    result = _run(
        tmp_path,
        _critique_stub(lambda case_id, condition: "Add one thing. Remove another thing."),
    )
    summary = summarize_role(result, "candidate", "candidate:test")

    # Two directives per draft over 16 drafts, minus one per correctly found defect.
    assert summary.unwarranted_changes == 2 * summary.items - summary.detected


def test_a_reviewer_that_invents_more_ranks_worse_despite_finding_more(tmp_path):
    """The ordering this whole report exists to make obvious."""
    finds_everything_but_invents = (
        "Add the missing CISA alert detail. Remove the timeframe. Rewrite the heading. "
        "Correct the owner. Replace the validations."
    )

    def critique(
        messages, response_format=None, *, model, seed, num_predict=None, on_response=None
    ):
        text = (
            finds_everything_but_invents if model == "candidate:test" else "No changes are needed."
        )
        return json.dumps({"critique": text})

    result = _run(tmp_path, critique)
    inventive = summarize_role(result, "candidate", "candidate:test")
    quiet = summarize_role(result, "comparison", "comparison:test")

    assert inventive.detected > quiet.detected
    assert inventive.unwarranted_changes > quiet.unwarranted_changes
    assert inventive.unwarranted_per_find > quiet.unwarranted_per_find


def test_inventing_while_finding_nothing_is_the_worst_case(tmp_path):
    result = _run(tmp_path, _critique_stub(lambda case_id, condition: "Rewrite the heading."))
    summary = summarize_role(result, "candidate", "candidate:test")

    assert summary.detected == 0
    assert summary.unwarranted_changes > 0
    assert summary.unwarranted_per_find == float("inf")
    assert "invented, found nothing" in render_summary(result)


def test_a_silent_reviewer_is_useless_but_not_scored_as_the_worst(tmp_path):
    """Finding nothing while demanding nothing is harmless, not catastrophic.

    Dividing by zero finds would otherwise put a quiet reviewer level with one
    that invents freely.
    """
    summary = summarize_role(_run(tmp_path, _critique_stub()), "candidate", "candidate:test")

    assert summary.detected == 0
    assert summary.unwarranted_changes == 0
    assert summary.unwarranted_per_find == 0.0


def test_truncated_json_is_salvaged_so_syntax_is_not_scored_as_prose(tmp_path):
    """A critique cut off at the ceiling leaves an unterminated object.

    Scoring the brace, field name and escape sequences as critique text
    inflates the directive count and overstates how aggressive the reviewer was.
    """
    body = "Add the missing detail.\\nRemove the extra clause."

    def critique(
        messages, response_format=None, *, model, seed, num_predict=None, on_response=None
    ):
        return '{\n  "critique": "' + body

    result = _run(tmp_path, critique)
    record = result.for_role("candidate")[0]

    assert record.schema_failed, "the truncation must still be recorded as a failure"
    assert not record.critique.startswith("{")
    assert record.critique == "Add the missing detail.\nRemove the extra clause."
    assert record.directive_count == 2
