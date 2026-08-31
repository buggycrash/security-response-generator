import json
import os
import re

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
            "human_review_focus": ["analyst fact coverage"],
        }
    )


def _analyst_reply(evidence: str = "completed draft") -> str:
    return json.dumps(
        {
            "included": True,
            "evidence_quote": evidence,
        }
    )


def _finding_with_verified_analyst() -> dict:
    finding = json.loads(_grade_reply())
    finding.update(
        {
            "analyst_context_included": True,
            "analyst_context_evidence": "completed draft",
            "analyst_context_evidence_verified": True,
            "analyst_context_check_reason": "Analyst context is present.",
        }
    )
    return finding


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


def test_smoke_profile_has_expected_compact_schedule():
    cases = model_evaluation.load_smoke_cases()

    assert [case.control_id for case in cases] == ["SI-5", "AC-2", "SC-8(1)"]
    assert len(model_evaluation.SMOKE_SCHEDULE) == 5
    assert [spec.phase for spec in model_evaluation.SMOKE_SCHEDULE] == [
        "cold",
        "warm",
        "warm",
        "quality",
        "quality",
    ]
    assert [spec.seed for spec in model_evaluation.SMOKE_SCHEDULE[:3]] == [42, 43, 44]
    assert all(case.rubric == cases[0].rubric for case in cases)
    rubric = " ".join(cases[0].rubric)
    assert "suggested evidence, not proof of implementation" in rubric
    assert "do not require or reward implementation specifics" in rubric
    assert not any(term in rubric for term in ("CISA", "TLS 1.3", "shared or group"))


def test_grader_instruction_requires_an_independent_absolute_assessment():
    instruction = " ".join(model_evaluation.GRADE_SYSTEM_INSTRUCTION.split())

    assert "Grounded does not automatically mean relevant" in instruction
    assert "no better than material_edits" in instruction
    assert "evaluate one fictional" in instruction.lower()
    assert "Evaluate this response independently" in instruction
    assert "Compare Response A" not in instruction
    assert "validation suggestions cannot supply or repair narrative coverage" in instruction
    assert "independent precheck handles analyst-context inclusion" in instruction
    assert "do not reassess it" in instruction
    assert "Classify customer-standard and private-context coverage independently" in instruction
    assert "None requires not_viable; partial requires material_edits or worse" in instruction
    assert "cannot alone make a response not_viable" in instruction
    assert "must not affect either source-coverage field" in instruction
    assert "must name the source field and the concrete material content" in instruction
    assert "Never request details absent from that source" in instruction
    assert "focused: all substantive content supports the exact control" in instruction
    assert "minor_drift: a small unnecessary detail" in instruction
    assert "material_drift: a substantial passage addresses another control" in instruction
    assert "specific offending content" in instruction
    assert "If the issues field cannot identify such content" in instruction

    inclusion_instruction = " ".join(model_evaluation.ANALYST_INCLUSION_SYSTEM_INSTRUCTION.split())
    assert "analyst-specific substance" in inclusion_instruction
    assert "context need not solve the control" in inclusion_instruction
    assert "Generic control language does not substitute" in inclusion_instruction
    assert "short verbatim quote copied from the narrative" in inclusion_instruction
    assert "no validations or other sources" in inclusion_instruction


def test_analyst_inclusion_prompt_excludes_validations_and_other_sources():
    case = model_evaluation.load_smoke_cases()[0]
    messages = model_evaluation._analyst_inclusion_messages(
        case,
        "The State SOC receives CISA alerts.\n[Validations]\nCISA screenshot.",
    )
    payload = json.loads(messages[1]["content"])

    assert set(payload) == {"analyst_context", "narrative"}
    assert payload["analyst_context"] == case.context
    assert payload["narrative"] == "The State SOC receives CISA alerts."
    assert "CISA screenshot" not in messages[1]["content"]
    assert "customer_standard" not in messages[1]["content"]


