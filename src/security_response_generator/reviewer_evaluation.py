"""Atomic reviewer-model comparison for ``srg evaluate-reviewer``.

Each decision supplies exactly one authoritative requirement and one mock
generated statement. The reviewer classifies their relationship as supported,
missing required information, contradictory, or unsupported. Optional feedback
is retained for human inspection but never scored.
"""

from __future__ import annotations

import json
import shutil
import statistics
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime
from importlib.resources import files
from io import StringIO
from pathlib import Path
from typing import Any

from rich import box
from rich.console import Console
from rich.table import Table
from rich.text import Text

from security_response_generator import config
from security_response_generator.benchmark import ModelCallTiming
from security_response_generator.llm.ollama_client import (
    embed_query,
    load_model,
    review_messages,
)
from security_response_generator.model_evaluation import (
    MIN_ARTIFACT_FREE_BYTES,
    _format_bytes,
    _slug_model,
    installed_model_names,
    normalize_model_name,
    prune_evaluation_runs,
    residency_snapshots,
    unload_models,
)
from security_response_generator.reviewer_evaluation_scoring import (
    CLASSIFICATIONS,
    EXPECTED_CLASSIFICATIONS,
    critique_distinctness,
    parse_failed,
    parse_review,
)

_RUN_MARKER = ".srg-reviewer-evaluation-run"
MAX_REVIEWER_EVALUATION_RUNS = 20
REVIEWER_SUITE_VERSION = 4

CONDITIONS = (
    "clean",
    "missing_required_information",
    "incorrect_required_information",
    "unsupported_claim",
    "wrong_control_content",
)

CLEAN_CONDITION = "clean"

EVALUATION_REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "classification": {"type": "string", "enum": list(CLASSIFICATIONS)},
        "constructive_feedback": {
            "type": "string",
            "description": "Optional unscored explanation for human inspection.",
        },
    },
    "required": ["classification"],
    "additionalProperties": False,
}

ATOMIC_REVIEW_INSTRUCTION = """Classify the relationship between one mock generated
statement and one authoritative requirement. The requirement is the complete factual and
scope basis for this check. Use no outside knowledge and assess nothing else.

Choose exactly one classification:
- supported: the statement faithfully expresses the requirement's material meaning.
- missing_required_information: the statement addresses the requirement but omits a material
  qualifier or value.
- contradicts_requirement: the statement conflicts with a material qualifier or value.
- adds_unsupported_information: the statement asserts a fact outside the supplied requirement,
  including content belonging to another control.

Faithful paraphrases and acronym expansions are supported. Do not judge style, tone, or
alternative valid wording. An unrelated statement is adds_unsupported_information; do not
also report that it fails to repeat the requirement. You may include constructive_feedback
to explain your choice, but it is retained only for a human and is never scored. Return only
the JSON object required by the response schema."""

# Hard ceiling on a single critique. Without one, a reviewer that fails to stop
# generates until it exhausts num_ctx, at which point Ollama begins shifting the
# context window and the request never returns -- observed with phi4-mini, which
# decoded 59,000 tokens over 15m49s on a normal-sized draft before the client
# gave up. In an unattended batch a single such model stalls the whole
# run and produces no measurement at all.
#
# Sizing this is subtler than it looks. Ollama counts *hidden reasoning* against
# num_predict but reports only content tokens in eval_count, so a
# thinking-capable reviewer can spend the entire budget reasoning and return
# empty content, which is invalid but produces no useful comparison.
# gemma4:e2b-it-qat emits roughly 5,000 characters (~1,400 tokens) of reasoning
# before its first content token, and a 1024 ceiling starved it on 10 of 16 drafts.
#
# Thinking is left enabled because production `--review` leaves it enabled;
# suppressing it would measure a configuration nobody runs. The ceiling instead
# sits well above reasoning-plus-critique for the models tested, so it bounds a
# runaway without starving a deliberative reviewer. Hitting it is recorded as a
# failure: a reviewer that cannot stop is a bad reviewer.
REVIEWER_MAX_TOKENS = 3072

