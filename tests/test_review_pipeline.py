import json

from security_response_generator import cli
from security_response_generator.generation.prompt import AssembledPrompt
from security_response_generator.generation.review import (
    REVIEW_SCHEMA,
    assemble_review_messages,
    parse_critique,
    revision_instruction,
)


def _reply(body: str) -> str:
    return json.dumps(
        {
            "needs_info": False,
            "question": None,
            "response": body,
            "validations": ["Screenshot."],
        }
    )


def test_review_and_revise_runs_two_critiques_and_two_generator_revisions(monkeypatch):
    prompt = AssembledPrompt(system="instructions and facts", user="grounding and control")
    initial = _reply("initial")
    generator_replies = iter([_reply("revision one"), _reply("final revision")])
    reviewer_replies = iter(
        [json.dumps({"critique": "fix omission"}), json.dumps({"critique": "tighten prose"})]
    )
    review_calls = []
    generator_calls = []

    def fake_review(messages, response_format=None):
        review_calls.append((messages, response_format))
        return next(reviewer_replies)

    def fake_wait(messages, label="Thinking..."):
        generator_calls.append((list(messages), label))
        return next(generator_replies)

    monkeypatch.setattr(cli, "review_messages", fake_review)
    monkeypatch.setattr(cli, "_wait_for_model", fake_wait)

    messages = [
        {"role": "system", "content": prompt.system},
        {"role": "user", "content": prompt.user},
    ]
    result = cli._review_and_revise(prompt, messages, initial)

    assert result == _reply("final revision")
    assert len(review_calls) == 2
    assert len(generator_calls) == 2
    assert review_calls[0][1] == REVIEW_SCHEMA
    assert initial in review_calls[0][0][-1]["content"]
    assert _reply("revision one") in review_calls[1][0][-1]["content"]
    assert "fix omission" in generator_calls[0][0][-1]["content"]
    assert "tighten prose" in generator_calls[1][0][-1]["content"]
    assert "Do not ask the human analyst" in generator_calls[0][0][-1]["content"]
    assert "final response" in generator_calls[1][0][-1]["content"]


def test_review_and_revise_does_not_duplicate_original_prompt_in_conversation(monkeypatch):
    # Regression test: assemble_review_messages already receives prompt.system and
    # prompt.user explicitly -- _review_and_revise must not also pass them again via
    # the `conversation` argument (messages[0:2]), or the reviewer's prompt doubles
    # in size for zero added information. Only genuine extra turns (prior
    # draft/revision-instruction pairs) should appear as `conversation`.
    prompt = AssembledPrompt(system="instructions and facts", user="grounding and control")
    initial = _reply("initial")
    generator_replies = iter([_reply("revision one"), _reply("final revision")])
    reviewer_replies = iter(
        [json.dumps({"critique": "fix omission"}), json.dumps({"critique": "tighten prose"})]
    )
    captured_conversations = []
    real_assemble_review_messages = assemble_review_messages

    def spy_assemble_review_messages(prompt, candidate, conversation=None):
        captured_conversations.append(conversation)
        return real_assemble_review_messages(prompt, candidate, conversation)

    def fake_review(messages, response_format=None):
        return next(reviewer_replies)

    def fake_wait(messages, label="Thinking..."):
        return next(generator_replies)

    monkeypatch.setattr(cli, "assemble_review_messages", spy_assemble_review_messages)
    monkeypatch.setattr(cli, "review_messages", fake_review)
    monkeypatch.setattr(cli, "_wait_for_model", fake_wait)

    messages = [
        {"role": "system", "content": prompt.system},
        {"role": "user", "content": prompt.user},
    ]
    cli._review_and_revise(prompt, messages, initial)

    assert len(captured_conversations) == 2
    # Pass 1: nothing has happened yet beyond the original prompt -- no extra turns.
    assert captured_conversations[0] == []
    # Pass 2: only the pass-1 draft + revision instruction, never the original prompt.
    assert len(captured_conversations[1]) == 2
    for entry in captured_conversations[1]:
        assert entry["content"] != prompt.system
        assert entry["content"] != prompt.user


def test_review_prompt_contains_original_grounding_and_candidate():
    prompt = AssembledPrompt(system="SYSTEM FACT", user="SOURCE MATERIAL")
    conversation = [{"role": "user", "content": "CLARIFICATION ANSWER"}]

    messages = assemble_review_messages(prompt, "CANDIDATE", conversation)

    assert "SYSTEM FACT" in messages[1]["content"]
    assert "SOURCE MATERIAL" in messages[1]["content"]
    assert "CANDIDATE" in messages[1]["content"]
    assert "CLARIFICATION ANSWER" in messages[1]["content"]
    assert "do not ask the human analyst questions" in messages[0]["content"]


def test_parse_critique_and_revision_instruction_fallbacks():
    assert parse_critique('{"critique": "correct it"}') == "correct it"
    assert parse_critique("plain critique") == "plain critique"
    instruction = revision_instruction("correct it", final=True)
    assert "needs_info to false" in instruction
    assert "question to null" in instruction