def test_analyst_inclusion_requires_a_verbatim_narrative_quote():
    narrative = "The State SOC receives CISA alerts through controlled channels."
    valid = model_evaluation._parse_analyst_inclusion(
        _analyst_reply("The State SOC receives CISA alerts"), narrative
    )
    invalid = model_evaluation._parse_analyst_inclusion(
        _analyst_reply("CISA appears only in a validation"), narrative
    )

    assert valid["analyst_context_included"] is True
    assert valid["analyst_context_evidence_verified"] is True
    assert invalid["analyst_context_included"] is None
    assert invalid["analyst_context_evidence_verified"] is False


def test_analyst_inclusion_preserves_an_explicit_missing_decision():
    raw = json.dumps(
        {
            "included": False,
            "evidence_quote": "",
        }
    )

    result = model_evaluation._parse_analyst_inclusion(raw, "Generic SI-5 language.")

    assert result["analyst_context_included"] is False
    assert result["analyst_context_evidence_verified"] is None


@pytest.mark.parametrize(
    ("value", "expected"),
    [("Gemma4:E4B", "gemma4:e4b"), ("llama3.1", "llama3.1:latest")],
)
def test_normalize_model_name(value, expected):
    assert model_evaluation.normalize_model_name(value) == expected


def test_preflight_reports_exact_pull_commands_for_missing_models(monkeypatch, tmp_path):
    monkeypatch.setattr(
        model_evaluation,
        "installed_model_names",
        lambda: ["default:latest", model_evaluation.config.EMBEDDING_MODEL],
    )

    with pytest.raises(ValueError) as exc_info:
        model_evaluation.validate_preflight(
            "candidate:latest", "default:latest", "grader:latest", tmp_path / "runs"
        )

    message = str(exc_info.value)
    assert "ollama pull candidate:latest" in message
    assert "ollama pull grader:latest" in message
    assert "ollama pull default:latest" not in message


def test_preflight_rejects_candidate_as_its_own_grader(tmp_path):
    with pytest.raises(ValueError, match="also the configured grader"):
        model_evaluation.validate_preflight(
            "candidate:latest", "default:latest", "candidate:latest", tmp_path / "runs"
        )


def test_unload_models_does_not_call_generate_for_absent_model(monkeypatch):
    generated = []

    class FakeClient:
        def generate(self, **kwargs):
            generated.append(kwargs)

    monkeypatch.setattr(model_evaluation, "_local_client", lambda: FakeClient())
    monkeypatch.setattr(model_evaluation, "_resident_process", lambda model: None)

    model_evaluation.unload_models(["not-loaded:latest"])

    assert generated == []


def test_unload_models_waits_for_ollama_ps_to_remove_process(monkeypatch):
    generated = []
    process_states = iter([object(), object(), None])

    class FakeClient:
        def generate(self, **kwargs):
            generated.append(kwargs)

    monkeypatch.setattr(model_evaluation, "_local_client", lambda: FakeClient())
    monkeypatch.setattr(model_evaluation, "_resident_process", lambda model: next(process_states))
    monkeypatch.setattr(model_evaluation.time, "sleep", lambda seconds: None)

    model_evaluation.unload_models(["loaded:latest"])

    assert generated == [{"model": "loaded:latest", "prompt": "", "keep_alive": 0}]


def test_residency_snapshots_poll_once_for_multiple_models(monkeypatch):
    ps_calls = 0

    class Process:
        def __init__(self, model, size, size_vram):
            self.model = model
            self.name = model
            self.size = size
            self.size_vram = size_vram
            self.context_length = 16384

    class Response:
        models = [
            Process("candidate:latest", 8 * 1024**3, 6 * 1024**3),
            Process("embedding:latest", 512 * 1024**2, 512 * 1024**2),
        ]

    class FakeClient:
        def ps(self):
            nonlocal ps_calls
            ps_calls += 1
            return Response()

    monkeypatch.setattr(model_evaluation, "_local_client", lambda: FakeClient())

    snapshots = model_evaluation.residency_snapshots(["candidate:latest", "embedding:latest"])

    assert ps_calls == 1
    assert snapshots["candidate:latest"]["size_bytes"] == 8 * 1024**3
    assert snapshots["candidate:latest"]["size_vram_bytes"] == 6 * 1024**3
    assert snapshots["embedding:latest"]["context_length"] == 16384