# Upper end assumes a reviewer that repeatedly runs to REVIEWER_MAX_TOKENS;
# a well-behaved one finishes near the lower end.
SMOKE_ESTIMATE = "about 5-15 minutes"


class ReviewerEvaluationInterrupted(Exception):
    """Raised after a user interrupt has been preserved as a partial run."""

    def __init__(self, output_dir: Path, artifact_error: Exception | None = None):
        super().__init__("Reviewer evaluation interrupted.")
        self.output_dir = output_dir
        self.artifact_error = artifact_error


@dataclass(frozen=True)
class DraftItem:
    """One generated statement carrying at most one known defect."""

    condition: str
    mock_generated_draft: str


@dataclass(frozen=True)
class ReviewerCase:
    id: str
    control_id: str
    grounding_information: str
    drafts: list[DraftItem]
    description: str = ""
    tags: tuple[str, ...] = ()


@dataclass
class CritiqueRecord:
    """One reviewer call and everything deterministically derived from it."""

    model: str
    role: str
    case_id: str
    control_id: str
    condition: str
    seed: int
    raw: str
    wall_seconds: float
    timing: dict[str, Any] | None = None
    residency: dict[str, dict[str, Any] | None] = field(default_factory=dict)

    # Deterministic scoring.
    classification: str | None = None
    expected_classification: str | None = None
    classification_correct: bool = False
    constructive_feedback: str = ""
    schema_failed: bool = False
    hit_token_ceiling: bool = False
    thinking_chars: int = 0

    @property
    def produced_nothing(self) -> bool:
        """The reviewer returned no content at all.

        Distinct from a malformed critique: there is nothing to score. Tracked
        separately so an empty response can never be mistaken for restraint,
        which would reward the failure it actually represents.
        """
        return not self.raw.strip()


@dataclass(frozen=True)
class ReviewerProfile:
    name: str
    label: str
    data_file: str
    seeds: tuple[int, ...]
    estimate: str

    def load_cases(self) -> list[ReviewerCase]:
        return load_cases(self.data_file)

    def item_count(self, cases: list[ReviewerCase]) -> int:
        return sum(len(case.drafts) for case in cases) * len(self.seeds)


PROFILES: dict[str, ReviewerProfile] = {
    "smoke": ReviewerProfile(
        name="smoke",
        label=(
            "ATOMIC REVIEWER EVALUATION - SMOKE\n"
            "Development feedback on classification quality; not qualification evidence"
        ),
        data_file="reviewer_critique_smoke.json",
        seeds=(42,),
        estimate=SMOKE_ESTIMATE,
    ),
}

DEFAULT_PROFILE = "smoke"


def load_cases(data_file: str) -> list[ReviewerCase]:
    """Load the frozen critique corpus shipped with the package."""
    payload = json.loads(
        files("security_response_generator.evaluation_data")
        .joinpath(data_file)
        .read_text(encoding="utf-8")
    )
    if payload.get("suite_version") != REVIEWER_SUITE_VERSION:
        raise ValueError(f"Unsupported reviewer suite version: {payload.get('suite_version')!r}.")
    cases = []
    for raw_case in payload["cases"]:
        metadata = raw_case.get("metadata", {})
        cases.append(
            ReviewerCase(
                id=raw_case["id"],
                control_id=raw_case["control_id"],
                grounding_information=raw_case["grounding_information"],
                description=metadata.get("description", ""),
                tags=tuple(metadata.get("tags", [])),
                drafts=[
                    DraftItem(
                        condition=raw_draft["condition"],
                        mock_generated_draft=raw_draft["mock_generated_draft"],
                    )
                    for raw_draft in raw_case["drafts"]
                    if raw_draft["condition"] in CONDITIONS
                ],
            )
        )
    return cases


def assemble_evaluation_review_messages(
    case: ReviewerCase, item: DraftItem
) -> list[dict[str, str]]:
    """Build the isolated one-requirement, one-statement review request."""
    payload = {
        "control_id": case.control_id,
        "grounding_information": case.grounding_information,
        "mock_generated_draft": item.mock_generated_draft,
    }
    return [
        {"role": "system", "content": ATOMIC_REVIEW_INSTRUCTION},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False, indent=2)},
    ]


