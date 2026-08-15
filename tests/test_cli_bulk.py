import json

import ollama
import pytest
from typer.testing import CliRunner

from security_response_generator import cli
from security_response_generator.generation.retrieval import RetrievalResult, RetrievedChunk

runner = CliRunner()


@pytest.fixture(autouse=True)
def _skip_review_pipeline_in_legacy_bulk_tests(monkeypatch):
    monkeypatch.setattr(cli, "_review_and_revise", lambda prompt, messages, draft, **kwargs: draft)


def _baseline_chunk(chunk_id: str = "doc.md::0") -> RetrievedChunk:
    return RetrievedChunk(text="baseline text", source_path="doc.md", chunk_id=chunk_id)


def _matched_result() -> RetrievalResult:
    return RetrievalResult(
        customer_chunks=[],
        baseline_chunks=[_baseline_chunk()],
        private_chunks=[],
        baseline_exact_match=True,
        customer_exact_match=False,
    )


def _unmatched_result() -> RetrievalResult:
    return RetrievalResult(
        customer_chunks=[],
        baseline_chunks=[],
        private_chunks=[],
        baseline_exact_match=False,
        customer_exact_match=False,
    )


def _final_reply(text: str) -> str:
    return json.dumps(
        {"needs_info": False, "question": None, "response": text, "validations": ["Screenshot."]}
    )


def _followup_reply(question: str) -> str:
    return json.dumps(
        {"needs_info": True, "question": question, "response": None, "validations": None}
    )


class FakeCollection:
    def __init__(self, count: int = 5):
        self._count = count

    def count(self) -> int:
        return self._count


def _patch_bulk_common(monkeypatch, retrieval_results, chat_replies, knowledge_base_count=5):
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

    results_by_control = dict(retrieval_results)
    monkeypatch.setattr(
        cli,
        "retrieve_for_control",
        lambda control_id, context, collections, **kwargs: results_by_control[control_id],
    )

    replies_by_control = dict(chat_replies)

    def fake_chat_messages(messages, response_format=None, *, on_response=None):
        control_id = replies_by_control["_current"]
        replies = replies_by_control[control_id]
        return replies.pop(0)

    monkeypatch.setattr(cli, "chat_messages", fake_chat_messages)
    return replies_by_control


def _write_csv(tmp_path, rows, name="controls.csv"):
    lines = ["Control ID,User added context"]
    lines.extend(f"{control_id},{context}" for control_id, context in rows)
    path = tmp_path / name
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_bulk_generate_happy_path_writes_one_file_per_row(monkeypatch, tmp_path):
    csv_path = _write_csv(tmp_path, [("AC-2", "notes a"), ("SI-5", "notes b")])
    output_dir = tmp_path / "out"

    retrieval_results = {"AC-2": _matched_result(), "SI-5": _matched_result()}
    replies_by_control = _patch_bulk_common(
        monkeypatch,
        retrieval_results,
        {"AC-2": [_final_reply("AC-2 body")], "SI-5": [_final_reply("SI-5 body")]},
    )

    def track_current(control_id, context, collections, **kwargs):
        replies_by_control["_current"] = control_id
        return retrieval_results[control_id]

    monkeypatch.setattr(cli, "retrieve_for_control", track_current)
    reviewed = []

    def fake_review(prompt, messages, draft, **kwargs):
        reviewed.append(replies_by_control["_current"])
        return draft

    monkeypatch.setattr(cli, "_review_and_revise", fake_review)

    result = runner.invoke(cli.app, ["bulk-generate", str(csv_path), "-o", str(output_dir)])

    assert result.exit_code == 0, result.output
    written = sorted(output_dir.glob("demo_*.md"))
    assert len(written) == 2
    assert any("AC-2 body" in path.read_text() for path in written)
    assert any("SI-5 body" in path.read_text() for path in written)
    assert reviewed == ["AC-2", "SI-5"]
    assert "2 clean, 0 with notes, 2 total" in result.output


def test_bulk_generate_notes_unmatched_control_and_continues(monkeypatch, tmp_path):
    csv_path = _write_csv(tmp_path, [("ZZ-99", ""), ("AC-2", "")])
    output_dir = tmp_path / "out"

    retrieval_results = {"ZZ-99": _unmatched_result(), "AC-2": _matched_result()}
    replies_by_control = _patch_bulk_common(
        monkeypatch, retrieval_results, {"AC-2": [_final_reply("AC-2 body")]}
    )

    def track_current(control_id, context, collections, **kwargs):
        replies_by_control["_current"] = control_id
        return retrieval_results[control_id]

    monkeypatch.setattr(cli, "retrieve_for_control", track_current)

    result = runner.invoke(cli.app, ["bulk-generate", str(csv_path), "-o", str(output_dir)])

    assert result.exit_code == 0, result.output
    written = sorted(output_dir.glob("demo_*.md"))
    assert len(written) == 2
    zz_file = next(path for path in written if "ZZ-99" in path.name)
    assert "No matching NIST baseline content found for control ID 'ZZ-99'" in zz_file.read_text()
    assert "1 clean, 1 with notes, 2 total" in result.output