def test_smoke_run_writes_blinded_artifacts(monkeypatch, tmp_path):
    generated = []
    analyst_payloads = []
    analyst_options = []
    grading_payloads = []
    statuses = []
    monkeypatch.setattr(model_evaluation, "unload_models", lambda models: None)
    monkeypatch.setattr(model_evaluation, "embed_query", lambda text: [0.0])
    monkeypatch.setattr(
        model_evaluation,
        "residency_snapshots",
        _resident_snapshots,
    )

    def fake_review(messages, response_format=None, **kwargs):
        payload = json.loads(messages[1]["content"])
        if response_format == model_evaluation.ANALYST_INCLUSION_SCHEMA:
            analyst_payloads.append(payload)
            analyst_options.append(kwargs)
            evidence = next(
                line for line in payload["narrative"].splitlines() if "Fictional narrative" in line
            )
            return _analyst_reply(evidence)
        grading_payloads.append(payload)
        return _grade_reply()

    monkeypatch.setattr(model_evaluation, "review_messages", fake_review)

    def fake_generate(case, model, seed, phase):
        generated.append((case.id, model, seed))
        return model_evaluation.GenerationOutput(
            response_text=(
                f"# {case.control_id}\n\nFictional narrative for seed {seed}.\n"
                f"[Validations]\n\nEvidence for seed {seed}."
            ),
            model_calls=[{"model": model, "total_duration": 10}],
            forced_completion=False,
        )

    result = model_evaluation.run_smoke_evaluation(
        "candidate:latest",
        "default:latest",
        generate=fake_generate,
        output_root=tmp_path / "runs",
        on_status=statuses.append,
    )

    assert len(generated) == 10
    assert generated[:3] == [
        ("si5-context", "candidate:latest", 42),
        ("si5-context", "candidate:latest", 43),
        ("si5-context", "candidate:latest", 44),
    ]
    assert len(result.grades) == 5
    assert [grade.trial_number for grade in result.grades] == [1, 2, 3, 1, 1]
    assert len(analyst_payloads) == 10
    assert (
        analyst_options
        == [
            {
                "num_predict": model_evaluation.ANALYST_INCLUSION_MAX_TOKENS,
                "temperature": model_evaluation.ANALYST_INCLUSION_TEMPERATURE,
            }
        ]
        * 10
    )
    assert len(grading_payloads) == 10
    assert set(analyst_payloads[0]) == {"analyst_context", "narrative"}
    assert "[Validations]" not in analyst_payloads[0]["narrative"]
    assert "response_a" not in grading_payloads[0]
    assert "analyst_fact" not in grading_payloads[0]
    assert "[Validations]" not in grading_payloads[0]["response"]["narrative"]
    assert grading_payloads[0]["response"]["validations"].startswith("Evidence")
    assert grading_payloads[0] == grading_payloads[1]
    assert any("Grading SI-5 trial 1 Response A independently" in status for status in statuses)
    assert any(
        "Checking analyst context for SI-5 trial 1 Response A" in status for status in statuses
    )
    assert result.output_dir.joinpath("results.json").is_file()
    assert result.output_dir.joinpath("summary.txt").is_file()
    assert result.output_dir.joinpath("grader-findings.md").is_file()
    assert len(list(result.output_dir.joinpath("responses").glob("*.md"))) == 10

    worksheet = result.output_dir.joinpath("human-review.md").read_text()
    answer_key = result.output_dir.joinpath("answer-key.md").read_text()
    grader_findings = result.output_dir.joinpath("grader-findings.md").read_text()
    assert "Response A" in worksheet and "Response B" in worksheet
    assert "candidate:latest" not in worksheet
    assert "A = candidate" in answer_key
    assert "Automated independent grader findings" in grader_findings
    assert "Preference:" not in grader_findings
    assert "Human review focus:" in grader_findings
    assert "Analyst context included: True" in grader_findings
    assert "Analyst narrative evidence: Fictional narrative for seed 42." in grader_findings
    assert "Analyst evidence verified: True" in grader_findings
    assert "Customer standard coverage: full" in grader_findings
    assert "Private context coverage: full" in grader_findings
    assert "Explicit placeholders: 0" in grader_findings
    assert "Forced completion: no" in grader_findings
    summary = model_evaluation.render_summary(result)
    assert "SMOKE EVALUATION - NOT A MODEL-QUALIFICATION RESULT" in summary
    assert "candidate:latest" in summary
    assert "default:latest" in summary
    assert "Performance" in summary and "Automated independent review" in summary
    assert "Automated coverage and completeness checks" in summary
    assert "All cases" in summary and "clear" in summary
    assert "Human review priorities" in summary
    assert "None identified" in summary
    assert "human prose and style spot-checking is still required" in summary
    assert f"Human review:    {result.output_dir / 'human-review.md'}" in summary
    assert f"Answer key:      {result.output_dir / 'answer-key.md'}" in summary


