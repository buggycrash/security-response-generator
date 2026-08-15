import json

import pytest
from typer.testing import CliRunner

from security_response_generator import cli
from security_response_generator.generation.prompt import (
    FORCED_COMPLETION_INSTRUCTION,
    AssembledPrompt,
)
from security_response_generator.generation.retrieval import (
    ChatRetrievalResult,
    RetrievalResult,
    RetrievedChunk,
)
from security_response_generator.ingest.chunking import Chunk

runner = CliRunner()
VALIDATION = "Screenshot of the relevant system screen showing the stated configuration."


@pytest.fixture(autouse=True)
def _skip_review_pipeline_in_legacy_cli_tests(monkeypatch):
    """These tests exercise generation/follow-up behavior, not review orchestration."""
    monkeypatch.setattr(cli, "_review_and_revise", lambda prompt, messages, draft, **kwargs: draft)


def _final_reply(text: str, validations: list[str] | None = None) -> str:
    return json.dumps(
        {
            "needs_info": False,
            "question": None,
            "response": text,
            "validations": validations if validations is not None else [VALIDATION],
        }
    )


def _followup_reply(question: str) -> str:
    return json.dumps(
        {"needs_info": True, "question": question, "response": None, "validations": None}
    )


def _rendered_reply(text: str, validation: str = VALIDATION) -> str:
    return f"{text}\n\n[Validations]\n\n* {validation}"


def _baseline_chunk(chunk_id: str = "doc.md::0") -> RetrievedChunk:
    return RetrievedChunk(text="baseline text", source_path="doc.md", chunk_id=chunk_id)


def _patch_common(
    monkeypatch, retrieval_result: RetrievalResult, chat_return: str = "response text"
):
    demo = cli.engagements.Engagement(
        "demo",
        "DEMO",
        cli.config.ENGAGEMENTS_DIR / "demo",
    )
    monkeypatch.setattr(cli, "_active_engagement_or_exit", lambda: demo)
    monkeypatch.setattr(cli, "get_client", lambda path=None: object())
    monkeypatch.setattr(cli, "get_collection", lambda client, name: object())
    monkeypatch.setattr(
        cli,
        "retrieve_for_control",
        lambda control_id, context, collections, **kwargs: retrieval_result,
    )
    monkeypatch.setattr(
        cli, "chat_messages", lambda messages, response_format=None, **kwargs: chat_return
    )


def test_update_nist_converts_catalog_and_prints_ingest_next_step(monkeypatch, tmp_path):
    output = tmp_path / "catalog.md"
    conversion = cli.nist_oscal.ConversionResult(
        markdown="converted",
        version="5.2.0",
        control_count=324,
        enhancement_count=872,
        source_sha256="abc123",
    )
    captured = {}

    def fake_update(source, destination):
        captured["args"] = (source, destination)
        return conversion

    monkeypatch.setattr(cli.nist_oscal, "update_catalog", fake_update)

    result = runner.invoke(
        cli.app,
        ["update-nist", "--source", "catalog.json", "--output", str(output)],
    )

    assert result.exit_code == 0
    assert captured["args"] == ("catalog.json", output)
    assert "NIST SP 800-53 5.2.0" in result.output
    assert "324 controls and 872 enhancements" in result.output
    assert "srg ingest --source knowledge_base" in result.output


def test_update_nist_reports_catalog_error(monkeypatch):
    def fail(*args):
        raise cli.nist_oscal.CatalogError("bad catalog")

    monkeypatch.setattr(cli.nist_oscal, "update_catalog", fail)

    result = runner.invoke(cli.app, ["update-nist", "--source", "bad.json"])

    assert result.exit_code == 1
    assert "Unable to update the NIST catalog: bad catalog" in result.output