def test_bulk_generate_never_prompts_and_notes_suppressed_question(monkeypatch, tmp_path):
    csv_path = _write_csv(tmp_path, [("AC-2", "")])
    output_dir = tmp_path / "out"

    retrieval_results = {"AC-2": _matched_result()}
    replies_by_control = _patch_bulk_common(
        monkeypatch,
        retrieval_results,
        {"AC-2": [_followup_reply("what SIEM?"), _final_reply("best effort body")]},
    )

    def track_current(control_id, context, collections, **kwargs):
        replies_by_control["_current"] = control_id
        return retrieval_results[control_id]

    monkeypatch.setattr(cli, "retrieve_for_control", track_current)

    def fail_prompt(*args, **kwargs):
        raise AssertionError("bulk-generate must never prompt interactively")

    monkeypatch.setattr(cli.typer, "prompt", fail_prompt)

    result = runner.invoke(cli.app, ["bulk-generate", str(csv_path), "-o", str(output_dir)])

    assert result.exit_code == 0, result.output
    written = list(output_dir.glob("demo_*.md"))
    assert len(written) == 1
    assert "best effort body" in written[0].read_text()
    assert "0 clean, 1 with notes, 1 total" in result.output


def test_bulk_generate_aborts_on_systemic_ollama_failure(monkeypatch, tmp_path):
    csv_path = _write_csv(tmp_path, [("AC-2", ""), ("SI-5", ""), ("AC-3", "")])
    output_dir = tmp_path / "out"

    retrieval_results = {
        "AC-2": _matched_result(),
        "SI-5": _matched_result(),
        "AC-3": _matched_result(),
    }
    demo = cli.engagements.Engagement("demo", "DEMO", cli.config.ENGAGEMENTS_DIR / "demo")
    monkeypatch.setattr(cli, "_active_engagement_or_exit", lambda: demo)
    monkeypatch.setattr(
        cli,
        "_build_collections",
        lambda engagement: {
            cli.config.COLLECTION_KNOWLEDGE_BASE: FakeCollection(5),
            cli.config.COLLECTION_CUSTOMER_STANDARDS: object(),
            cli.config.COLLECTION_PRIVATE_CONTEXT: object(),
        },
    )
    monkeypatch.setattr(
        cli,
        "retrieve_for_control",
        lambda control_id, context, collections, **kwargs: retrieval_results[control_id],
    )

    call_count = {"value": 0}

    def fake_chat_messages(messages, response_format=None, *, on_response=None):
        call_count["value"] += 1
        if call_count["value"] == 2:
            raise ConnectionError("Ollama unreachable")
        return _final_reply("body")

    monkeypatch.setattr(cli, "chat_messages", fake_chat_messages)

    result = runner.invoke(cli.app, ["bulk-generate", str(csv_path), "-o", str(output_dir)])

    assert result.exit_code == 1
    written = list(output_dir.glob("demo_*.md"))
    assert len(written) == 1
    assert not list(output_dir.glob("demo_AC-3_*.md"))
    assert not list(output_dir.glob("demo_SI-5_*.md"))
    assert "Completed: AC-2" in result.output
    assert "Not attempted: SI-5, AC-3" in result.output


def test_bulk_generate_aborts_on_systemic_ollama_response_error(monkeypatch, tmp_path):
    csv_path = _write_csv(tmp_path, [("AC-2", ""), ("SI-5", "")])
    output_dir = tmp_path / "out"

    retrieval_results = {"AC-2": _matched_result(), "SI-5": _matched_result()}
    demo = cli.engagements.Engagement("demo", "DEMO", cli.config.ENGAGEMENTS_DIR / "demo")
    monkeypatch.setattr(cli, "_active_engagement_or_exit", lambda: demo)
    monkeypatch.setattr(
        cli,
        "_build_collections",
        lambda engagement: {
            cli.config.COLLECTION_KNOWLEDGE_BASE: FakeCollection(5),
            cli.config.COLLECTION_CUSTOMER_STANDARDS: object(),
            cli.config.COLLECTION_PRIVATE_CONTEXT: object(),
        },
    )
    monkeypatch.setattr(
        cli,
        "retrieve_for_control",
        lambda control_id, context, collections, **kwargs: retrieval_results[control_id],
    )

    def fake_chat_messages(messages, response_format=None, *, on_response=None):
        raise ollama.ResponseError("model not pulled")

    monkeypatch.setattr(cli, "chat_messages", fake_chat_messages)

    result = runner.invoke(cli.app, ["bulk-generate", str(csv_path), "-o", str(output_dir)])

    assert result.exit_code == 1
    assert not list(output_dir.glob("*"))
    assert "Not attempted: AC-2, SI-5" in result.output