def test_grading_policy_makes_missing_analyst_context_not_viable():
    parsed = _finding_with_verified_analyst()
    parsed["assessment"] = "material_edits"
    parsed["analyst_context_included"] = False
    case = model_evaluation.load_smoke_cases()[0]

    adjusted = model_evaluation._apply_finding_policy(parsed, case)

    assert adjusted["assessment"] == "not_viable"
    assert "mandatory analyst context was missing" in adjusted["policy_adjustment"]


def test_grading_policy_applies_customer_coverage_severity():
    case = model_evaluation.load_smoke_cases()[0]
    missing = _finding_with_verified_analyst()
    missing["customer_standard_coverage"] = "none"
    partial = _finding_with_verified_analyst()
    partial["customer_standard_coverage"] = "partial"

    missing_adjusted = model_evaluation._apply_finding_policy(missing, case)
    partial_adjusted = model_evaluation._apply_finding_policy(partial, case)

    assert missing_adjusted["assessment"] == "not_viable"
    assert "no supplied customer-standard requirements" in missing_adjusted["policy_adjustment"]
    assert partial_adjusted["assessment"] == "material_edits"
    assert "customer-standard coverage was partial" in partial_adjusted["policy_adjustment"]


def test_grading_policy_makes_missing_private_context_material_edits():
    parsed = _finding_with_verified_analyst()
    parsed["private_context_coverage"] = "none"
    case = model_evaluation.load_smoke_cases()[0]

    adjusted = model_evaluation._apply_finding_policy(parsed, case)

    assert adjusted["assessment"] == "material_edits"
    assert "private-context coverage was none" in adjusted["policy_adjustment"]


def test_grading_policy_does_not_penalize_sources_that_were_not_provided():
    parsed = _finding_with_verified_analyst()
    parsed["customer_standard_coverage"] = "none"
    parsed["private_context_coverage"] = "none"
    case = model_evaluation.EvaluationCase(
        id="no-optional-context",
        control_id="SC-8(1)",
        context="TLS protects information in transit.",
        customer_chunks=[],
        baseline_chunks=["Protect transmitted information."],
        private_chunks=[],
        rubric=["Evaluate source coverage independently."],
    )

    adjusted = model_evaluation._apply_finding_policy(parsed, case)

    assert adjusted["assessment"] == "viable"
    assert adjusted["customer_standard_coverage"] == "not_provided"
    assert adjusted["private_context_coverage"] == "not_provided"
    assert "policy_adjustment" not in adjusted


def test_explicit_placeholder_count_is_deterministic():
    response = """The word placeholder alone does not count.
[PLACEHOLDER: identify the owner]
[placeholder]
[Validations]
* Screenshot without a marker.
"""

    assert model_evaluation.count_placeholders(response) == 2


def test_one_explicit_placeholder_prevents_viable_assessment():
    finding = json.loads(_grade_reply())
    trial = model_evaluation.TrialRecord(
        role="candidate",
        model="candidate:latest",
        case_id="si5-context",
        control_id="SI-5",
        seed=42,
        phase="cold",
        wall_seconds=1.0,
        response_text="[PLACEHOLDER: owner]",
        model_calls=[],
        forced_completion=True,
        residency=None,
        embedding_residency=None,
        placeholder_count=1,
    )

    adjusted = model_evaluation._apply_completeness_policy(finding, trial)

    assert adjusted["assessment"] == "material_edits"
    assert "one explicit placeholder" in adjusted["completeness_adjustment"]


