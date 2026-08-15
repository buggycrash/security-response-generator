from rich.console import Console

from security_response_generator import benchmark


def test_model_call_timing_converts_nanoseconds_to_seconds():
    response = {
        "model": "gemma4:e4b-it-qat",
        "total_duration": 2_000_000_000,
        "load_duration": 500_000_000,
        "prompt_eval_duration": 300_000_000,
        "eval_duration": 1_200_000_000,
        "prompt_eval_count": 1000,
        "eval_count": 200,
    }

    timing = benchmark.ModelCallTiming.from_response("generation: draft", response)

    assert timing.model == "gemma4:e4b-it-qat"
    assert timing.total_seconds == 2.0
    assert timing.load_seconds == 0.5
    assert timing.prompt_eval_seconds == 0.3
    assert timing.eval_seconds == 1.2
    assert timing.prompt_eval_count == 1000
    assert timing.eval_count == 200


def test_model_call_timing_handles_missing_fields_for_embed_responses():
    response = {"model": "embeddinggemma", "total_duration": 100_000_000, "load_duration": 0}

    timing = benchmark.ModelCallTiming.from_response("embedding", response)

    assert timing.eval_seconds is None
    assert timing.eval_count is None
    assert timing.prompt_eval_seconds is None
    assert timing.prompt_eval_count is None


def test_likely_cold_load_thresholds():
    cold = benchmark.ModelCallTiming.from_response("x", {"load_duration": 2_000_000_000})
    warm = benchmark.ModelCallTiming.from_response("x", {"load_duration": 10_000_000})
    unknown = benchmark.ModelCallTiming.from_response("x", {})

    assert cold.likely_cold_load is True
    assert warm.likely_cold_load is False
    assert unknown.likely_cold_load is None


def _call(total_seconds: float, label: str = "generation: draft") -> benchmark.ModelCallTiming:
    return benchmark.ModelCallTiming(
        label=label,
        model="m",
        total_seconds=total_seconds,
        load_seconds=0.0,
        prompt_eval_seconds=0.0,
        eval_seconds=0.0,
        prompt_eval_count=0,
        eval_count=0,
    )


def test_control_run_timing_other_seconds_is_the_residual():
    run = benchmark.ControlRunTiming(
        control_id="SI-5",
        iteration=1,
        wall_seconds=10.0,
        embedding=None,
        retrieval_embedding_seconds=1.0,
        retrieval_chroma_seconds=0.5,
        retrieval_chroma_by_collection={},
        model_calls=[_call(5.0)],
        forced_completion=False,
        has_baseline_match=True,
    )

    assert run.other_seconds == 10.0 - 1.0 - 0.5 - 5.0


def test_control_run_timing_other_seconds_floors_at_zero():
    # accounted time can exceed wall time slightly due to callback timing overlap --
    # the residual must never go negative.
    run = benchmark.ControlRunTiming(
        control_id="SI-5",
        iteration=1,
        wall_seconds=1.0,
        embedding=None,
        retrieval_embedding_seconds=1.0,
        retrieval_chroma_seconds=1.0,
        retrieval_chroma_by_collection={},
        model_calls=[_call(5.0)],
        forced_completion=False,
        has_baseline_match=True,
    )

    assert run.other_seconds == 0.0


def test_recorder_labels_generation_and_review_calls_in_order():
    recorder = benchmark.Recorder()

    recorder.on_generation_response({"model": "gen", "load_duration": 0})
    recorder.on_review_response({"model": "rev", "load_duration": 0})
    recorder.on_generation_response({"model": "gen", "load_duration": 0})
    recorder.on_review_response({"model": "rev", "load_duration": 0})
    recorder.on_generation_response({"model": "gen", "load_duration": 0})

    labels = [call.label for call in recorder.model_calls]
    assert labels == [
        "generation: draft",
        "review: pass 1",
        "generation: revision (pass 1)",
        "review: pass 2",
        "generation: revision (pass 2)",
    ]


