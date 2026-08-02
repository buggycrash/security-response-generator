"""Assemble the final prompt sent to the generation model."""

import enum
import json
from dataclasses import dataclass

from security_response_generator.generation.retrieval import RetrievedChunk

CUSTOMER_LABEL = "--- Customer/State Standard (Authoritative) ---"
BASELINE_LABEL = "--- NIST 800-53 Baseline ---"
PRIVATE_LABEL = "--- System-Specific Context ---"


class OutputFormat(enum.StrEnum):
    markdown = "markdown"
    text = "text"


MARKDOWN_FORMAT_INSTRUCTION = (
    'When you do write a "response", its content must be valid Markdown, but kept '
    "minimal: a single '# <Control ID>' heading followed by plain narrative "
    "paragraphs, and nothing else. Do not use tables, bullet or numbered lists, "
    "multiple subheadings, bold status labels, or a separate summary/conclusion "
    "section -- this text goes directly into a GRC tool's response field as the "
    "control implementation narrative, not a formatted audit report. Do not include "
    "commentary outside the response itself."
)

TEXT_FORMAT_INSTRUCTION = (
    'When you do write a "response", its content must be plain ASCII text. Do not '
    "use any Markdown syntax (no #, *, _, backticks, tables, or bullet symbols), "
    "smart quotes, em-dashes, or any non-ASCII characters. Use a plain capitalized "
    "line with the control ID as a heading, followed by plain narrative paragraphs, "
    "and nothing else -- no tables (including ASCII-art tables built from "
    "dashes/pipes), no bullet or numbered lists, no separate summary/conclusion "
    "section. Do not include commentary outside the response itself."
)

_FORMAT_INSTRUCTIONS = {
    OutputFormat.markdown: MARKDOWN_FORMAT_INSTRUCTION,
    OutputFormat.text: TEXT_FORMAT_INSTRUCTION,
}

RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "needs_info": {"type": "boolean"},
        "question": {"type": ["string", "null"]},
        "response": {"type": ["string", "null"]},
    },
    "required": ["needs_info", "question", "response"],
}

FOLLOWUP_INSTRUCTION = (
    'Your reply must be a JSON object with exactly three fields: "needs_info" '
    '(boolean), "question" (string or null), and "response" (string or null). '
    "If completing this response requires information not covered by the material "
    "above or the analyst's notes -- and the gap concerns a distinct, material part "
    'of the control rather than a minor stylistic detail -- set "needs_info" to '
    'true, put one focused question in "question", and leave "response" null. '
    'Otherwise set "needs_info" to false, leave "question" null, and put the '
    'full response in "response" following the rules above.'
)

FORCED_COMPLETION_INSTRUCTION = (
    "You have reached the limit of follow-up questions for this session. Do not ask "
    'any further questions -- set "needs_info" to false and write your final '
    'response now in the "response" field, using only the information gathered so '
    "far. For any distinct part of the control you still cannot address with "
    "confidence, insert a clearly marked placeholder in the text (for example: "
    '"[PLACEHOLDER: need details on ...]") instead of guessing or inventing details. '
    "Open the response with a brief, polite note that some information was not "
    "available and that placeholder(s) were left for the analyst to fill in before "
    "this response is submitted to the assessor."
)


@dataclass
class ModelReply:
    needs_info: bool
    question: str | None
    response: str | None


def parse_model_reply(raw: str) -> ModelReply:
    """Parse the model's structured JSON reply.

    Falls back to treating the raw text as a final response if the model
    returns something that doesn't match the requested schema -- grammar-
    constrained decoding makes this very unlikely, but the reply is still
    external input crossing a process boundary.
    """
    try:
        data = json.loads(raw)
        return ModelReply(
            needs_info=bool(data["needs_info"]),
            question=data.get("question"),
            response=data.get("response"),
        )
    except (json.JSONDecodeError, KeyError, TypeError):
        return ModelReply(needs_info=False, question=None, response=raw)


@dataclass
class AssembledPrompt:
    system: str
    user: str


def assemble_prompt(
    instructions: str,
    control_id: str,
    context_notes: str,
    customer_chunks: list[RetrievedChunk],
    baseline_chunks: list[RetrievedChunk],
    private_chunks: list[RetrievedChunk],
    output_format: OutputFormat = OutputFormat.markdown,
) -> AssembledPrompt:
    sections: list[str] = []

    if customer_chunks:
        sections.append(CUSTOMER_LABEL)
        sections.extend(chunk.text for chunk in customer_chunks)

    sections.append(BASELINE_LABEL)
    sections.extend(chunk.text for chunk in baseline_chunks)

    if private_chunks:
        sections.append(PRIVATE_LABEL)
        sections.extend(chunk.text for chunk in private_chunks)

    sections.append(f"Control ID: {control_id}")
    if context_notes:
        sections.append(f"Analyst notes: {context_notes}")
    sections.append(FOLLOWUP_INSTRUCTION)
    sections.append(_FORMAT_INSTRUCTIONS[output_format])

    return AssembledPrompt(system=instructions, user="\n\n".join(sections))