def test_multiple_explicit_placeholders_are_not_viable():
    finding = json.loads(_grade_reply())
    finding["assessment"] = "material_edits"
    trial = model_evaluation.TrialRecord(
        role="candidate",
        model="candidate:latest",
        case_id="si5-context",
        control_id="SI-5",
        seed=42,
        phase="cold",
        wall_seconds=1.0,
        response_text="[PLACEHOLDER: owner] [PLACEHOLDER: date]",
        model_calls=[],
        forced_completion=True,
        residency=None,
        embedding_residency=None,
        placeholder_count=2,
    )

    adjusted = model_evaluation._apply_completeness_policy(finding, trial)

    assert adjusted["assessment"] == "not_viable"
    assert "2 explicit placeholders" in adjusted["completeness_adjustment"]


def test_summary_surfaces_missing_facts_and_placeholders_before_human_review(tmp_path):
    candidate_trial = model_evaluation.TrialRecord(
        role="candidate",
        model="phi4-mini:latest",
        case_id="si5-context",
        control_id="SI-5",
        seed=42,
        phase="cold",
        wall_seconds=1.0,
        response_text="[PLACEHOLDER: owner] [PLACEHOLDER: date]",
        model_calls=[],
        forced_completion=True,
        residency=None,
        embedding_residency=None,
        placeholder_count=2,
    )
    comparison_trial = model_evaluation.TrialRecord(
        role="comparison",
        model="default:latest",
        case_id="si5-context",
        control_id="SI-5",
        seed=42,
        phase="cold",
        wall_seconds=1.0,
        response_text="complete",
        model_calls=[],
        forced_completion=False,
        residency=None,
        embedding_residency=None,
    )
    result = model_evaluation.EvaluationResult(
        candidate_model="phi4-mini:latest",
        comparison_model="default:latest",
        grader_model="grader:latest",
        profile="smoke",
        output_dir=tmp_path,
        trials=[candidate_trial, comparison_trial],
        grades=[
            model_evaluation.GradeRecord(
                case_id="si5-context",
                response_a_role="candidate",
                response_b_role="comparison",
                parsed={
                    "response_a": {
                        "assessment": "not_viable",
                        "analyst_context_included": False,
                        "customer_standard_coverage": "full",
                        "private_context_coverage": "partial",
                    },
                    "response_b": {
                        "assessment": "viable",
                        "analyst_context_included": True,
                        "customer_standard_coverage": "full",
                        "private_context_coverage": "full",
                    },
                },
                raw="{}",
            )
        ],
    )

    summary = model_evaluation.render_summary(result)

    assert summary.index("Automated coverage and completeness checks") < summary.index(
        "Human review priorities"
    )
    assert "phi4-mini:latest" in summary
    assert "missing" in summary
    assert "partial" in summary
    assert "2" in summary
    assert "yes" in summary
    assert "analyst=missing, customer=none" in summary


def test_generation_timing_excludes_residency_monitoring_delay():
    assert model_evaluation._net_generation_seconds(15.0, 4.5) == 10.5
    assert model_evaluation._net_generation_seconds(2.0, 3.0) == 0.0


def test_case_summary_aggregates_trials_conservatively(tmp_path):
    def grade(trial_number, candidate_assessment, comparison_assessment, preference):
        return model_evaluation.GradeRecord(
            case_id="si5-context",
            response_a_role="candidate",
            response_b_role="comparison",
            parsed={
                "response_a": {"assessment": candidate_assessment},
                "response_b": {"assessment": comparison_assessment},
                "preference": preference,
            },
            raw="{}",
            trial_number=trial_number,
        )

    result = model_evaluation.EvaluationResult(
        candidate_model="candidate:latest",
        comparison_model="default:latest",
        grader_model="grader:latest",
        profile="smoke",
        output_dir=tmp_path,
        trials=[],
        grades=[
            grade(1, "viable", "viable", "response_a"),
            grade(2, "material_edits", "viable", "response_a"),
            grade(3, "viable", "viable", "response_a"),
        ],
    )

    summary = model_evaluation._case_grade_summaries(result)[0]

    assert summary["aggregates"] == {
        "candidate": "material_edits",
        "comparison": "viable",
    }
    assert summary["preferred_role"] == "comparison"
    assert "2 viable, 1 material_edits" in model_evaluation.render_summary(result)