def test_generate_refuses_when_no_baseline_match(monkeypatch):
    result_obj = RetrievalResult(
        customer_chunks=[],
        baseline_chunks=[],
        private_chunks=[],
        baseline_exact_match=False,
        customer_exact_match=False,
    )
    _patch_common(monkeypatch, result_obj)
    chat_called = {"value": False}
    monkeypatch.setattr(
        cli, "chat_messages", lambda *a, **kw: chat_called.__setitem__("value", True)
    )

    result = runner.invoke(cli.app, ["generate", "ZZ-99"])

    assert result.exit_code == 1
    assert "No matching NIST baseline content found" in result.output
    assert chat_called["value"] is False


def test_generate_refuses_for_fabricated_control_id_despite_semantic_hits(monkeypatch):
    # A fabricated ID (e.g. "IL-27") can still pull back semantically-nearest baseline
    # chunks even though it doesn't exist -- refusal must key off baseline_exact_match,
    # not baseline_chunks being non-empty.
    result_obj = RetrievalResult(
        customer_chunks=[],
        baseline_chunks=[_baseline_chunk()],
        private_chunks=[],
        baseline_exact_match=False,
        customer_exact_match=False,
    )
    _patch_common(monkeypatch, result_obj)
    chat_called = {"value": False}
    monkeypatch.setattr(
        cli, "chat_messages", lambda *a, **kw: chat_called.__setitem__("value", True)
    )

    result = runner.invoke(cli.app, ["generate", "IL-27"])

    assert result.exit_code == 1
    assert "No matching NIST baseline content found" in result.output
    assert chat_called["value"] is False


def test_generate_prints_response_and_writes_output_file(monkeypatch, tmp_path):
    result_obj = RetrievalResult(
        customer_chunks=[],
        baseline_chunks=[_baseline_chunk()],
        private_chunks=[],
        baseline_exact_match=True,
        customer_exact_match=True,
    )
    _patch_common(monkeypatch, result_obj, chat_return="# SI-5\nGenerated response")
    output_file = tmp_path / "response.md"

    result = runner.invoke(
        cli.app, ["generate", "SI-5", "--context", "notes", "-o", str(output_file)]
    )

    assert result.exit_code == 0
    assert "Generated response" in result.stdout
    assert output_file.read_text() == (
        "# Customer: DEMO\n\n# SI-5\nGenerated response\n\n[Validations]\n\n"
        "* [PLACEHOLDER: no screenshot validations were generated.]"
    )


def test_generate_prepends_deterministic_note_when_no_customer_standard_matched(monkeypatch):
    # SRG determines this from retrieval, not the model -- a small local reviewer/
    # generator model handling this itself proved unreliable in both directions
    # (sometimes omitting the caveat, sometimes claiming no standard existed when one
    # did), so the caveat is code-generated and unconditional on model compliance.
    result_obj = RetrievalResult(
        customer_chunks=[],
        baseline_chunks=[_baseline_chunk()],
        private_chunks=[],
        baseline_exact_match=True,
        customer_exact_match=False,
    )
    _patch_common(monkeypatch, result_obj, chat_return="# SI-5\nGenerated response")

    result = runner.invoke(cli.app, ["generate", "SI-5"])

    assert result.exit_code == 0
    assert (
        "[No customer- or state-specific standard was located for SI-5. "
        "This response is based on the NIST baseline and any available system context.]"
        in result.stdout
    )


def test_generate_omits_deterministic_note_when_customer_standard_matched(monkeypatch):
    result_obj = RetrievalResult(
        customer_chunks=[_baseline_chunk("customer.md::0")],
        baseline_chunks=[_baseline_chunk()],
        private_chunks=[],
        baseline_exact_match=True,
        customer_exact_match=True,
    )
    _patch_common(monkeypatch, result_obj, chat_return="# SI-5\nGenerated response")

    result = runner.invoke(cli.app, ["generate", "SI-5"])

    assert result.exit_code == 0
    assert "No customer- or state-specific standard was located" not in result.stdout