def _hit_ceiling(timing: dict[str, Any] | None) -> bool:
    """True when the reviewer generated right up to `REVIEWER_MAX_TOKENS`.

    Ollama does not report *why* generation stopped, so reaching the ceiling is
    inferred from the token count. A healthy critique stops far short of it, so
    landing exactly on it means the model was still going when it was cut off.
    """
    if not timing:
        return False
    eval_count = timing.get("eval_count")
    return eval_count is not None and eval_count >= REVIEWER_MAX_TOKENS


def score_record(record: CritiqueRecord, case: ReviewerCase, item: DraftItem) -> CritiqueRecord:
    """Compare one parsed classification with the frozen expected answer."""
    record.schema_failed = parse_failed(record.raw)
    record.expected_classification = EXPECTED_CLASSIFICATIONS[item.condition]
    parsed = parse_review(record.raw)
    if parsed is not None:
        record.classification = parsed.classification
        record.constructive_feedback = parsed.constructive_feedback
        record.classification_correct = parsed.classification == record.expected_classification
    return record


@dataclass
class ReviewerEvaluationResult:
    candidate_model: str
    comparison_model: str
    profile: str
    output_dir: Path
    generation_model: str
    embedding_model: str
    records: list[CritiqueRecord]
    cases: list[ReviewerCase]
    status: str = "completed"
    incomplete_operation: dict[str, Any] | None = None
    pruned_runs: list[str] = field(default_factory=list)

    def for_role(self, role: str) -> list[CritiqueRecord]:
        return [record for record in self.records if record.role == role]

    @property
    def roles(self) -> tuple[tuple[str, str], ...]:
        return (
            ("candidate", self.candidate_model),
            ("comparison", self.comparison_model),
        )


def validate_preflight(
    candidate: str,
    comparison: str,
    output_root: Path,
    *,
    profile: str = DEFAULT_PROFILE,
) -> None:
    """Fail before any model call when the run cannot possibly succeed."""
    if profile not in PROFILES:
        raise ValueError(f"Unknown reviewer-evaluation profile '{profile}'.")
    if normalize_model_name(candidate) == normalize_model_name(comparison):
        raise ValueError("The candidate and comparison reviewer resolve to the same model.")

    # The generation and embedding models are loaded but never prompted: the
    # run measures whether a candidate reviewer can stay resident alongside
    # them, which is the memory number that actually governs a review pass.
    required = (candidate, comparison, config.GENERATION_MODEL, config.EMBEDDING_MODEL)
    installed = installed_model_names()
    missing = [
        model
        for model in dict.fromkeys(required)
        if not any(normalize_model_name(model) == normalize_model_name(name) for name in installed)
    ]
    if missing:
        pulls = "\n".join(f"  ollama pull {model}" for model in missing)
        raise ValueError(f"Required local model(s) are not installed:\n{pulls}")

    PROFILES[profile].load_cases()
    existing_parent = output_root
    while not existing_parent.exists() and existing_parent != existing_parent.parent:
        existing_parent = existing_parent.parent
    if shutil.disk_usage(existing_parent).free < MIN_ARTIFACT_FREE_BYTES:
        raise OSError("Less than 10 MB is available for reviewer-evaluation artifacts.")


def _create_output_dir(output_root: Path, candidate: str) -> Path:
    output_root = output_root.resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base = output_root / f"{timestamp}_{_slug_model(candidate)}"
    target = base
    suffix = 2
    while target.exists():
        target = Path(f"{base}_{suffix}")
        suffix += 1
    target.mkdir()
    target.joinpath(_RUN_MARKER).touch()
    return target


def prune_reviewer_evaluation_runs(output_root: Path) -> list[Path]:
    """Keep only the newest recognized reviewer-evaluation run directories."""
    return prune_evaluation_runs(
        output_root,
        keep=MAX_REVIEWER_EVALUATION_RUNS,
        run_marker=_RUN_MARKER,
    )