def test_case_preference_prioritizes_more_viable_trials_over_grader_votes(tmp_path):
    def grade(trial_number, comparison_assessment, preference):
        return model_evaluation.GradeRecord(
            case_id="si5-context",
            response_a_role="candidate",
            response_b_role="comparison",
            parsed={
                "response_a": {"assessment": "material_edits"},
                "response_b": {"assessment": comparison_assessment},
                "preference": preference,
            },
            raw="{}",
            trial_number=trial_number,
        )

    result = model_evaluation.EvaluationResult(
        candidate_model="llama3.1:8b",
        comparison_model="gemma4:e4b-it-qat",
        grader_model="gemma4:e2b-it-qat",
        profile="smoke",
        output_dir=tmp_path,
        trials=[],
        grades=[
            grade(1, "material_edits", "response_a"),
            grade(2, "material_edits", "response_a"),
            grade(3, "viable", "response_b"),
        ],
    )

    summary = model_evaluation._case_grade_summaries(result)[0]
    rendered = model_evaluation.render_summary(result)

    assert summary["aggregates"] == {
        "candidate": "material_edits",
        "comparison": "material_edits",
    }
    assert summary["preferred_role"] == "comparison"
    assert "Human review priorities" in rendered
    assert "llama3.1:8b" in rendered and "1, 2, 3 (material_edits)" in rendered
    assert "gemma4:e4b-it-qat" in rendered and "1, 2 (material_edits)" in rendered


def test_equal_independent_assessment_distributions_produce_a_tie(tmp_path):
    result = model_evaluation.EvaluationResult(
        candidate_model="candidate:latest",
        comparison_model="default:latest",
        grader_model="grader:latest",
        profile="smoke",
        output_dir=tmp_path,
        trials=[],
        grades=[
            model_evaluation.GradeRecord(
                case_id="si5-context",
                response_a_role="candidate",
                response_b_role="comparison",
                parsed={
                    "response_a": {"assessment": "viable"},
                    "response_b": {"assessment": "viable"},
                    # Older artifacts may contain this field; it must not affect
                    # preference after grading was made independent.
                    "preference": "response_a",
                },
                raw="{}",
            )
        ],
    )

    assert model_evaluation._case_grade_summaries(result)[0]["preferred_role"] == "tie"


def test_prune_evaluation_runs_keeps_newest_twenty_and_ignores_unrelated(tmp_path):
    output_root = tmp_path / "runs"
    output_root.mkdir()
    created = []
    for index in range(22):
        run_dir = output_root / f"20260101_0000{index:02d}_model"
        run_dir.mkdir()
        run_dir.joinpath("results.json").write_text("{}\n", encoding="utf-8")
        os.utime(run_dir, (index, index))
        created.append(run_dir)
    unrelated = output_root / "personal-notes"
    unrelated.mkdir()
    unrelated.joinpath("keep.txt").write_text("keep\n", encoding="utf-8")

    removed = model_evaluation.prune_evaluation_runs(output_root)

    assert len(removed) == 2
    assert not created[0].exists()
    assert not created[1].exists()
    assert all(path.exists() for path in created[2:])
    assert unrelated.joinpath("keep.txt").is_file()


