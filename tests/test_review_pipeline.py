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