def run_evaluation(
    candidate: str,
    comparison: str,
    output_root: Path,
    *,
    profile: str = DEFAULT_PROFILE,
    on_status: Callable[[str], None] | None = None,
    critique: Callable[..., str] = review_messages,
) -> ReviewerEvaluationResult:
    """Critique every fixture statement with both reviewers and score the results.

    ``critique`` is injected so tests can drive the whole loop offline.
    """
    reviewer_profile = PROFILES[profile]
    cases = reviewer_profile.load_cases()
    output_dir = _create_output_dir(output_root, candidate)
    records: list[CritiqueRecord] = []
    incomplete_operation: dict[str, Any] | None = None
    tracked = [config.GENERATION_MODEL, config.EMBEDDING_MODEL]

    def current_result(status: str = "completed") -> ReviewerEvaluationResult:
        return ReviewerEvaluationResult(
            candidate_model=candidate,
            comparison_model=comparison,
            profile=profile,
            output_dir=output_dir,
            generation_model=config.GENERATION_MODEL,
            embedding_model=config.EMBEDDING_MODEL,
            records=records,
            cases=cases,
            status=status,
            incomplete_operation=incomplete_operation,
        )

    total = reviewer_profile.item_count(cases) * 2
    call_index = 0
    try:
        for role, model in (("candidate", candidate), ("comparison", comparison)):
            incomplete_operation = {"stage": "preparation", "role": role, "model": model}
            if on_status:
                on_status(f"Preparing {role} reviewer...")
            # Keep the embedding model resident between reviewer blocks. It is
            # immediately warmed below and its cold-start timing is not measured.
            # Unloading it here added a needless Ollama scheduler transition and
            # intermittently aborted runs when that transition exceeded the
            # unload timeout. A reviewer that is itself the configured embedding
            # model is still included through candidate/comparison.
            unload_models([candidate, comparison, config.GENERATION_MODEL])
            # Reproduce a real review pass: the generation and embedding models
            # are resident throughout, so the reviewer's cost is measured under
            # the memory pressure it will actually face.
            embed_query("SRG reviewer-model evaluation warm-up")
            load_model(config.GENERATION_MODEL)

            for seed in reviewer_profile.seeds:
                for case in cases:
                    for item in case.drafts:
                        call_index += 1
                        incomplete_operation = {
                            "stage": "critique",
                            "role": role,
                            "model": model,
                            "case_id": case.id,
                            "control_id": case.control_id,
                            "condition": item.condition,
                            "seed": seed,
                        }
                        if on_status:
                            on_status(
                                f"Critiquing {case.control_id} {item.condition} "
                                f"({call_index}/{total})..."
                            )
                        timings: list[dict[str, Any]] = []
                        thinking: list[str] = []

                        def observe(response, _timings=timings, _thinking=thinking):
                            _timings.append(
                                asdict(ModelCallTiming.from_response("review", response))
                            )
                            # Hidden reasoning is billed against num_predict but
                            # excluded from eval_count, so it is invisible in the
                            # timing block. Capture it directly.
                            message = response.get("message") or {}
                            _thinking.append(message.get("thinking") or "")

                        start = time.perf_counter()
                        raw = critique(
                            assemble_evaluation_review_messages(case, item),
                            response_format=EVALUATION_REVIEW_SCHEMA,
                            num_predict=REVIEWER_MAX_TOKENS,
                            model=model,
                            seed=seed,
                            on_response=observe,
                        )
                        wall_seconds = time.perf_counter() - start
                        record = CritiqueRecord(
                            model=model,
                            role=role,
                            case_id=case.id,
                            control_id=case.control_id,
                            condition=item.condition,
                            seed=seed,
                            raw=raw,
                            wall_seconds=wall_seconds,
                            timing=timings[-1] if timings else None,
                            residency=residency_snapshots([model, *tracked]),
                            hit_token_ceiling=_hit_ceiling(timings[-1] if timings else None),
                            thinking_chars=len(thinking[-1]) if thinking else 0,
                        )
                        records.append(score_record(record, case, item))
                        incomplete_operation = None
    except KeyboardInterrupt as exc:
        if on_status:
            on_status("Interrupt received; preserving completed work...")
        partial = current_result(status="interrupted")
        partial.pruned_runs = [str(path) for path in prune_reviewer_evaluation_runs(output_root)]
        artifact_error: Exception | None = None
        try:
            write_artifacts(partial)
        except OSError as error:  # pragma: no cover - filesystem failure path
            artifact_error = error
        raise ReviewerEvaluationInterrupted(output_dir, artifact_error) from exc
    except Exception as exc:
        partial = current_result(status="failed")
        partial.pruned_runs = [str(path) for path in prune_reviewer_evaluation_runs(output_root)]
        artifact_error: str | None = None
        try:
            write_artifacts(partial)
            output_dir.joinpath("ERROR.txt").write_text(
                f"{type(exc).__name__}: {exc}\n", encoding="utf-8"
            )
        except OSError as error:  # pragma: no cover - filesystem failure path
            artifact_error = str(error)
        message = f"Reviewer evaluation failed; partial artifacts are in {output_dir}: {exc}"
        if artifact_error:
            message += f" (artifact error: {artifact_error})"
        raise OSError(message) from exc

    result = current_result()
    result.pruned_runs = [str(path) for path in prune_reviewer_evaluation_runs(output_root)]
    write_artifacts(result)
    return result