def test_terminal_summary_colors_failures_and_only_offending_timings(monkeypatch, tmp_path):
    monkeypatch.delenv("NO_COLOR", raising=False)

    def trial(role, model, phase, seconds, memory_gib=3, gpu_gib=None):
        gpu_gib = memory_gib if gpu_gib is None else gpu_gib
        resident = {
            "model": model,
            "size_bytes": memory_gib * 1024**3,
            "size_vram_bytes": gpu_gib * 1024**3,
        }
        embedding = {
            "model": "embedding:latest",
            "size_bytes": 512 * 1024**2,
            "size_vram_bytes": 512 * 1024**2,
        }
        return model_evaluation.TrialRecord(
            role=role,
            model=model,
            case_id="si5-context",
            control_id="SI-5",
            seed=42,
            phase=phase,
            wall_seconds=seconds,
            response_text="draft",
            model_calls=[],
            forced_completion=False,
            residency=resident,
            embedding_residency=embedding,
        )

    result = model_evaluation.EvaluationResult(
        candidate_model="candidate:latest",
        comparison_model="default:latest",
        grader_model="grader:latest",
        profile="smoke",
        output_dir=tmp_path,
        trials=[
            trial("candidate", "candidate:latest", "cold", 76.0, 8, 6),
            trial("candidate", "candidate:latest", "warm", 41.0, 6),
            trial("candidate", "candidate:latest", "warm", 20.0, 7),
            trial("comparison", "default:latest", "cold", 30.0),
            trial("comparison", "default:latest", "warm", 20.0),
        ],
        grades=[
            model_evaluation.GradeRecord(
                case_id="si5-context",
                response_a_role="candidate",
                response_b_role="comparison",
                parsed={
                    "response_a": {"assessment": "not_viable"},
                    "response_b": {"assessment": "viable"},
                    "preference": "response_b",
                },
                raw="{}",
            )
        ],
    )

    colored = model_evaluation.render_summary(result, color=True)
    plain = model_evaluation.render_summary(result)

    assert "\x1b[" in colored
    assert "\x1b[1;38;5;166m76.0s\x1b[0m" in colored
    assert "\x1b[1;38;5;166m41.0s\x1b[0m" in colored
    assert re.search(r"\x1b\[1;31mFAIL\s*\x1b\[0m", colored)
    assert re.search(r"\x1b\[1;31mnot_viable\s*\x1b\[0m", colored)
    assert re.search(r"\x1b\[1;38;5;166m7\.00 GiB\s*\x1b\[0m", colored)
    assert re.search(r"\x1b\[1;38;5;166m8\.00 GiB\s*\x1b\[0m", colored)
    assert "\x1b[1;38;5;166mno" in colored
    assert "2/3 full" in plain
    assert "\x1b[1;38;5;166m20.0s\x1b[0m" not in colored
    assert "\x1b[" not in plain
    assert "Cold (<75s)" in plain
    assert "Warm runs (<40s)" in plain
    assert "Observed memory residency" in plain
    assert "process-table polling is excluded" in plain
    assert "does not score prose quality or writing style" in plain
    assert "Contradictory grader findings" in plain
    assert "Policy-adjusted trials" not in plain


def test_user_interrupt_preserves_completed_work_and_cleans_up_models(monkeypatch, tmp_path):
    unload_calls = []
    generation_calls = 0
    monkeypatch.setattr(
        model_evaluation, "unload_models", lambda models: unload_calls.append(models)
    )
    monkeypatch.setattr(model_evaluation, "embed_query", lambda text: [0.0])
    monkeypatch.setattr(
        model_evaluation,
        "residency_snapshots",
        _resident_snapshots,
    )

    def interrupt_second_trial(case, model, seed, phase):
        nonlocal generation_calls
        generation_calls += 1
        if generation_calls == 2:
            raise KeyboardInterrupt
        return model_evaluation.GenerationOutput(
            response_text="completed draft",
            model_calls=[],
            forced_completion=False,
        )

    with pytest.raises(model_evaluation.EvaluationInterrupted) as exc_info:
        model_evaluation.run_smoke_evaluation(
            "candidate:latest",
            "default:latest",
            generate=interrupt_second_trial,
            output_root=tmp_path / "runs",
        )

    output_dir = exc_info.value.output_dir
    saved = json.loads(output_dir.joinpath("results.json").read_text())
    assert saved["status"] == "interrupted"
    assert len(saved["trials"]) == 1
    assert saved["incomplete_operation"]["stage"] == "generation"
    assert saved["incomplete_operation"]["phase"] == "warm"
    assert "INCOMPLETE" in output_dir.joinpath("summary.txt").read_text()
    assert "Completed response trials: 1/10" in output_dir.joinpath("INTERRUPTED.txt").read_text()
    assert not output_dir.joinpath("ERROR.txt").exists()
    assert unload_calls[-1] == [
        "candidate:latest",
        "default:latest",
        model_evaluation.config.REVIEW_MODEL,
    ]