def test_generate_omits_semantic_only_customer_chunks_from_prompt(monkeypatch):
    # A control with no genuine customer/state standard match can still pull back
    # semantically-nearest customer chunks (e.g. SC-13 content for a SC-8(1) query,
    # the only SC-family entry in a collection with no SC-8(1) match) -- those chunks
    # must never reach assemble_prompt's "Authoritative" section when
    # has_customer_match is False, even though customer_chunks itself is non-empty.
    result_obj = RetrievalResult(
        customer_chunks=[_baseline_chunk("customer.md::0")],
        baseline_chunks=[_baseline_chunk()],
        private_chunks=[],
        baseline_exact_match=True,
        customer_exact_match=False,
    )
    _patch_common(monkeypatch, result_obj, chat_return="response text")
    captured = {}
    original_assemble_prompt = cli.assemble_prompt

    def spy_assemble_prompt(*args, **kwargs):
        captured["customer_chunks"] = kwargs["customer_chunks"]
        return original_assemble_prompt(*args, **kwargs)

    monkeypatch.setattr(cli, "assemble_prompt", spy_assemble_prompt)

    result = runner.invoke(cli.app, ["generate", "SI-5"])

    assert result.exit_code == 0
    assert captured["customer_chunks"] == []


def test_generate_forwards_genuine_customer_chunks_to_prompt(monkeypatch):
    result_obj = RetrievalResult(
        customer_chunks=[_baseline_chunk("customer.md::0")],
        baseline_chunks=[_baseline_chunk()],
        private_chunks=[],
        baseline_exact_match=True,
        customer_exact_match=True,
    )
    _patch_common(monkeypatch, result_obj, chat_return="response text")
    captured = {}
    original_assemble_prompt = cli.assemble_prompt

    def spy_assemble_prompt(*args, **kwargs):
        captured["customer_chunks"] = kwargs["customer_chunks"]
        return original_assemble_prompt(*args, **kwargs)

    monkeypatch.setattr(cli, "assemble_prompt", spy_assemble_prompt)

    result = runner.invoke(cli.app, ["generate", "SI-5"])

    assert result.exit_code == 0
    assert captured["customer_chunks"] == result_obj.customer_chunks


def test_generate_without_output_flag_does_not_require_file(monkeypatch):
    result_obj = RetrievalResult(
        customer_chunks=[],
        baseline_chunks=[_baseline_chunk()],
        private_chunks=[],
        baseline_exact_match=True,
        customer_exact_match=False,
    )
    _patch_common(monkeypatch, result_obj, chat_return="response text")

    result = runner.invoke(cli.app, ["generate", "SI-5"])

    assert result.exit_code == 0
    assert "response text" in result.stdout


def test_generate_skips_review_by_default(monkeypatch):
    result_obj = RetrievalResult(
        customer_chunks=[],
        baseline_chunks=[_baseline_chunk()],
        private_chunks=[],
        baseline_exact_match=True,
        customer_exact_match=False,
    )
    _patch_common(monkeypatch, result_obj, chat_return=_final_reply("draft body"))
    monkeypatch.setattr(
        cli,
        "_review_and_revise",
        lambda *args: (_ for _ in ()).throw(AssertionError("review must be opt-in")),
    )

    result = runner.invoke(cli.app, ["generate", "SI-5"])

    assert result.exit_code == 0, result.output
    assert "draft body" in result.stdout


def test_generate_review_flag_enables_review_pipeline(monkeypatch):
    result_obj = RetrievalResult(
        customer_chunks=[],
        baseline_chunks=[_baseline_chunk()],
        private_chunks=[],
        baseline_exact_match=True,
        customer_exact_match=False,
    )
    _patch_common(monkeypatch, result_obj, chat_return=_final_reply("draft body"))
    calls = []

    def fake_review(prompt, messages, draft, **kwargs):
        calls.append(draft)
        return _final_reply("reviewed body")

    monkeypatch.setattr(cli, "_review_and_revise", fake_review)

    result = runner.invoke(cli.app, ["generate", "SI-5", "--review"])

    assert result.exit_code == 0, result.output
    assert calls == [_final_reply("draft body")]
    assert "reviewed body" in result.stdout