# --- Aggregation ------------------------------------------------------------


@dataclass
class RoleSummary:
    """Everything the report shows for one reviewer, all deterministic."""

    role: str
    model: str
    items: int
    correct: int
    incorrect: int
    invalid_decisions: int
    schema_failures: int
    token_ceiling_hits: int
    empty_outputs: int
    mean_thinking_chars: int
    distinctness: float | None
    distinctness_samples: int
    mean_seconds: float
    mean_critique_chars: int
    median_seconds: float
    max_seconds: float
    median_response_chars: int
    max_response_chars: int
    peak_reviewer_bytes: int | None
    peak_coexistence_bytes: int | None
    coexistence_samples: int
    coexistence_intact: int


def _peak(values: list[int | None]) -> int | None:
    present = [value for value in values if value is not None]
    return max(present) if present else None


def summarize_role(result: ReviewerEvaluationResult, role: str, model: str) -> RoleSummary:
    records = result.for_role(role)

    reviewer_sizes: list[int | None] = []
    coexistence: list[int | None] = []
    intact = 0
    for record in records:
        if not record.residency:
            continue
        reviewer_snapshot = record.residency.get(model)
        reviewer_sizes.append(reviewer_snapshot.get("size_bytes") if reviewer_snapshot else None)
        if all(snapshot is not None for snapshot in record.residency.values()):
            sizes = [snapshot.get("size_bytes") for snapshot in record.residency.values()]
            present = [size for size in sizes if size is not None]
            coexistence.append(sum(present) if present else None)
            intact += 1

    valid_classifications = [
        record.classification
        for record in records
        if record.classification is not None and not record.schema_failed
    ]
    wall_seconds = [record.wall_seconds for record in records]
    response_chars = [len(record.raw) for record in records]

    return RoleSummary(
        role=role,
        model=model,
        items=len(records),
        correct=sum(1 for record in records if record.classification_correct),
        incorrect=sum(
            1
            for record in records
            if record.classification is not None and not record.classification_correct
        ),
        invalid_decisions=sum(record.classification is None for record in records),
        schema_failures=sum(1 for record in records if record.schema_failed),
        token_ceiling_hits=sum(1 for record in records if record.hit_token_ceiling),
        empty_outputs=sum(1 for record in records if record.produced_nothing),
        mean_thinking_chars=(
            round(sum(record.thinking_chars for record in records) / len(records)) if records else 0
        ),
        distinctness=(
            critique_distinctness(valid_classifications)
            if len(valid_classifications) >= 2
            else None
        ),
        distinctness_samples=len(valid_classifications),
        mean_seconds=(
            sum(record.wall_seconds for record in records) / len(records) if records else 0.0
        ),
        mean_critique_chars=(
            round(sum(len(record.raw) for record in records) / len(records)) if records else 0
        ),
        median_seconds=statistics.median(wall_seconds) if wall_seconds else 0.0,
        max_seconds=max(wall_seconds, default=0.0),
        median_response_chars=round(statistics.median(response_chars)) if response_chars else 0,
        max_response_chars=max(response_chars, default=0),
        peak_reviewer_bytes=_peak(reviewer_sizes),
        peak_coexistence_bytes=_peak(coexistence),
        coexistence_samples=len([record for record in records if record.residency]),
        coexistence_intact=intact,
    )


