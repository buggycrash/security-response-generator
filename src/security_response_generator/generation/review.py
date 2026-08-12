"""Prompts and parsing for the response review/revision pipeline."""

import json

from security_response_generator.generation.prompt import AssembledPrompt

REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "critique": {
            "type": "string",
            "description": "Specific, actionable critique for the response generator.",
        }
    },
    "required": ["critique"],
    "additionalProperties": False,
}

REVIEW_SYSTEM_INSTRUCTION = """You are the quality reviewer for a draft security
control response. Check the draft strictly against the supplied generator instructions
and grounding material. Identify unsupported claims, omitted material facts, conflicts
with authoritative customer standards, missing coverage of THIS control's own clauses,
structural violations, and weak or unsupported screenshot validations.

Control scope creep: flag any material in the draft that actually belongs to a
different control ID than the one being answered (check the "Control ID:" line in the
grounding material), even if it is topically related or from the same control family.
Never suggest that the response add coverage of other controls, related clauses
outside this control, or broader material than what was requested -- answering only
the exact control asked is correct, not a gap.

Analyst fact checklist: find the "Analyst-Provided Facts (Must Use)" section in the
grounding material, if present, and check each discrete fact in it against the draft
one at a time. A fact counts as covered only if it is explicitly stated or its specific
operational effect is directly addressed -- topical overlap with the customer standard
or NIST baseline (for example, both mentioning an account-manager role) does not excuse
omitting the analyst's specific detail (a count, name, date, tool, or scope). If any
fact is missing, quote the exact missing fact in your critique and instruct the
generator to add it by name. Do not summarize this check with a vague, hedged judgment
such as "most facts are present" or "largely addressed" -- either name each missing
fact specifically or state affirmatively that all analyst facts are present.

Give concise, actionable correction instructions to the response generator. Do not
rewrite the response and do not ask the human analyst questions. Return only the
required JSON object."""


def assemble_review_messages(
    prompt: AssembledPrompt,
    candidate: str,
    conversation: list[dict] | None = None,
) -> list[dict]:
    """Give the reviewer all grounding, including clarification answers."""
    conversation_text = json.dumps(conversation or [], ensure_ascii=False)
    return [
        {"role": "system", "content": REVIEW_SYSTEM_INSTRUCTION},
        {
            "role": "user",
            "content": (
                "GENERATOR SYSTEM INSTRUCTIONS AND ANALYST FACTS:\n"
                f"{prompt.system}\n\nGROUNDING MATERIAL AND REQUEST:\n{prompt.user}\n\n"
                f"GENERATOR CONVERSATION BEFORE THIS DRAFT:\n{conversation_text}\n\n"
                f"DRAFT RESPONSE (structured JSON):\n{candidate}"
            ),
        },
    ]


def parse_critique(raw: str) -> str:
    """Extract structured critique, falling back safely for unexpected model output."""
    try:
        value = json.loads(raw)["critique"]
        return value if isinstance(value, str) and value.strip() else raw
    except (json.JSONDecodeError, KeyError, TypeError):
        return raw


def revision_instruction(critique: str, *, final: bool) -> str:
    stage = "final response" if final else "revised draft"
    return f"""A separate reviewer gave the critique below.

--- Reviewer critique ---
{critique}
--- End reviewer critique ---

Produce the {stage}, correcting every valid issue while continuing to follow the original
instructions and use only the supplied grounding material. Do not ask the human analyst
any more questions. Set needs_info to false and question to null. Return the complete
response and validations in the required JSON schema, even if the critique says no changes
are needed. Preserve clearly marked placeholders when the available facts are insufficient;
do not invent details."""