def test_generate_text_format_normalizes_output(monkeypatch):
    result_obj = RetrievalResult(
        customer_chunks=[],
        baseline_chunks=[_baseline_chunk()],
        private_chunks=[],
        baseline_exact_match=True,
        customer_exact_match=False,
    )
    _patch_common(monkeypatch, result_obj, chat_return="## SI-5\n\n“Quoted” response — done.")

    result = runner.invoke(cli.app, ["generate", "SI-5", "--format", "text"])

    assert result.exit_code == 0
    assert "Customer: DEMO" in result.stdout
    assert "SI-5" in result.stdout
    assert '"Quoted" response - done.' in result.stdout
    assert "#" not in result.stdout
    assert all(ord(c) < 128 for c in result.stdout if c != "\n")


def test_generate_markdown_format_is_default_and_unmodified(monkeypatch):
    result_obj = RetrievalResult(
        customer_chunks=[],
        baseline_chunks=[_baseline_chunk()],
        private_chunks=[],
        baseline_exact_match=True,
        customer_exact_match=False,
    )
    _patch_common(monkeypatch, result_obj, chat_return="## SI-5\n\n“Quoted” response.")

    result = runner.invoke(cli.app, ["generate", "SI-5"])

    assert result.exit_code == 0
    assert "# Customer: DEMO" in result.stdout
    assert "## SI-5" in result.stdout
    assert "“Quoted”" in result.stdout


def test_generate_labels_customer_engagement_in_plain_ascii_without_uppercasing(
    monkeypatch, tmp_path
):
    result_obj = RetrievalResult(
        customer_chunks=[],
        baseline_chunks=[_baseline_chunk()],
        private_chunks=[],
        baseline_exact_match=True,
        customer_exact_match=False,
    )
    _patch_common(monkeypatch, result_obj, chat_return="Normal sentence capitalization.")
    engagement = cli.engagements.Engagement("acme-health", "Acme Héalth", tmp_path)
    monkeypatch.setattr(cli, "_active_engagement_or_exit", lambda: engagement)

    result = runner.invoke(cli.app, ["generate", "SI-5", "--format", "text"])

    assert result.exit_code == 0
    assert "Customer: Acme Health" in result.stdout
    assert "Normal sentence capitalization." in result.stdout
    assert "NORMAL SENTENCE CAPITALIZATION" not in result.stdout
    assert all(ord(character) < 128 for character in result.stdout)


def test_generate_text_format_default_output_filename_uses_txt_extension(monkeypatch, tmp_path):
    result_obj = RetrievalResult(
        customer_chunks=[],
        baseline_chunks=[_baseline_chunk()],
        private_chunks=[],
        baseline_exact_match=True,
        customer_exact_match=False,
    )
    _patch_common(monkeypatch, result_obj, chat_return="response text")

    result = runner.invoke(cli.app, ["generate", "SI-5", "--format", "text", "-o", str(tmp_path)])

    assert result.exit_code == 0
    written_files = list(tmp_path.glob("demo_SI-5_*.txt"))
    assert len(written_files) == 1


def test_generate_markdown_format_default_output_filename_uses_md_extension(monkeypatch, tmp_path):
    result_obj = RetrievalResult(
        customer_chunks=[],
        baseline_chunks=[_baseline_chunk()],
        private_chunks=[],
        baseline_exact_match=True,
        customer_exact_match=False,
    )
    _patch_common(monkeypatch, result_obj, chat_return="response text")

    result = runner.invoke(cli.app, ["generate", "SI-5", "-o", str(tmp_path)])

    assert result.exit_code == 0
    written_files = list(tmp_path.glob("demo_SI-5_*.md"))
    assert len(written_files) == 1


def _patch_chat_common(
    monkeypatch, chat_result: ChatRetrievalResult, chat_return: str = "answer text"
):
    demo = cli.engagements.Engagement(
        "demo",
        "DEMO",
        cli.config.ENGAGEMENTS_DIR / "demo",
    )
    monkeypatch.setattr(cli, "_active_engagement_or_exit", lambda: demo)
    monkeypatch.setattr(cli, "get_client", lambda path=None: object())
    monkeypatch.setattr(cli, "get_collection", lambda client, name: object())
    monkeypatch.setattr(cli, "retrieve_for_chat", lambda question, collections: chat_result)
    monkeypatch.setattr(
        cli, "chat_messages", lambda messages, response_format=None, **kwargs: chat_return
    )