def detection_grid(result: ReviewerEvaluationResult) -> list[dict[str, Any]]:
    """Return the expected and actual classification for every fixture item.

    At smoke scale each cell is a single observation, so the report shows marks
    rather than percentages; the candidate-versus-baseline comparison carries
    the signal.
    """
    rows = []
    for case in result.cases:
        for condition in CONDITIONS:
            row: dict[str, Any] = {
                "case_id": case.id,
                "condition": condition,
                "expected": EXPECTED_CLASSIFICATIONS[condition],
            }
            for role, _model in result.roles:
                match = [
                    record
                    for record in result.for_role(role)
                    if record.case_id == case.id and record.condition == condition
                ]
                row[role] = None if not match else match[-1].classification
            rows.append(row)
    return rows


# --- Artifacts --------------------------------------------------------------


def _classification_mark(actual: str | None, expected: str) -> str:
    if actual is None:
        return "INVALID"
    return "correct" if actual == expected else f"WRONG: {actual}"


_CONDITION_PRESENTATION = {
    "clean": (
        "Clean, faithfully grounded statement",
        "Classify the faithful paraphrase as `supported`.",
    ),
    "missing_required_information": (
        "Required information omitted",
        "Classify the related but incomplete statement as `missing_required_information`.",
    ),
    "incorrect_required_information": (
        "Required information contradicted",
        "Classify the conflicting value as `contradicts_requirement`.",
    ),
    "unsupported_claim": (
        "Unsupported implementation claim inserted",
        "Classify the invented implementation claim as `adds_unsupported_information`.",
    ),
    "wrong_control_content": (
        "Content from another control inserted",
        "Classify the unrelated control claim as `adds_unsupported_information`.",
    ),
}


def _blockquote(text: str) -> list[str]:
    """Render arbitrary multiline text without letting it reshape the worksheet."""
    return [f"> {line}" if line else ">" for line in text.strip().splitlines()] or ["> _(empty)_"]


def _details(summary: str, body: list[str]) -> list[str]:
    return ["<details>", f"<summary>{summary}</summary>", "", *body, "", "</details>", ""]


def _record_outcome(record: CritiqueRecord, item: DraftItem) -> str:
    if record.schema_failed:
        return "❌ Invalid or empty reviewer response"
    return (
        "✅ Correct classification" if record.classification_correct else "❌ Wrong classification"
    )


def _render_reviewer_record(record: CritiqueRecord, item: DraftItem) -> list[str]:
    lines = [
        f"##### {record.model} — {record.role} reviewer, seed {record.seed}",
        "",
        f"**{_record_outcome(record, item)}**",
        "",
    ]
    lines.extend(
        [
            f"- **Expected classification:** `{record.expected_classification}`",
            f"- **Reviewer classification:** `{record.classification or 'invalid'}`",
            f"- **Elapsed time:** {record.wall_seconds:.1f} seconds",
        ]
    )
    if record.hit_token_ceiling:
        lines.append("- **Output warning:** reviewer reached the token ceiling")
    lines.append("")
    if record.schema_failed:
        lines.extend(
            _details(
                "Raw response (could not be rendered as a classification)",
                ["```text", record.raw.strip() or "(empty response)", "```"],
            )
        )
        return lines
    lines.extend(["**Constructive feedback (retained, not scored)**", ""])
    lines.extend(
        _blockquote(record.constructive_feedback)
        if record.constructive_feedback.strip()
        else ["_None provided._"]
    )
    lines.append("")
    return lines