def test_grading_interrupt_preserves_each_completed_independent_call(monkeypatch, tmp_path):
    grader_calls = 0
    monkeypatch.setattr(model_evaluation, "unload_models", lambda models: None)
    monkeypatch.setattr(model_evaluation, "embed_query", lambda text: [0.0])
    monkeypatch.setattr(model_evaluation, "residency_snapshots", _resident_snapshots)

    def fake_generate(case, model, seed, phase):
        return model_evaluation.GenerationOutput(
            response_text="completed draft",
            model_calls=[],
            forced_completion=False,
        )

    def interrupt_third_grader_call(messages, response_format=None, **kwargs):
        nonlocal grader_calls
        grader_calls += 1
        if grader_calls == 3:
            raise KeyboardInterrupt
        if response_format == model_evaluation.ANALYST_INCLUSION_SCHEMA:
            return _analyst_reply()
        return _grade_reply()

    monkeypatch.setattr(model_evaluation, "review_messages", interrupt_third_grader_call)

    with pytest.raises(model_evaluation.EvaluationInterrupted) as exc_info:
        model_evaluation.run_smoke_evaluation(
            "candidate:latest",
            "default:latest",
            generate=fake_generate,
            output_root=tmp_path / "runs",
        )

    output_dir = exc_info.value.output_dir
    saved = json.loads(output_dir.joinpath("results.json").read_text())
    assert saved["grades"][0]["parsed"]["response_a"]["assessment"] == "viable"
    assert saved["grades"][0]["parsed"]["response_b"] is None
    assert list(saved["grades"][0]["raw"]) == ["response_a"]
    assert list(saved["grades"][0]["analyst_checks"]) == ["response_a"]
    interrupted = output_dir.joinpath("INTERRUPTED.txt").read_text()
    assert "Completed grader calls: 2/20" in interrupted
    assert '"stage": "analyst_inclusion"' in interrupted
    assert '"blinded_response": "response_b"' in interrupted


def test_model_ejection_hard_fails_and_preserves_completed_trial(monkeypatch, tmp_path):
    unload_calls = []
    monkeypatch.setattr(
        model_evaluation, "unload_models", lambda models: unload_calls.append(models)
    )
    monkeypatch.setattr(model_evaluation, "embed_query", lambda text: [0.0])
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

    def fake_generate(case, model, seed, phase):
        return model_evaluation.GenerationOutput(
            response_text="completed before ejection detection",
            model_calls=[],
            forced_completion=False,
        )

    with pytest.raises(OSError, match="Ollama ejected required model"):
        model_evaluation.run_smoke_evaluation(
            "candidate:latest",
            "default:latest",
            generate=fake_generate,
            output_root=tmp_path / "runs",
        )

    output_dir = next((tmp_path / "runs").iterdir())
    saved = json.loads(output_dir.joinpath("results.json").read_text())
    assert saved["status"] == "failed"
    assert len(saved["trials"]) == 1
    assert saved["trials"][0]["response_text"] == "completed before ejection detection"
    assert model_evaluation.config.EMBEDDING_MODEL in output_dir.joinpath("ERROR.txt").read_text()
    assert unload_calls[-1] == [
        "candidate:latest",
        "default:latest",
        model_evaluation.config.REVIEW_MODEL,
    ]


def test_smoke_run_preserves_partial_artifacts_on_model_failure(monkeypatch, tmp_path):
    calls = {"count": 0}
    monkeypatch.setattr(model_evaluation, "unload_models", lambda models: None)
    monkeypatch.setattr(model_evaluation, "embed_query", lambda text: [0.0])
    monkeypatch.setattr(model_evaluation, "residency_snapshots", _resident_snapshots)

    def fail_after_one(case, model, seed, phase):
        calls["count"] += 1
        if calls["count"] == 2:
            raise ConnectionError("runner stopped")
        return model_evaluation.GenerationOutput(
            response_text="first completed response",
            model_calls=[],
            forced_completion=False,
        )

    with pytest.raises(OSError, match="partial artifacts"):
        model_evaluation.run_smoke_evaluation(
            "candidate:latest",
            "default:latest",
            generate=fail_after_one,
            output_root=tmp_path / "runs",
        )

    run_dirs = list((tmp_path / "runs").iterdir())
    assert len(run_dirs) == 1
    assert run_dirs[0].joinpath("ERROR.txt").is_file()
    responses = list(run_dirs[0].joinpath("responses").glob("*.md"))
    assert len(responses) == 1
    assert "first completed response" in responses[0].read_text()