def test_chat_prints_answer_labeled_with_engagement_and_draft_disclaimer(monkeypatch):
    chat_result = ChatRetrievalResult(
        customer_chunks=[_baseline_chunk("customer.md::0")],
        baseline_chunks=[_baseline_chunk("baseline.md::0")],
        private_chunks=[_baseline_chunk("private.md::0")],
    )
    _patch_chat_common(monkeypatch, chat_result, chat_return="Passwords must be 14+ characters.")

    result = runner.invoke(cli.app, ["chat", "What is the password complexity requirement?"])

    assert result.exit_code == 0
    assert "Customer: DEMO" in result.stdout
    assert "Passwords must be 14+ characters." in result.stdout
    assert "Draft answer" in result.stdout


def test_chat_answers_even_when_nothing_retrieved(monkeypatch):
    chat_result = ChatRetrievalResult(customer_chunks=[], baseline_chunks=[], private_chunks=[])
    chat_called = {"value": False}

    def fake_chat_messages(messages, response_format=None, **kwargs):
        chat_called["value"] = True
        return "The indexed material doesn't cover that."

    _patch_chat_common(monkeypatch, chat_result)
    monkeypatch.setattr(cli, "chat_messages", fake_chat_messages)

    result = runner.invoke(cli.app, ["chat", "What color is the sky?"])

    assert result.exit_code == 0
    assert chat_called["value"] is True
    assert "doesn't cover that" in result.stdout


def test_chat_answers_when_only_customer_standards_match(monkeypatch):
    chat_result = ChatRetrievalResult(
        customer_chunks=[_baseline_chunk("customer.md::0")],
        baseline_chunks=[],
        private_chunks=[],
    )
    _patch_chat_common(monkeypatch, chat_result, chat_return="Reviewed weekly per state policy.")

    result = runner.invoke(cli.app, ["chat", "How often are audit logs reviewed?"])

    assert result.exit_code == 0
    assert "Reviewed weekly per state policy." in result.stdout


def test_chat_shows_demo_reminder(monkeypatch):
    chat_result = ChatRetrievalResult(customer_chunks=[], baseline_chunks=[], private_chunks=[])
    _patch_chat_common(monkeypatch, chat_result)

    result = runner.invoke(cli.app, ["chat", "What is the password complexity requirement?"])

    assert result.exit_code == 0
    assert "Using DEMO engagement" in result.output


def _prompt() -> AssembledPrompt:
    return AssembledPrompt(system="SYSTEM", user="USER")


def test_run_conversation_returns_immediately_when_no_followup_needed(monkeypatch):
    calls = []

    def fake_chat_messages(messages, response_format=None, **kwargs):
        calls.append(list(messages))
        return _final_reply("# SI-5\n\nFinal response.")

    monkeypatch.setattr(cli, "chat_messages", fake_chat_messages)

    result = cli._run_conversation(_prompt())

    assert result == _rendered_reply("# SI-5\n\nFinal response.")
    assert len(calls) == 1


def test_render_final_reply_uses_unbulleted_validations_for_text_output():
    raw = _final_reply("AC-2\n\nImplementation prose.")

    result = cli._render_final_reply(raw, cli.OutputFormat.text)

    assert result == f"AC-2\n\nImplementation prose.\n\n[Validations]\n\n{VALIDATION}"


def test_render_final_reply_replaces_model_inlined_validations_with_structured_list():
    raw = _final_reply(
        "# AC-2\n\nImplementation prose.\n\n[Validations]\n\n* Duplicate suggestion.",
        validations=["Screenshot of the account settings showing shared accounts disabled."],
    )

    result = cli._render_final_reply(raw, cli.OutputFormat.markdown)

    assert result.count("[Validations]") == 1
    assert "Duplicate suggestion" not in result
    assert result.endswith("* Screenshot of the account settings showing shared accounts disabled.")