def _render_critiques(result: ReviewerEvaluationResult) -> str:
    lines = [
        "# Human review of reviewer decisions",
        "",
        "This worksheet explains what each reviewer was given, what the synthetic test",
        "expected, which classification each reviewer selected, and any optional feedback",
        "it supplied. Feedback is shown verbatim but never scored. `results.json` remains",
        "the machine-readable record.",
        "",
        "## Run context",
        "",
        f"- **Candidate reviewer:** `{result.candidate_model}`",
        f"- **Comparison reviewer:** `{result.comparison_model}`",
        f"- **Profile:** `{result.profile}`",
        f"- **Run status:** `{result.status}`",
        "- **Scope:** one requirement and one generated statement per decision; no "
        "generation or revision",
        "",
        "## How SRG scores a decision",
        "",
        "- The selected classification either exactly matches the frozen answer or it does not.",
        "- Invalid or empty JSON cannot receive credit.",
        "- Constructive feedback is never interpreted, graded, or included in summary metrics.",
        "",
    ]
    for case in result.cases:
        lines.extend(
            [
                f"## Case: {case.control_id} — {case.id}",
                "",
                case.description,
                "",
                "### What the reviewer was given",
                "",
                "**Authoritative requirement**",
                "",
                *_blockquote(case.grounding_information),
                "",
            ]
        )

        for item in case.drafts:
            title, expectation = _CONDITION_PRESENTATION[item.condition]
            expected_classification = EXPECTED_CLASSIFICATIONS[item.condition]
            lines.extend(
                [
                    f"### Test: {title}",
                    "",
                    f"**Fixture condition:** `{item.condition}`",
                    "",
                    "#### What a good reviewer should do",
                    "",
                    expectation,
                    "",
                    f"- **Expected classification:** `{expected_classification}`",
                    "",
                ]
            )
            lines.extend(
                ["#### Mock generated statement", "", *_blockquote(item.mock_generated_draft), ""]
            )
            lines.extend(["#### What each reviewer did", ""])
            for role, _model in result.roles:
                matching_records = [
                    record
                    for record in result.for_role(role)
                    if record.case_id == case.id and record.condition == item.condition
                ]
                for record in matching_records:
                    lines.extend(_render_reviewer_record(record, item))
    return "\n".join(lines) + "\n"


