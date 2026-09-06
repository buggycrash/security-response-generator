"""Deterministic scoring for `srg evaluate-reviewer`.

Pure text transforms: no I/O, no Ollama import, no model call. The whole module
is unit-testable offline, and that is deliberate. Asking an LLM to judge a
reviewer's critique would reintroduce exactly the defect this command exists to
measure, and the NLI alternative was measured and rejected (see
``docs/model-evaluation-nli-findings.md``).

Every check here corresponds to a clause of ``REVIEW_SYSTEM_INSTRUCTION``
(``generation/review.py``). Read that prompt as a list of previously observed
reviewer failures -- each "never do X" is a scar -- and this module measures
compliance with SRG's actual reviewer contract rather than a generic notion of
good reviewing.

The governing asymmetry, which is why restraint is weighted as heavily as
detection: the reviewer's critique is consumed by a *separate generator* under
the instruction to correct every valid issue (``revision_instruction``). A
missed defect leaves one flaw in the draft; an invented defect *creates* one.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

# --- Directive extraction ---------------------------------------------------

# REVIEW_SYSTEM_INSTRUCTION tells the reviewer to "phrase every instruction as
# something the generator must do (for example, 'State X explicitly,' 'Add Y,'
# 'Correct Z')". Compliant critiques are therefore lexically regular, which is
# what makes deterministic extraction viable at all.
IMPERATIVE_VERBS = (
    "add",
    "align",
    "avoid",
    "cite",
    "clarify",
    "consider",
    "correct",
    "delete",
    "describe",
    "drop",
    "eliminate",
    "ensure",
    "expand",
    "explain",
    "include",
    "limit",
    "link",
    "move",
    "omit",
    "provide",
    "quote",
    "reference",
    "remove",
    "rephrase",
    "replace",
    "restate",
    "restrict",
    "revise",
    "rewrite",
    "reword",
    "shorten",
    "specify",
    "state",
    "strike",
    "tie",
    "update",
)

_IMPERATIVE_RE = re.compile(rf"^(?:{'|'.join(IMPERATIVE_VERBS)})\b", re.IGNORECASE)

# "State" is both an imperative and, in this domain, a very common proper noun
# ("the State ISO", "State SOC"). A unit opening with a capitalized "State"
# followed by another capitalized word is a noun phrase, not an instruction.
# Directives read "State that...", "State the specific...", so a lowercase
# continuation still counts.
_STATE_NOUN_RE = re.compile(r"^State\s+[A-Z]")

# Requirements phrased about the draft rather than at the generator: "the
# response must state X", "the narrative should include Y". Lower precision
# than the imperative form, so it is tracked separately and surfaced in the
# per-item record.
_MODAL_RE = re.compile(
    r"\b(?:must|should|needs? to|is required to|ought to)\s+(?:be\s+|not\s+)?[a-z]",
    re.IGNORECASE,
)

_BULLET_RE = re.compile(r"^\s*(?:[-*+•]|\d+[.)])\s*")
_EMPHASIS_RE = re.compile(r"^(?:\*\*|__|\*|_|`)+|(?:\*\*|__|\*|_|`)+$")
# Deliberately not split on ';'. Reviewers routinely join an instruction to its
# justification with a semicolon ("Correct the window; it should say 24 hours"),
# and splitting there strips the imperative head off the directive -- which both
# misattributes false alarms to the fragment and makes the worksheet unreadable.
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")

# A critique that correctly finds nothing wrong. Recognized so a compliant
# no-op critique on a clean draft is reported as such rather than as silence.
_NO_ISSUES_RE = re.compile(
    r"\b(?:no (?:changes?|issues?|defects?|corrections?|edits?|revisions?)"
    r"(?: (?:are |is |were )?(?:needed|required|necessary|identified|found))?"
    r"|nothing to correct|no further changes|meets all|fully compliant)\b",
    re.IGNORECASE,
)

DIRECTIVE_IMPERATIVE = "imperative"
DIRECTIVE_MODAL = "modal"


@dataclass(frozen=True)
class Directive:
    """One requested change, with the pattern that identified it."""

    text: str
    kind: str


def _units(critique: str) -> list[str]:
    """Split a critique into candidate instruction units.

    Splits on lines first so bulleted and numbered critiques keep one item per
    unit, then on sentence boundaries within each line, since models routinely
    pack several instructions into one paragraph.
    """
    units: list[str] = []
    for line in critique.splitlines():
        stripped = _BULLET_RE.sub("", line).strip()
        if not stripped:
            continue
        for sentence in _SENTENCE_SPLIT_RE.split(stripped):
            cleaned = _EMPHASIS_RE.sub("", sentence.strip()).strip()
            if cleaned:
                units.append(cleaned)
    return units


def extract_directives(critique: str) -> list[Directive]:
    """Return the changes a critique asks the generator to make.

    This is the workhorse of the whole evaluation. One extractor powers three
    separate qualities: restraint (the count on a clean draft should be zero),
    over-flagging (the count against a single seeded defect), and actionability
    (whether the critique instructs at all rather than merely narrating).
    """
    directives: list[Directive] = []
    for unit in _units(critique):
        if _IMPERATIVE_RE.match(unit) and not _STATE_NOUN_RE.match(unit):
            directives.append(Directive(unit, DIRECTIVE_IMPERATIVE))
        elif _MODAL_RE.search(unit):
            directives.append(Directive(unit, DIRECTIVE_MODAL))
    return directives


def reports_no_issues(critique: str) -> bool:
    """True when the critique explicitly states the draft needs no changes."""
    return bool(_NO_ISSUES_RE.search(critique))


# --- Marker matching --------------------------------------------------------


def normalize(text: str) -> str:
    """Case-fold and collapse whitespace for marker comparison."""
    return " ".join(text.split()).casefold()


def detects(critique: str, detect_markers: list[list[str]]) -> bool:
    """True when the critique names a seeded defect.

    ``detect_markers`` is a list of alternative token groups; the defect counts
    as detected when *every* token of *any one* group appears. Alternatives
    exist because a correct critique can name the same defect several ways.

    Marker matching is not a scoring shortcut. REVIEW_SYSTEM_INSTRUCTION
    already requires the reviewer to "quote the exact missing fact ... and
    instruct the generator to add it by name", so this measures literal
    compliance with the stated contract. Its limitation -- a correct critique
    phrased entirely in paraphrase scores as a miss -- is real and documented.
    """
    haystack = normalize(critique)
    return any(
        group and all(normalize(token) in haystack for token in group) for group in detect_markers
    )


def false_alarms(critique: str, must_not_flag: list[str]) -> list[str]:
    """Return directives that demand a change to content already correct.

    A hard false positive: the reviewer is not merely verbose, it is asking the
    generator to alter a fact the sources support. Because the generator
    complies, this is the failure mode that actively damages a good draft.

    Only meaningful on the ``clean`` condition, and the harness applies it
    nowhere else. On a seeded draft a correct critique legitimately quotes the
    right value while naming the wrong one ("the draft says 72 hours but the
    standard requires 24 hours"), which would score as a false alarm here.
    """
    hits: list[str] = []
    for directive in extract_directives(critique):
        text = normalize(directive.text)
        if any(normalize(fact) in text for fact in must_not_flag):
            hits.append(directive.text)
    return hits


# --- Containment ------------------------------------------------------------

CONTROL_ID_RE = re.compile(r"\b[A-Z]{2,3}-\d+(?:\(\d+\))?\b")

# The prompt prohibits *suggesting that the response add coverage of other
# controls* -- not naming them at all. A reviewer that correctly catches scope
# creep has to say "remove this, it belongs to RA-3", so only an additive
# directive naming a foreign control is a violation. The removal check wins
# when both appear, covering "remove the RA-3 paragraph and include only
# scanning content".
_ADDITIVE_RE = re.compile(
    r"\b(?:add|adding|include|including|incorporate|incorporating"
    r"|expand|expanding|provide|providing)\b",
    re.IGNORECASE,
)
_REMOVAL_RE = re.compile(
    r"\b(?:remove|removing|delete|deleting|drop|dropping|omit|omitting"
    r"|strike|striking|eliminate|eliminating|move|moving|cut|excise)\b",
    re.IGNORECASE,
)

_ROLE_CONFUSION_RE = re.compile(
    r"\bI(?:'ll\b|\s+(?:will|shall|have\s+(?:revised|rewritten|updated)"
    r"|am\s+going\s+to|would\s+(?:revise|rewrite)))|\blet me\b",
    re.IGNORECASE,
)
_HEDGING_RE = re.compile(
    r"\b(?:most facts|largely addressed|largely present|generally"
    r"\s+(?:present|addressed|covered|acceptable)|appears?\s+to\s+be"
    r"\s+(?:mostly|largely)|seems\s+(?:mostly|largely))\b",
    re.IGNORECASE,
)
_QUESTION_RE = re.compile(r"\?")

# A critique long enough to be a rewrite rather than a critique, or one that
# reproduces a long verbatim span of the draft, has stopped being an
# instruction the generator can act on.
REWRITE_CHAR_THRESHOLD = 2500
VERBATIM_SPAN_WORDS = 12

CONTAINMENT_CATEGORIES = (
    "other_control",
    "role_confusion",
    "hedging",
    "analyst_question",
    "rewrote_draft",
)


def _base_control(control_id: str) -> str:
    """`SC-8(1)` -> `SC-8`, so naming the parent control is not a violation."""
    return control_id.split("(", maxsplit=1)[0]


def _shares_verbatim_span(critique: str, draft_response: str) -> bool:
    """True when the critique reproduces a long word-for-word run of the draft.

    Quoting a short phrase is required behavior; reproducing a dozen
    consecutive words means the reviewer is rewriting rather than instructing.
    """
    draft_words = normalize(draft_response).split()
    if len(draft_words) < VERBATIM_SPAN_WORDS:
        return False
    critique_text = normalize(critique)
    return any(
        " ".join(draft_words[index : index + VERBATIM_SPAN_WORDS]) in critique_text
        for index in range(len(draft_words) - VERBATIM_SPAN_WORDS + 1)
    )


def foreign_control_ids(text: str, control_id: str) -> list[str]:
    """Control IDs named in `text` other than the one under test (or its parent)."""
    allowed = {control_id.upper(), _base_control(control_id).upper()}
    return [found for found in CONTROL_ID_RE.findall(text) if found.upper() not in allowed]


def suggests_other_control(critique: str, control_id: str) -> bool:
    """True when the critique asks the generator to *add* other-control coverage.

    Merely naming another control is legitimate and often required -- catching
    scope creep means saying which control the stray content belongs to. Only
    an additive instruction is the prohibited behavior, and it is the most
    consequential containment failure because the generator will comply and
    produce genuinely wrong-control content.
    """
    return any(
        foreign_control_ids(directive.text, control_id)
        and _ADDITIVE_RE.search(directive.text)
        and not _REMOVAL_RE.search(directive.text)
        for directive in extract_directives(critique)
    )


def containment_violations(critique: str, control_id: str, draft_response: str) -> list[str]:
    """Return the prompt prohibitions this critique broke.

    Each category maps to an explicit "never do X" clause in
    REVIEW_SYSTEM_INSTRUCTION.
    """
    violations: list[str] = []

    if suggests_other_control(critique, control_id):
        violations.append("other_control")
    if _ROLE_CONFUSION_RE.search(critique):
        violations.append("role_confusion")
    if _HEDGING_RE.search(critique):
        violations.append("hedging")
    if _QUESTION_RE.search(critique):
        violations.append("analyst_question")
    if len(critique) > REWRITE_CHAR_THRESHOLD or _shares_verbatim_span(critique, draft_response):
        violations.append("rewrote_draft")
    return violations


# --- Cross-item signals -----------------------------------------------------

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _token_set(text: str) -> frozenset[str]:
    return frozenset(_TOKEN_RE.findall(text.casefold()))


def critique_distinctness(critiques: list[str]) -> float:
    """1 - mean pairwise Jaccard similarity across a model's critiques.

    Catches degenerate constant output, which no per-item metric can see by
    construction: a reviewer emitting the same text for every draft scores
    normally on each individual item. A granite4.1:3b grader run produced a
    constant verdict on 60 of 60 responses, and nothing in SRG flagged it.

    Returns 1.0 for fewer than two critiques, since there is nothing to compare.
    """
    token_sets = [_token_set(critique) for critique in critiques]
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
    """True when the reviewer's output did not satisfy REVIEW_SCHEMA.

    ``parse_critique`` (``generation/review.py``) silently falls back to
    returning the raw string, so a schema failure is otherwise invisible.
    Grammar-constrained decoding makes this rare, which is exactly why it is
    worth counting when it happens.
    """
    try:
        value = json.loads(raw)["critique"]
    except (json.JSONDecodeError, KeyError, TypeError):
        return True
    return not (isinstance(value, str) and value.strip())
