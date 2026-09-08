"""Deterministic classification scoring for ``srg evaluate-reviewer``.

The model chooses one relationship between an authoritative requirement and a
mock generated statement.  The expected relationship is frozen in the fixture;
free-form constructive feedback is retained for humans but never interpreted or
scored by this module.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

CLASSIFICATIONS = (
    "supported",
    "missing_required_information",
    "contradicts_requirement",
    "adds_unsupported_information",
)
VALID_CLASSIFICATIONS = frozenset(CLASSIFICATIONS)

EXPECTED_CLASSIFICATIONS = {
    "clean": "supported",
    "missing_required_information": "missing_required_information",
    "incorrect_required_information": "contradicts_requirement",
    "unsupported_claim": "adds_unsupported_information",
    "wrong_control_content": "adds_unsupported_information",
}


@dataclass(frozen=True)
class ParsedReview:
    """A schema-valid atomic classification."""

    classification: str
    constructive_feedback: str = ""


def normalize(text: str) -> str:
    """Case-fold and collapse whitespace for deterministic comparison."""
    return " ".join(text.split()).casefold()


def parse_review(raw: str) -> ParsedReview | None:
    """Parse the classifier response without interpreting its optional prose."""
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(payload, dict) or "classification" not in payload:
        return None
    if not set(payload) <= {"classification", "constructive_feedback"}:
        return None
    classification = payload["classification"]
    feedback = payload.get("constructive_feedback", "")
    if classification not in VALID_CLASSIFICATIONS or not isinstance(feedback, str):
        return None
    return ParsedReview(classification=classification, constructive_feedback=feedback)


_TOKEN_RE = re.compile(r"[a-z0-9_]+")


def _token_set(text: str) -> frozenset[str]:
    return frozenset(_TOKEN_RE.findall(text.casefold()))


def critique_distinctness(classifications: list[str]) -> float:
    """Return one minus mean pairwise Jaccard similarity of valid decisions.

    Only the scored classification is compared. Optional constructive feedback
    cannot make a constant classifier appear more distinct.
    """
    token_sets = [_token_set(value) for value in classifications]
    pairs = [
        (left, right) for index, left in enumerate(token_sets) for right in token_sets[index + 1 :]
    ]
    if not pairs:
        return 1.0
    similarities = []
    for left, right in pairs:
        union = left | right
        similarities.append(len(left & right) / len(union) if union else 1.0)
    return 1.0 - (sum(similarities) / len(similarities))


def parse_failed(raw: str) -> bool:
    """Return whether the response misses the classifier schema."""
    return parse_review(raw) is None