def test_run_conversation_asks_once_then_returns_final_answer(monkeypatch, tmp_path):
    first_reply = _followup_reply("what SIEM do you use?")
    replies = iter(
        [
            first_reply,
            _final_reply("# SI-5\n\nFinal response using Acme Sentinel."),
        ]
    )
    calls = []

    def fake_chat_messages(messages, response_format=None, **kwargs):
        calls.append(list(messages))
        return next(replies)

    monkeypatch.setattr(cli, "chat_messages", fake_chat_messages)
    monkeypatch.setattr(cli.typer, "prompt", lambda _: "Acme Sentinel")

    result = cli._run_conversation(_prompt())

    assert result == _rendered_reply("# SI-5\n\nFinal response using Acme Sentinel.")
    assert len(calls) == 2
    # second call's message history includes the question and the analyst's answer
    assert calls[1][-2] == {"role": "assistant", "content": first_reply}
    assert calls[1][-1] == {"role": "user", "content": "Acme Sentinel"}


def test_run_conversation_forces_completion_when_budget_exhausted(monkeypatch):
    monkeypatch.setattr(cli.config, "MAX_FOLLOWUP_TURNS", 2)

    replies = iter(
        [
            _followup_reply("what SIEM do you use?"),
            _followup_reply("how often is it reviewed?"),
            _followup_reply("even more detail please"),
            _final_reply("# SI-5\n\nBest-effort response. [PLACEHOLDER: need details on X]"),
        ]
    )
    chat_call_count = {"value": 0}
    prompt_call_count = {"value": 0}

    def fake_chat_messages(messages, response_format=None, **kwargs):
        chat_call_count["value"] += 1
        return next(replies)

    def fake_prompt(_):
        prompt_call_count["value"] += 1
        return "an answer"

    monkeypatch.setattr(cli, "chat_messages", fake_chat_messages)
    monkeypatch.setattr(cli.typer, "prompt", fake_prompt)

    result = cli._run_conversation(_prompt())

    assert result == _rendered_reply(
        "# SI-5\n\nBest-effort response. [PLACEHOLDER: need details on X]"
    )
    # 2 answered questions + 1 that trips the budget + 1 forced-completion call
    assert chat_call_count["value"] == 4
    # only the 2 budgeted questions were asked interactively
    assert prompt_call_count["value"] == 2


def test_render_final_reply_shows_placeholder_for_blank_response():
    raw = _final_reply("")

    result = cli._render_final_reply(raw, cli.OutputFormat.markdown)

    assert cli.BLANK_RESPONSE_PLACEHOLDER in result
    assert "needs_info" not in result


def test_run_conversation_tracked_retries_once_on_blank_response(monkeypatch):
    replies = iter([_final_reply(""), _final_reply("actual content")])
    calls = []

    def fake_chat_messages(messages, response_format=None, **kwargs):
        calls.append(list(messages))
        return next(replies)

    monkeypatch.setattr(cli, "chat_messages", fake_chat_messages)

    outcome = cli._run_conversation_tracked(_prompt())

    assert outcome.text == _rendered_reply("actual content")
    assert len(calls) == 2
    assert calls[1][-1] == {"role": "user", "content": cli.BLANK_RESPONSE_RETRY_INSTRUCTION}


def test_run_conversation_tracked_gives_up_after_one_blank_retry(monkeypatch):
    calls = []

    def fake_chat_messages(messages, response_format=None, **kwargs):
        calls.append(list(messages))
        return _final_reply("")

    monkeypatch.setattr(cli, "chat_messages", fake_chat_messages)

    outcome = cli._run_conversation_tracked(_prompt())

    assert len(calls) == 2
    assert cli.BLANK_RESPONSE_PLACEHOLDER in outcome.text