def test_recorder_labels_extra_generation_call_before_any_review():
    # A forced-completion or blank-response retry can add an extra generation call
    # before review even starts -- the labeling must still make sense in that case.
    recorder = benchmark.Recorder()

    recorder.on_generation_response({"model": "gen", "load_duration": 0})
    recorder.on_generation_response({"model": "gen", "load_duration": 0})
    recorder.on_review_response({"model": "rev", "load_duration": 0})

    labels = [call.label for call in recorder.model_calls]
    assert labels == ["generation: draft", "generation: revision (pass 1)", "review: pass 1"]


def test_build_run_timing_assembles_from_recorder():
    recorder = benchmark.Recorder()
    recorder.on_retrieval_timing(
        benchmark.RetrievalTiming(
            embedding_seconds=0.1,
            chroma_seconds=0.2,
            chroma_seconds_by_collection={"knowledge_base": 0.2},
        )
    )
    recorder.on_embed_response({"model": "embeddinggemma", "load_duration": 0})
    recorder.on_generation_response({"model": "gen", "load_duration": 0, "total_duration": 0})

    run = benchmark.build_run_timing(
        "SI-5", 1, 5.0, recorder, forced_completion=True, has_baseline_match=True
    )

    assert run.control_id == "SI-5"
    assert run.iteration == 1
    assert run.wall_seconds == 5.0
    assert run.embedding is not None
    assert run.retrieval_embedding_seconds == 0.1
    assert run.retrieval_chroma_seconds == 0.2
    assert run.retrieval_chroma_by_collection == {"knowledge_base": 0.2}
    assert len(run.model_calls) == 1
    assert run.forced_completion is True
    assert run.has_baseline_match is True


def test_build_run_timing_handles_no_retrieval_or_embed_callbacks():
    recorder = benchmark.Recorder()

    run = benchmark.build_run_timing(
        "SI-5", 1, 1.0, recorder, forced_completion=False, has_baseline_match=True
    )

    assert run.embedding is None
    assert run.retrieval_embedding_seconds == 0.0
    assert run.retrieval_chroma_seconds == 0.0
    assert run.retrieval_chroma_by_collection == {}


def test_render_report_smoke_test_includes_key_labels():
    report = benchmark.BenchmarkReport(collection_load_seconds=0.05)
    recorder = benchmark.Recorder()
    recorder.on_retrieval_timing(
        benchmark.RetrievalTiming(
            embedding_seconds=0.1,
            chroma_seconds=0.2,
            chroma_seconds_by_collection={"knowledge_base": 0.2},
        )
    )
    recorder.on_generation_response(
        {
            "model": "gemma4:e4b-it-qat",
            "total_duration": 1_000_000_000,
            "load_duration": 0,
            "eval_count": 100,
        }
    )
    report.runs.append(
        benchmark.build_run_timing(
            "SI-5", 1, 2.0, recorder, forced_completion=False, has_baseline_match=True
        )
    )

    console = Console(record=True, width=200)
    benchmark.render_report(report, console)
    output = console.export_text()

    assert "SI-5" in output
    assert "Chroma: knowledge_base" in output
    assert "generation: draft" in output
    assert "Summary" in output
    assert "No evidence of repeated cold loads" in output


def test_print_findings_flags_cold_load_after_warm_model_reuse():
    report = benchmark.BenchmarkReport(collection_load_seconds=0.0)
    recorder = benchmark.Recorder()
    # First generation call: warm (small load_duration).
    recorder.on_generation_response(
        {"model": "gemma4:e4b-it-qat", "load_duration": 10_000_000, "total_duration": 0}
    )
    # Reviewer call in between, different model.
    recorder.on_review_response(
        {"model": "gemma4:e2b-it-qat", "load_duration": 10_000_000, "total_duration": 0}
    )
    # Second generation call to the SAME model now shows a cold-sized load -- this is
    # the thrashing signal.
    recorder.on_generation_response(
        {"model": "gemma4:e4b-it-qat", "load_duration": 3_000_000_000, "total_duration": 0}
    )
    report.runs.append(
        benchmark.build_run_timing(
            "SI-5", 1, 10.0, recorder, forced_completion=False, has_baseline_match=True
        )
    )

    console = Console(record=True, width=200)
    benchmark.render_report(report, console)
    output = console.export_text()

    assert "may be evicting/reloading" in output
    assert "OLLAMA_MAX_LOADED_MODELS" in output