def write_artifacts(result: ReviewerEvaluationResult) -> None:
    output_dir = result.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    payload = {
        "candidate_model": result.candidate_model,
        "comparison_model": result.comparison_model,
        "generation_model": result.generation_model,
        "embedding_model": result.embedding_model,
        "profile": result.profile,
        "suite_version": REVIEWER_SUITE_VERSION,
        "status": result.status,
        "incomplete_operation": result.incomplete_operation,
        "measures": (
            "Four-way classification accuracy for one authoritative requirement and one "
            "mock generated statement. Optional constructive feedback is retained but never "
            "scored. The generation and embedding models are loaded solely to measure "
            "reviewer coexistence."
        ),
        "evaluation_cases": [asdict(case) for case in result.cases],
        "records": [asdict(record) for record in result.records],
    }
    output_dir.joinpath("results.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    output_dir.joinpath("critiques.md").write_text(_render_critiques(result), encoding="utf-8")

    key = [
        "# Answer key",
        "",
        "The expected classification for each fixture statement. All content is fictional and",
        "belongs to the DEMO engagement.",
        "",
    ]
    for case in result.cases:
        key.extend([f"## {case.id} ({case.control_id})", ""])
        for item in case.drafts:
            expected = EXPECTED_CLASSIFICATIONS[item.condition]
            key.append(f"- `{item.condition}` → `{expected}`")
        key.append("")
    output_dir.joinpath("answer-key.md").write_text("\n".join(key) + "\n", encoding="utf-8")

    output_dir.joinpath("summary.txt").write_text(render_summary(result), encoding="utf-8")


# --- Reporting --------------------------------------------------------------


def _coexistence_text(summary: RoleSummary) -> str:
    if not summary.coexistence_samples:
        return "unknown"
    if summary.coexistence_intact < summary.coexistence_samples:
        return f"FAILED {summary.coexistence_intact}/{summary.coexistence_samples}"
    return (
        f"{summary.coexistence_intact}/{summary.coexistence_samples}, "
        f"{_format_bytes(summary.peak_coexistence_bytes)} peak"
    )


def render_summary(result: ReviewerEvaluationResult, *, color: bool = False) -> str:
    buffer = StringIO()
    console = Console(
        file=buffer,
        width=160,
        force_terminal=color,
        color_system="256" if color else None,
        highlight=False,
    )
    console.print(PROFILES[result.profile].label)
    console.print(f"Candidate reviewer:  {result.candidate_model}")
    console.print(f"Comparison reviewer: {result.comparison_model}")
    console.print(
        "Scope: one requirement and one mock generated statement per decision; "
        "no full-response review, revision, or engagement data"
    )
    if result.status != "completed":
        console.print(f"Run status: {result.status}")
    console.print()

    summaries = {role: summarize_role(result, role, model) for role, model in result.roles}

    bottom_line = Table(title="Classification accuracy", box=box.SIMPLE_HEAVY)
    bottom_line.add_column("Reviewer")
    bottom_line.add_column("Correct")
    bottom_line.add_column("Wrong")
    bottom_line.add_column("Invalid")
    for role, model in result.roles:
        summary = summaries[role]
        bottom_line.add_row(
            model,
            f"{summary.correct}/{summary.items}",
            f"{summary.incorrect}/{summary.items}",
            f"{summary.invalid_decisions}/{summary.items}",
        )
    console.print(bottom_line)
    console.print(
        "Only the four-way classification is scored. Optional constructive feedback is "
        "preserved in critiques.md but does not affect any summary metric."
    )
    console.print()

    cost = Table(title="Operational profile", box=box.SIMPLE_HEAVY)
    cost.add_column("Reviewer")
    cost.add_column("Time median/max")
    cost.add_column("Output median/max")
    cost.add_column("Hidden reasoning avg")
    cost.add_column("Token\nceiling")
    cost.add_column("Distinctness")
    cost.add_column("Gen + reviewer + embed")
    for role, model in result.roles:
        summary = summaries[role]
        cost.add_row(
            model,
            f"{summary.median_seconds:.1f}/{summary.max_seconds:.1f}s",
            f"{summary.median_response_chars}/{summary.max_response_chars} chars",
            f"{summary.mean_thinking_chars} chars",
            f"{summary.token_ceiling_hits}/{summary.items}",
            "unknown" if summary.distinctness is None else f"{summary.distinctness:.2f}",
            Text(
                _coexistence_text(summary),
                style=(
                    "bold red"
                    if color
                    and summary.coexistence_samples
                    and summary.coexistence_intact < summary.coexistence_samples
                    else None
                ),
            ),
        )
    console.print(cost)
    console.print(
        "Distinctness compares valid classification values only; feedback cannot improve it. "
        "Coexistence peak is reported only when all three models were resident."
    )
    console.print()

    classification = Table(
        title="Classification detail (one observation per cell)",
        box=box.SIMPLE_HEAVY,
    )
    classification.add_column("Case")
    classification.add_column("Scenario")
    classification.add_column("Expected")
    for _role, model in result.roles:
        classification.add_column(model)
    for row in detection_grid(result):
        classification.add_row(
            row["case_id"],
            row["condition"],
            row["expected"],
            *[_classification_mark(row[role], row["expected"]) for role, _model in result.roles],
        )
    console.print(classification)
    console.print(
        "Correct means the returned classification exactly matches the frozen answer. No "
        "action, evidence quote, or free-form feedback is graded."
    )
    console.print()

    console.print(
        "Smoke scale: one observation per scenario per case. The candidate-versus-"
        "comparison difference is the signal; individual cells are anecdotes."
    )
    console.print(
        "This measures the reviewer's decision, not what the generator does with it. "
        "The targeted smoke result is development evidence, not qualification."
    )
    console.print(f"Human worksheet:   {result.output_dir / 'critiques.md'}")
    console.print(f"Full results:       {result.output_dir / 'results.json'}")
    console.print(f"Answer key:         {result.output_dir / 'answer-key.md'}")
    console.print(
        f"Retention: newest {MAX_REVIEWER_EVALUATION_RUNS} runs kept; "
        f"{len(result.pruned_runs)} older run(s) removed"
    )
    return buffer.getvalue()