def test_run_conversation_tracked_reports_no_forced_completion_on_first_try(monkeypatch):
    monkeypatch.setattr(
        cli, "chat_messages", lambda messages, response_format=None, **kwargs: _final_reply("body")
    )

    outcome = cli._run_conversation_tracked(_prompt())

    assert outcome.text == _rendered_reply("body")
    assert outcome.forced_completion is False


def test_run_conversation_tracked_reports_forced_completion_flag(monkeypatch):
    replies = iter([_followup_reply("what SIEM?"), _final_reply("best effort body")])
    monkeypatch.setattr(
        cli, "chat_messages", lambda messages, response_format=None, **kwargs: next(replies)
    )

    outcome = cli._run_conversation_tracked(_prompt(), max_followups=0)

    assert outcome.text == _rendered_reply("best effort body")
    assert outcome.forced_completion is True


def test_run_conversation_respects_explicit_max_followups_override_independent_of_config(
    monkeypatch,
):
    # config.MAX_FOLLOWUP_TURNS is left at its real default (not monkeypatched) to prove
    # an explicit max_followups argument takes precedence rather than being shadowed by
    # a bound-at-definition-time default.
    replies = iter([_followup_reply("what SIEM?"), _final_reply("best effort body")])
    calls = []

    def fake_chat_messages(messages, response_format=None, **kwargs):
        calls.append(list(messages))
        return next(replies)

    monkeypatch.setattr(cli, "chat_messages", fake_chat_messages)
    monkeypatch.setattr(
        cli.typer, "prompt", lambda *a, **kw: (_ for _ in ()).throw(AssertionError("no prompt"))
    )

    outcome = cli._run_conversation_tracked(_prompt(), max_followups=0)

    assert outcome.forced_completion is True
    assert len(calls) == 2
    assert calls[1][-1] == {"role": "user", "content": FORCED_COMPLETION_INSTRUCTION}


def test_run_conversation_forced_completion_message_included_in_final_call(monkeypatch):
    monkeypatch.setattr(cli.config, "MAX_FOLLOWUP_TURNS", 0)
    replies = iter(
        [
            _followup_reply("what SIEM do you use?"),
            _final_reply("# SI-5\n\nBest-effort response with placeholder."),
        ]
    )
    calls = []

    def fake_chat_messages(messages, response_format=None, **kwargs):
        calls.append(list(messages))
        return next(replies)

    monkeypatch.setattr(cli, "chat_messages", fake_chat_messages)

    result = cli._run_conversation(_prompt())

    assert result == _rendered_reply("# SI-5\n\nBest-effort response with placeholder.")
    assert len(calls) == 2
    assert calls[1][-1] == {"role": "user", "content": FORCED_COMPLETION_INSTRUCTION}


def test_ingest_rejects_unknown_source():
    result = runner.invoke(cli.app, ["ingest", "--source", "bogus"])

    assert result.exit_code == 1
    assert "Unknown source" in result.output


def test_ingest_all_calls_ingest_source_for_each_collection(monkeypatch, tmp_path):
    calls = []
    engagement = cli.engagements.Engagement("test", "Test", tmp_path / "test")
    monkeypatch.setattr(cli, "_active_engagement_or_exit", lambda: engagement)
    monkeypatch.setattr(cli, "get_client", lambda path=None: object())
    monkeypatch.setattr(
        cli,
        "_ingest_source",
        lambda client, name, source_dir, manifest: calls.append(name),
    )
    monkeypatch.setattr(cli.config, "MANIFEST_PATH", tmp_path / "manifest.json")

    result = runner.invoke(cli.app, ["ingest"])

    assert result.exit_code == 0
    assert set(calls) == set(cli.config.SOURCE_NAMES)


def test_ingest_single_source_only_calls_that_collection(monkeypatch, tmp_path):
    calls = []
    engagement = cli.engagements.Engagement("test", "Test", tmp_path / "test")
    monkeypatch.setattr(cli, "_active_engagement_or_exit", lambda: engagement)
    monkeypatch.setattr(cli, "get_client", lambda path=None: object())
    monkeypatch.setattr(
        cli,
        "_ingest_source",
        lambda client, name, source_dir, manifest: calls.append(name),
    )
    monkeypatch.setattr(cli.config, "MANIFEST_PATH", tmp_path / "manifest.json")

    result = runner.invoke(cli.app, ["ingest", "--source", "private_context"])

    assert result.exit_code == 0
    assert calls == ["private_context"]