def test_bulk_generate_aborts_before_any_row_when_knowledge_base_empty(monkeypatch, tmp_path):
    csv_path = _write_csv(tmp_path, [("AC-2", "")])
    output_dir = tmp_path / "out"

    def fail_retrieve(*args, **kwargs):
        raise AssertionError("retrieve_for_control should not be called")

    demo = cli.engagements.Engagement("demo", "DEMO", cli.config.ENGAGEMENTS_DIR / "demo")
    monkeypatch.setattr(cli, "_active_engagement_or_exit", lambda: demo)
    monkeypatch.setattr(
        cli,
        "_build_collections",
        lambda engagement: {
            cli.config.COLLECTION_KNOWLEDGE_BASE: FakeCollection(0),
            cli.config.COLLECTION_CUSTOMER_STANDARDS: object(),
            cli.config.COLLECTION_PRIVATE_CONTEXT: object(),
        },
    )
    monkeypatch.setattr(cli, "retrieve_for_control", fail_retrieve)

    result = runner.invoke(cli.app, ["bulk-generate", str(csv_path), "-o", str(output_dir)])

    assert result.exit_code == 1
    assert "srg ingest" in result.output
    assert not output_dir.exists() or not list(output_dir.glob("*"))


def test_bulk_generate_rejects_invalid_csv_before_touching_engagement(monkeypatch, tmp_path):
    csv_path = tmp_path / "bad.csv"
    csv_path.write_text("Wrong,Headers\nAC-2,notes\n", encoding="utf-8")
    output_dir = tmp_path / "out"

    def fail_engagement():
        raise AssertionError("engagement should not be loaded when CSV validation fails")

    monkeypatch.setattr(cli, "_active_engagement_or_exit", fail_engagement)

    result = runner.invoke(cli.app, ["bulk-generate", str(csv_path), "-o", str(output_dir)])

    assert result.exit_code == 1
    assert "Invalid bulk CSV" in result.output
    assert "Missing required column" in result.output


def test_bulk_generate_requires_output_dir(tmp_path):
    csv_path = _write_csv(tmp_path, [("AC-2", "")])

    result = runner.invoke(cli.app, ["bulk-generate", str(csv_path)])

    assert result.exit_code != 0


def test_bulk_generate_creates_missing_output_dir(monkeypatch, tmp_path):
    csv_path = _write_csv(tmp_path, [("AC-2", "")])
    output_dir = tmp_path / "does" / "not" / "exist"

    retrieval_results = {"AC-2": _matched_result()}
    replies_by_control = _patch_bulk_common(
        monkeypatch, retrieval_results, {"AC-2": [_final_reply("AC-2 body")]}
    )

    def track_current(control_id, context, collections, **kwargs):
        replies_by_control["_current"] = control_id
        return retrieval_results[control_id]

    monkeypatch.setattr(cli, "retrieve_for_control", track_current)

    result = runner.invoke(cli.app, ["bulk-generate", str(csv_path), "-o", str(output_dir)])

    assert result.exit_code == 0, result.output
    assert output_dir.is_dir()
    assert len(list(output_dir.glob("demo_*.md"))) == 1


def test_bulk_generate_text_format_uses_txt_extension(monkeypatch, tmp_path):
    csv_path = _write_csv(tmp_path, [("AC-2", "")])
    output_dir = tmp_path / "out"

    retrieval_results = {"AC-2": _matched_result()}
    replies_by_control = _patch_bulk_common(
        monkeypatch, retrieval_results, {"AC-2": [_final_reply("AC-2 body")]}
    )

    def track_current(control_id, context, collections, **kwargs):
        replies_by_control["_current"] = control_id
        return retrieval_results[control_id]

    monkeypatch.setattr(cli, "retrieve_for_control", track_current)

    result = runner.invoke(
        cli.app, ["bulk-generate", str(csv_path), "-o", str(output_dir), "--format", "text"]
    )

    assert result.exit_code == 0, result.output
    written = list(output_dir.glob("demo_*.txt"))
    assert len(written) == 1
    assert "Customer: DEMO" in written[0].read_text()


def test_bulk_generate_rerun_same_day_overwrites_prior_file(monkeypatch, tmp_path):
    csv_path = _write_csv(tmp_path, [("AC-2", "")])
    output_dir = tmp_path / "out"

    retrieval_results = {"AC-2": _matched_result()}

    def run_once(body_text):
        replies_by_control = _patch_bulk_common(
            monkeypatch, retrieval_results, {"AC-2": [_final_reply(body_text)]}
        )

        def track_current(control_id, context, collections, **kwargs):
            replies_by_control["_current"] = control_id
            return retrieval_results[control_id]

        monkeypatch.setattr(cli, "retrieve_for_control", track_current)
        return runner.invoke(cli.app, ["bulk-generate", str(csv_path), "-o", str(output_dir)])

    first = run_once("first run body")
    assert first.exit_code == 0, first.output
    second = run_once("second run body")
    assert second.exit_code == 0, second.output

    written = list(output_dir.glob("demo_*.md"))
    assert len(written) == 1
    assert "second run body" in written[0].read_text()
    assert "first run body" not in written[0].read_text()
