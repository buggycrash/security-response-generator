from typer.testing import CliRunner

from security_response_generator import cli

runner = CliRunner()


class FakeCollection:
    def __init__(self, count: int = 5):
        self._count = count

    def count(self) -> int:
        return self._count


def _patch_common(monkeypatch, knowledge_base_count: int = 5):
    demo = cli.engagements.Engagement("demo", "DEMO", cli.config.ENGAGEMENTS_DIR / "demo")
    monkeypatch.setattr(cli, "_active_engagement_or_exit", lambda: demo)
    monkeypatch.setattr(
        cli,
        "_build_collections",
        lambda engagement: {
            cli.config.COLLECTION_KNOWLEDGE_BASE: FakeCollection(knowledge_base_count),
            cli.config.COLLECTION_CUSTOMER_STANDARDS: object(),
            cli.config.COLLECTION_PRIVATE_CONTEXT: object(),
        },
    )


def _fake_generate(
    control_id,
    context,
    collections,
    engagement,
    output_format,
    max_followups,
    review,
    *,
    on_retrieval_timing=None,
    on_embed_response=None,
    on_generation_response=None,
    on_review_response=None,
):
    if on_retrieval_timing is not None:
        from security_response_generator.generation.retrieval import RetrievalTiming

        on_retrieval_timing(
            RetrievalTiming(
                embedding_seconds=0.01,
                chroma_seconds=0.02,
                chroma_seconds_by_collection={
                    cli.config.COLLECTION_KNOWLEDGE_BASE: 0.02,
                    cli.config.COLLECTION_CUSTOMER_STANDARDS: 0.0,
                    cli.config.COLLECTION_PRIVATE_CONTEXT: 0.0,
                },
            )
        )
    if on_embed_response is not None:
        on_embed_response({"model": "embeddinggemma", "load_duration": 0, "total_duration": 0})
    if on_generation_response is not None:
        on_generation_response(
            {
                "model": "gemma4:e4b-it-qat",
                "load_duration": 0,
                "total_duration": 100_000_000,
                "eval_count": 50,
            }
        )
    if review and on_review_response is not None:
        on_review_response(
            {"model": "gemma4:e2b-it-qat", "load_duration": 0, "total_duration": 100_000_000}
        )
    return cli.ControlGenerationResult(
        response_text="Customer: DEMO\n\nbody", has_baseline_match=True, forced_completion=False
    )


def test_benchmark_runs_control_and_prints_report(monkeypatch):
    _patch_common(monkeypatch)
    monkeypatch.setattr(cli, "_generate_control_response", _fake_generate)

    result = runner.invoke(cli.app, ["benchmark", "SI-5"])

    assert result.exit_code == 0, result.output
    assert "SI-5" in result.output
    assert "Collection load" in result.output
    assert "generation: draft" in result.output
    assert "Summary" in result.output


def test_benchmark_review_flag_includes_review_calls(monkeypatch):
    _patch_common(monkeypatch)
    monkeypatch.setattr(cli, "_generate_control_response", _fake_generate)

    result = runner.invoke(cli.app, ["benchmark", "SI-5", "--review"])

    assert result.exit_code == 0, result.output
    assert "review: pass 1" in result.output


def test_benchmark_no_review_by_default(monkeypatch):
    _patch_common(monkeypatch)
    monkeypatch.setattr(cli, "_generate_control_response", _fake_generate)

    result = runner.invoke(cli.app, ["benchmark", "SI-5"])

    assert result.exit_code == 0, result.output
    assert "review: pass 1" not in result.output


def test_benchmark_iterations_runs_control_multiple_times(monkeypatch):
    _patch_common(monkeypatch)
    calls = []

    def counting_generate(
        control_id, context, collections, engagement, output_format, max_followups, review, **kwargs
    ):
        calls.append(control_id)
        return _fake_generate(
            control_id,
            context,
            collections,
            engagement,
            output_format,
            max_followups,
            review,
            **kwargs,
        )

    monkeypatch.setattr(cli, "_generate_control_response", counting_generate)

    result = runner.invoke(cli.app, ["benchmark", "SI-5", "--iterations", "3"])

    assert result.exit_code == 0, result.output
    assert calls == ["SI-5", "SI-5", "SI-5"]


def test_benchmark_multiple_control_ids(monkeypatch):
    _patch_common(monkeypatch)
    monkeypatch.setattr(cli, "_generate_control_response", _fake_generate)

    result = runner.invoke(cli.app, ["benchmark", "SI-5", "AC-2"])

    assert result.exit_code == 0, result.output
    assert "SI-5" in result.output
    assert "AC-2" in result.output


def test_benchmark_forces_noninteractive_max_followups(monkeypatch):
    _patch_common(monkeypatch)
    captured = {}

    def capturing_generate(
        control_id, context, collections, engagement, output_format, max_followups, review, **kwargs
    ):
        captured["max_followups"] = max_followups
        return _fake_generate(
            control_id,
            context,
            collections,
            engagement,
            output_format,
            max_followups,
            review,
            **kwargs,
        )

    monkeypatch.setattr(cli, "_generate_control_response", capturing_generate)

    result = runner.invoke(cli.app, ["benchmark", "SI-5"])

    assert result.exit_code == 0, result.output
    assert captured["max_followups"] == 0


def test_benchmark_rejects_empty_knowledge_base(monkeypatch):
    _patch_common(monkeypatch, knowledge_base_count=0)
    monkeypatch.setattr(
        cli,
        "_generate_control_response",
        lambda *a, **kw: (_ for _ in ()).throw(AssertionError("should not be called")),
    )

    result = runner.invoke(cli.app, ["benchmark", "SI-5"])

    assert result.exit_code == 1
    assert "srg ingest" in result.output


def test_benchmark_aborts_on_systemic_error_and_prints_partial_report(monkeypatch):
    _patch_common(monkeypatch)
    calls = {"count": 0}

    def failing_generate(
        control_id, context, collections, engagement, output_format, max_followups, review, **kwargs
    ):
        calls["count"] += 1
        if calls["count"] == 2:
            raise ConnectionError("Ollama unreachable")
        return _fake_generate(
            control_id,
            context,
            collections,
            engagement,
            output_format,
            max_followups,
            review,
            **kwargs,
        )

    monkeypatch.setattr(cli, "_generate_control_response", failing_generate)

    result = runner.invoke(cli.app, ["benchmark", "SI-5", "--iterations", "2"])

    assert result.exit_code == 1
    assert "Aborted" in result.output
    # partial report for the first successful run should still be printed
    assert "SI-5" in result.output