def test_ingest_rebuild_rejects_shared_knowledge_base():
    result = runner.invoke(
        cli.app,
        ["ingest", "--source", "knowledge_base", "--rebuild"],
    )

    assert result.exit_code == 1
    assert "--rebuild applies to engagement data" in result.output


def test_single_source_rebuild_preserves_other_engagement_manifest(monkeypatch, tmp_path):
    engagement = cli.engagements.Engagement("test", "Test", tmp_path / "test")
    engagement.chroma_dir.mkdir(parents=True)
    engagement.manifest_path.write_text(
        json.dumps(
            {
                "customer_standards/old.md": "customer-hash",
                "private_context/context.md": "private-hash",
            }
        ),
        encoding="utf-8",
    )

    class FakeClient:
        def delete_collection(self, name):
            pass

    monkeypatch.setattr(cli, "_active_engagement_or_exit", lambda: engagement)
    monkeypatch.setattr(cli, "get_client", lambda path=None: FakeClient())
    monkeypatch.setattr(cli, "_ingest_source", lambda *args: None)
    monkeypatch.setattr(cli.config, "MANIFEST_PATH", tmp_path / "baseline-manifest.json")

    result = runner.invoke(
        cli.app,
        ["ingest", "--source", "customer_standards", "--rebuild"],
    )

    assert result.exit_code == 0
    saved = json.loads(engagement.manifest_path.read_text(encoding="utf-8"))
    assert saved == {"private_context/context.md": "private-hash"}


def test_create_engagement_command_reports_document_paths(monkeypatch, tmp_path):
    engagement = cli.engagements.Engagement("virginia", "Virginia", tmp_path / "virginia")
    monkeypatch.setattr(
        cli.engagements,
        "create_engagement",
        lambda name, customer_name=None: engagement,
    )

    result = runner.invoke(cli.app, ["create-engagement", "virginia"])

    assert result.exit_code == 0
    assert "Created and activated engagement: Virginia" in result.output
    assert "Add customer standards files in:" in result.output
    assert "Add private system context details in:" in result.output
    assert str(engagement.customer_standards_dir) in result.output
    assert str(engagement.private_context_dir) in result.output


def test_demo_reminder_links_to_engagement_documentation(capsys):
    demo = cli.engagements.Engagement(
        "demo",
        "DEMO",
        cli.config.ENGAGEMENTS_DIR / "demo",
    )
    cli._show_demo_reminder(demo)

    output = capsys.readouterr().err
    assert output == (
        "\nUsing DEMO engagement. To create your first engagement, see README.md "
        "#create-a-customer-engagement.\n"
    )


def test_upsert_with_progress_calls_upsert_chunks_with_working_callback(monkeypatch):
    captured = {}

    def fake_upsert_chunks(collection, relative_path, collection_name, chunks, on_batch=None):
        captured["args"] = (collection, relative_path, collection_name, chunks)
        if on_batch is not None:
            on_batch(len(chunks))  # simulate a single batch completing

    monkeypatch.setattr(cli, "upsert_chunks", fake_upsert_chunks)

    chunks = [Chunk(text="a", chunk_index=0), Chunk(text="b", chunk_index=1)]
    cli._upsert_with_progress("fake-collection", "doc.md", "knowledge_base", chunks)

    assert captured["args"] == ("fake-collection", "doc.md", "knowledge_base", chunks)


def test_upsert_with_progress_no_op_for_empty_chunks(monkeypatch):
    def fake_upsert_chunks(*args, **kwargs):
        raise AssertionError("should not be called for empty chunks")

    monkeypatch.setattr(cli, "upsert_chunks", fake_upsert_chunks)

    cli._upsert_with_progress("fake-collection", "doc.md", "knowledge_base", [])
