"""Assemble the final prompt sent to the generation model."""

import enum
import json
from dataclasses import dataclass

from security_response_generator.generation.retrieval import RetrievedChunk

CUSTOMER_LABEL = "--- Customer/State Standard (Authoritative) ---"
BASELINE_LABEL = "--- NIST 800-53 Baseline ---"
PRIVATE_LABEL = "--- System-Specific Context ---"
ANALYST_FACTS_LABEL = "--- Analyst-Provided Facts (Must Use) ---"


class OutputFormat(enum.StrEnum):
    markdown = "markdown"
    text = "text"


MARKDOWN_FORMAT_INSTRUCTION = (
    'When you do write a "response", render its content as valid Markdown. Preserve '
    "the content and structure requested by the system instructions; this formatting "
    "instruction does not add or remove sections."
)

TEXT_FORMAT_INSTRUCTION = (
    'When you do write a "response", render its content as plain ASCII text. Do not '
    "use Markdown syntax, smart quotes, em-dashes, or other non-ASCII characters. "
    "Preserve the content and structure requested by the system instructions, using "
    "plain ASCII equivalents such as bracketed section labels and unbulleted lines."
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
        "response": {
            "type": ["string", "null"],
            "description": (
                "One control heading followed by 2-4 cohesive implementation paragraphs; "
                "synthesize requirements without clause-by-clause sections, signposts, "
                "or applicability judgments labeled with clause letters or numbers."
            ),
        },
        "validations": {
            "type": ["array", "null"],
            "items": {"type": "string"},
            "description": "Concrete screenshot artifacts tied to claims in the response.",
        },
    },
    "required": ["needs_info", "question", "response", "validations"],
    "additionalProperties": False,
}

FOLLOWUP_INSTRUCTION = (
    'Your reply must be a JSON object with exactly four fields: "needs_info" '
    '(boolean), "question" (string or null), "response" (string or null), and '
    '"validations" (an array of strings or null). '
    "If completing this response requires information not covered by the material "
    "above or the analyst's notes -- and the gap concerns a distinct, material part "
    'of the control rather than a minor stylistic detail -- set "needs_info" to '
    'true, put one focused question in "question", and leave "response" and '
    '"validations" null. '
    'Otherwise set "needs_info" to false, leave "question" null, and put the '
    'control heading and implementation prose only in "response". Put each requested '
    'screenshot evidence suggestion in "validations" as a separate string; do not '
    'include the [Validations] heading or validation suggestions inside "response". '
    "Before setting needs_info to false, verify that every relevant fact in the "
    "Analyst-Provided Facts section is explicitly stated or directly addressed in "
    "the response. If an analyst fact means the condition for one control clause is "
    "absent, state the operational effect without labeling clauses as applicable or "
    "not applicable. Requirements that define, prohibit, or govern account and role "
    "types still apply even when one account type is not deployed. Do not characterize "
    "the entire control as not applicable. Follow all content and structure rules from "
    "the system instructions."
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
    "this response is submitted to the assessor. Also provide the requested screenshot "
    'suggestions in the "validations" array.'
)


@dataclass
class ModelReply:
    needs_info: bool
    question: str | None
    response: str | None
    validations: list[str] | None


def parse_model_reply(raw: str) -> ModelReply:
    """Parse the model's structured JSON reply.

    Falls back to treating the raw text as a final response if the model
    returns something that doesn't match the requested schema -- grammar-
    constrained decoding makes this very unlikely, but the reply is still
    external input crossing a process boundary.
    """
    try:
        data = json.loads(raw)
        validations = data["validations"]
        if validations is not None and not (
            isinstance(validations, list)
            and all(isinstance(validation, str) for validation in validations)
        ):
            raise TypeError
        return ModelReply(
            needs_info=bool(data["needs_info"]),
            question=data.get("question"),
            response=data.get("response"),
            validations=validations,
        )
    except (json.JSONDecodeError, KeyError, TypeError):
        return ModelReply(needs_info=False, question=None, response=raw, validations=None)


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
    system_sections = [instructions]
    sections: list[str] = []

    if context_notes:
        system_sections.extend(
            (
                ANALYST_FACTS_LABEL,
                context_notes,
                "These analyst-provided facts are authoritative for this response and "
                "must be explicitly reflected in the implementation narrative.",
            )
        )

    if customer_chunks:
        sections.append(CUSTOMER_LABEL)
        sections.extend(chunk.text for chunk in customer_chunks)

    sections.append(BASELINE_LABEL)
    sections.extend(chunk.text for chunk in baseline_chunks)

    if private_chunks:
        sections.append(PRIVATE_LABEL)
        sections.extend(chunk.text for chunk in private_chunks)

    sections.append(f"Control ID: {control_id}")
    sections.append(FOLLOWUP_INSTRUCTION)
    sections.append(_FORMAT_INSTRUCTIONS[output_format])

    return AssembledPrompt(system="\n\n".join(system_sections), user="\n\n".join(sections))


def assemble_chat_prompt(
    instructions: str,
    question: str,
    customer_chunks: list[RetrievedChunk],
    baseline_chunks: list[RetrievedChunk],
    private_chunks: list[RetrievedChunk],
) -> AssembledPrompt:
    sections: list[str] = []

    if customer_chunks:
        sections.append(CUSTOMER_LABEL)
        sections.extend(chunk.text for chunk in customer_chunks)

    if baseline_chunks:
        sections.append(BASELINE_LABEL)
        sections.extend(chunk.text for chunk in baseline_chunks)

    if private_chunks:
        sections.append(PRIVATE_LABEL)
        sections.extend(chunk.text for chunk in private_chunks)

    sections.append(f"Question: {question}")

    return AssembledPrompt(system=instructions, user="\n\n".join(sections))
