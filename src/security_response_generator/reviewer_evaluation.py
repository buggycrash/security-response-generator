"""Harness for `srg evaluate-reviewer`.

Measures the *critique* role: the reviewer used by `srg generate --review` and
`bulk-generate`, which emits a free-text critique that a separate generator
then executes ("correcting every valid issue", ``revision_instruction``). It
does not measure the structured grader role used by ``evaluate-model``.

That consumer relationship sets the whole design. A missed defect leaves one
flaw in the draft; an invented defect *creates* one, because the generator
complies. For a reviewer, precision outranks recall -- the opposite of a
grader -- so restraint on a clean draft is weighted as heavily as detection on
a defective one.

``model_evaluation`` is deliberately neither imported wholesale nor subclassed.
Its category-derivation logic exists to *compensate* for reviewer defects, so
reusing it here would measure the compensation rather than the reviewer. Only
model-lifecycle and formatting primitives are shared.

Scope is intentionally small: one smoke profile, critique quality only. End-to-
end effect (critique -> revision -> repair or damage) is already exercised by
hand through ``srg generate --review``; see ``docs/reviewer-evaluation.md``.
"""

from __future__ import annotations

import json
import re
import shutil
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
from security_response_generator.generation.prompt import (
    AssembledPrompt,
    OutputFormat,
    assemble_prompt,
)
from security_response_generator.generation.retrieval import RetrievedChunk
from security_response_generator.generation.review import (
    REVIEW_SCHEMA,
    assemble_review_messages,
    parse_critique,
)
from security_response_generator.llm.ollama_client import (
    embed_query,
    load_model,
    review_messages,
)
from security_response_generator.model_evaluation import (
    MIN_ARTIFACT_FREE_BYTES,
    _format_bytes,
    _slug_model,
    count_narrative_validations,
    count_placeholders,
    installed_model_names,
    normalize_model_name,
    prune_evaluation_runs,
    residency_snapshots,
    unload_models,
)
from security_response_generator.reviewer_evaluation_scoring import (
    CONTAINMENT_CATEGORIES,
    containment_violations,
    critique_distinctness,
    detects,
    extract_directives,
    false_alarms,
    foreign_control_ids,
    parse_failed,
    reports_no_issues,
)

_RUN_MARKER = ".srg-reviewer-evaluation-run"

CONDITIONS = (
    "clean",
    "dropped_analyst_fact",
    "wrong_customer_parameter",
    "unsupported_claim",
    "wrong_control_content",
    "omitted_control_clause",
    "weak_validation",
    "narrative_validations",
)

# Defects defined by something being *removed*. Their detect_markers must be
# absent from the defective draft and present in the clean one; the fixture
# test pins that invariant.
OMISSION_CONDITIONS = frozenset({"dropped_analyst_fact", "omitted_control_clause"})

CLEAN_CONDITION = "clean"

# Hard ceiling on a single critique. Without one, a reviewer that fails to stop
# generates until it exhausts num_ctx, at which point Ollama begins shifting the
# context window and the request never returns -- observed with phi4-mini, which
# decoded 59,000 tokens over 15m49s on a normal-sized draft before the client
# gave up. In an unattended 32-call batch a single such model stalls the whole
# run and produces no measurement at all.
#
# Sizing this is subtler than it looks. Ollama counts *hidden reasoning* against
# num_predict but reports only content tokens in eval_count, so a
# thinking-capable reviewer can spend the entire budget reasoning and return
# empty content -- which then scores as perfect restraint and zero detection,
# quietly inverting the comparison. gemma4:e2b-it-qat emits roughly 5,000
# characters (~1,400 tokens) of reasoning before its first content token, and a
# 1024 ceiling starved it on 10 of 16 drafts.
#
# Thinking is left enabled because production `--review` leaves it enabled;
# suppressing it would measure a configuration nobody runs. The ceiling instead
# sits well above reasoning-plus-critique for the models tested, so it bounds a
# runaway without starving a deliberative reviewer. Hitting it is recorded as a
# finding: a reviewer that cannot stop is a bad reviewer.
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
    """One draft carrying at most one known defect."""

    condition: str
    draft: dict[str, Any]
    detect_markers: list[list[str]]
    must_not_flag: list[str]

    @property
    def draft_json(self) -> str:
        return json.dumps(self.draft, ensure_ascii=False, indent=2)

    @property
    def response_text(self) -> str:
        return self.draft.get("response") or ""


@dataclass(frozen=True)
class ReviewerCase:
    id: str
    control_id: str
    context: str
    customer_chunks: list[str]
    baseline_chunks: list[str]
    private_chunks: list[str]
    must_keep: list[str]
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
    critique: str
    wall_seconds: float
    timing: dict[str, Any] | None = None
    residency: dict[str, dict[str, Any] | None] = field(default_factory=dict)

    # Deterministic scoring.
    detected: bool | None = None
    directives: list[dict[str, str]] = field(default_factory=list)
    hard_false_alarms: list[str] = field(default_factory=list)
    containment: list[str] = field(default_factory=list)
    foreign_controls: list[str] = field(default_factory=list)
    schema_failed: bool = False
    no_issues_reported: bool = False
    hit_token_ceiling: bool = False
    thinking_chars: int = 0
    narrative_validations: int = 0
    placeholders: int = 0

    @property
    def directive_count(self) -> int:
        return len(self.directives)

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
            "REVIEWER CRITIQUE EVALUATION - SMOKE\n"
            "Development feedback on critique quality; not qualification evidence"
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
    cases = []
    for raw_case in payload["cases"]:
        metadata = raw_case.get("metadata", {})
        cases.append(
            ReviewerCase(
                id=raw_case["id"],
                control_id=raw_case["control_id"],
                context=raw_case["context"],
                customer_chunks=list(raw_case.get("customer_chunks", [])),
                baseline_chunks=list(raw_case.get("baseline_chunks", [])),
                private_chunks=list(raw_case.get("private_chunks", [])),
                must_keep=list(raw_case.get("must_keep", [])),
                description=metadata.get("description", ""),
                tags=tuple(metadata.get("tags", [])),
                drafts=[
                    DraftItem(
                        condition=raw_draft["condition"],
                        draft=raw_draft["draft"],
                        detect_markers=[list(group) for group in raw_draft["detect_markers"]],
                        must_not_flag=list(raw_draft.get("must_not_flag", [])),
                    )
                    for raw_draft in raw_case["drafts"]
                ],
            )
        )
    return cases


def case_prompt(case: ReviewerCase, instructions: str) -> AssembledPrompt:
    """Rebuild the exact generator prompt the reviewer sees in production.

    The reviewer's own prompt embeds the generator's system instructions and
    grounding material verbatim (``assemble_review_messages``), so a faithful
    evaluation has to reconstruct them rather than approximate them.
    """

    def chunks(values: list[str], source: str) -> list[RetrievedChunk]:
        return [
            RetrievedChunk(text=value, source_path=source, chunk_id=f"{source}::{index}")
            for index, value in enumerate(values)
        ]

    return assemble_prompt(
        instructions=instructions,
        control_id=case.control_id,
        context_notes=case.context,
        customer_chunks=chunks(case.customer_chunks, f"reviewer-eval/{case.id}/customer.md"),
        baseline_chunks=chunks(case.baseline_chunks, f"reviewer-eval/{case.id}/nist.md"),
        private_chunks=chunks(case.private_chunks, f"reviewer-eval/{case.id}/private.md"),
        output_format=OutputFormat.markdown,
    )


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


_TRUNCATED_CRITIQUE_RE = re.compile(r'"critique"\s*:\s*"(.*)', re.DOTALL)


def salvage_critique(raw: str) -> str:
    """Recover the critique text from output truncated mid-JSON.

    A reviewer cut off at the token ceiling leaves an unterminated object, so
    ``parse_critique`` falls back to returning the whole raw string -- brace,
    field name, escape sequences and all. Scoring that punctuation as critique
    prose inflates the directive count and makes a truncated reviewer look far
    more aggressive than it was.

    The truncation is still recorded as a schema failure; this only ensures the
    text that *is* scored is the reviewer's actual words.
    """
    parsed = parse_critique(raw)
    if parsed != raw:
        return parsed
    match = _TRUNCATED_CRITIQUE_RE.search(raw)
    if not match:
        return raw
    body = match.group(1)
    # Decode the JSON string body by closing it and re-parsing, so escapes
    # become the characters the reviewer meant.
    for candidate in (body, body.rstrip("\\")):
        try:
            return json.loads(f'"{candidate}"')
        except json.JSONDecodeError:
            continue
    return body


def score_record(record: CritiqueRecord, case: ReviewerCase, item: DraftItem) -> CritiqueRecord:
    """Apply every deterministic check to one critique.

    No model is consulted. An LLM judge would reintroduce exactly the defect
    being measured, and the NLI alternative was measured and rejected.
    """
    record.schema_failed = parse_failed(record.raw)
    record.no_issues_reported = reports_no_issues(record.critique)
    record.directives = [
        {"text": directive.text, "kind": directive.kind}
        for directive in extract_directives(record.critique)
    ]
    record.containment = containment_violations(
        record.critique, case.control_id, item.response_text
    )
    record.foreign_controls = sorted(set(foreign_control_ids(record.critique, case.control_id)))
    record.narrative_validations = count_narrative_validations(item.response_text)
    record.placeholders = count_placeholders(item.response_text)

    if item.condition == CLEAN_CONDITION:
        # Detection is undefined with no seeded defect; restraint is the
        # measurement here, so must_not_flag applies only on this condition.
        record.detected = None
        record.hard_false_alarms = false_alarms(record.critique, item.must_not_flag)
    else:
        record.detected = detects(record.critique, item.detect_markers)
        record.hard_false_alarms = []
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


def run_evaluation(
    candidate: str,
    comparison: str,
    instructions: str,
    output_root: Path,
    *,
    profile: str = DEFAULT_PROFILE,
    on_status: Callable[[str], None] | None = None,
    critique: Callable[..., str] = review_messages,
) -> ReviewerEvaluationResult:
    """Critique every fixture draft with both reviewers and score the results.

    ``critique`` is injected so tests can drive the whole loop offline.
    """
    reviewer_profile = PROFILES[profile]
    cases = reviewer_profile.load_cases()
    prompts = {case.id: case_prompt(case, instructions) for case in cases}
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
            unload_models([candidate, comparison, *tracked])
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
                            assemble_review_messages(prompts[case.id], item.draft_json, []),
                            response_format=REVIEW_SCHEMA,
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
                            critique=salvage_critique(raw),
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
        artifact_error: Exception | None = None
        try:
            write_artifacts(partial)
        except OSError as error:  # pragma: no cover - filesystem failure path
            artifact_error = error
        raise ReviewerEvaluationInterrupted(output_dir, artifact_error) from exc

    result = current_result()
    write_artifacts(result)
    result.pruned_runs = [str(path) for path in prune_evaluation_runs(output_root)]
    return result


# --- Aggregation ------------------------------------------------------------


@dataclass
class RoleSummary:
    """Everything the report shows for one reviewer, all deterministic."""

    role: str
    model: str
    items: int
    detected: int
    detectable: int
    clean_items: int
    clean_directives: int
    clean_silent: int
    hard_false_alarms: int
    seeded_directives: int
    seeded_items: int
    unwarranted_changes: int
    containment: dict[str, int]
    schema_failures: int
    token_ceiling_hits: int
    empty_outputs: int
    mean_thinking_chars: int
    distinctness: float
    mean_seconds: float
    mean_critique_chars: int
    peak_reviewer_bytes: int | None
    peak_coexistence_bytes: int | None
    coexistence_samples: int
    coexistence_intact: int

    @property
    def detection_rate(self) -> float:
        return self.detected / self.detectable if self.detectable else 0.0

    @property
    def unwarranted_per_find(self) -> float:
        """Unwarranted changes demanded for each real defect found.

        The headline trade. A reviewer that finds more defects while demanding
        far more unwarranted changes is the worse choice, not the better one.

        Zero when nothing was invented, even if nothing was found either: a
        reviewer that stays silent is useless but harmless, and it must not
        share a score with one that invents freely. Infinite is reserved for
        the genuinely worst case -- demanded changes, found nothing.
        """
        if not self.unwarranted_changes:
            return 0.0
        return self.unwarranted_changes / self.detected if self.detected else float("inf")

    @property
    def directives_per_clean_draft(self) -> float:
        return self.clean_directives / self.clean_items if self.clean_items else 0.0

    @property
    def directives_per_seeded_draft(self) -> float:
        return self.seeded_directives / self.seeded_items if self.seeded_items else 0.0


def _peak(values: list[int | None]) -> int | None:
    present = [value for value in values if value is not None]
    return max(present) if present else None


def summarize_role(result: ReviewerEvaluationResult, role: str, model: str) -> RoleSummary:
    records = result.for_role(role)
    clean = [record for record in records if record.condition == CLEAN_CONDITION]
    seeded = [record for record in records if record.condition != CLEAN_CONDITION]

    containment = dict.fromkeys(CONTAINMENT_CATEGORIES, 0)
    for record in records:
        for category in record.containment:
            containment[category] += 1

    reviewer_sizes: list[int | None] = []
    coexistence: list[int | None] = []
    intact = 0
    for record in records:
        if not record.residency:
            continue
        reviewer_snapshot = record.residency.get(model)
        reviewer_sizes.append(reviewer_snapshot.get("size_bytes") if reviewer_snapshot else None)
        sizes = [
            snapshot.get("size_bytes")
            for snapshot in record.residency.values()
            if snapshot is not None
        ]
        present = [size for size in sizes if size is not None]
        coexistence.append(sum(present) if present else None)
        if all(snapshot is not None for snapshot in record.residency.values()):
            intact += 1

    return RoleSummary(
        role=role,
        model=model,
        items=len(records),
        detected=sum(1 for record in seeded if record.detected),
        detectable=len(seeded),
        clean_items=len(clean),
        clean_directives=sum(record.directive_count for record in clean),
        # Silence only counts as restraint when the reviewer actually spoke.
        # A model that returned nothing requested no changes trivially, and
        # crediting that would reward the failure it represents.
        clean_silent=sum(
            1
            for record in clean
            if not record.produced_nothing
            and (record.directive_count == 0 or record.no_issues_reported)
        ),
        hard_false_alarms=sum(len(record.hard_false_alarms) for record in clean),
        seeded_directives=sum(record.directive_count for record in seeded),
        seeded_items=len(seeded),
        # Exact ground truth makes this measurable: every draft carries at most
        # one defect, so one directive is warranted when the reviewer found it
        # and none are otherwise. Anything beyond that asks the generator to
        # change something the fixture says is correct.
        unwarranted_changes=sum(
            max(0, record.directive_count - (1 if record.detected else 0)) for record in records
        ),
        containment=containment,
        schema_failures=sum(1 for record in records if record.schema_failed),
        token_ceiling_hits=sum(1 for record in records if record.hit_token_ceiling),
        empty_outputs=sum(1 for record in records if record.produced_nothing),
        mean_thinking_chars=(
            round(sum(record.thinking_chars for record in records) / len(records)) if records else 0
        ),
        distinctness=critique_distinctness([record.critique for record in records]),
        mean_seconds=(
            sum(record.wall_seconds for record in records) / len(records) if records else 0.0
        ),
        mean_critique_chars=(
            round(sum(len(record.critique) for record in records) / len(records)) if records else 0
        ),
        peak_reviewer_bytes=_peak(reviewer_sizes),
        peak_coexistence_bytes=_peak(coexistence),
        coexistence_samples=len([record for record in records if record.residency]),
        coexistence_intact=intact,
    )


def detection_grid(result: ReviewerEvaluationResult) -> list[dict[str, Any]]:
    """Per-condition hit/miss, per case, per model.

    At smoke scale each cell is a single observation, so the report shows marks
    rather than percentages; the candidate-versus-baseline comparison carries
    the signal.
    """
    rows = []
    for case in result.cases:
        for condition in CONDITIONS:
            if condition == CLEAN_CONDITION:
                continue
            row: dict[str, Any] = {"case_id": case.id, "condition": condition}
            for role, _model in result.roles:
                match = [
                    record
                    for record in result.for_role(role)
                    if record.case_id == case.id and record.condition == condition
                ]
                row[role] = None if not match else all(record.detected for record in match)
            rows.append(row)
    return rows


# --- Artifacts --------------------------------------------------------------


def _mark(value: bool | None) -> str:
    if value is None:
        return "-"
    return "found" if value else "MISSED"


def write_artifacts(result: ReviewerEvaluationResult) -> None:
    output_dir = result.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    payload = {
        "candidate_model": result.candidate_model,
        "comparison_model": result.comparison_model,
        "generation_model": result.generation_model,
        "embedding_model": result.embedding_model,
        "profile": result.profile,
        "status": result.status,
        "incomplete_operation": result.incomplete_operation,
        "measures": (
            "Critique quality only. The revision pipeline is not run; the "
            "generation and embedding models are loaded solely to measure "
            "reviewer coexistence."
        ),
        "records": [asdict(record) for record in result.records],
    }
    output_dir.joinpath("results.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    lines = [
        "# Reviewer critique worksheet",
        "",
        "Every critique verbatim, beside the defect that was seeded and the",
        "deterministic verdict. Read this to check the verdicts against your own",
        "judgment: marker matching is the load-bearing assumption of this command.",
        "",
    ]
    for case in result.cases:
        lines.extend([f"## {case.id} ({case.control_id})", "", case.description, ""])
        for item in case.drafts:
            lines.extend([f"### Condition: {item.condition}", ""])
            if item.detect_markers:
                groups = "; ".join(" + ".join(group) for group in item.detect_markers)
                lines.extend([f"Detected when the critique names any of: {groups}", ""])
            else:
                lines.extend(
                    [
                        "No seeded defect. Any requested change is an unnecessary "
                        "instruction; one naming "
                        f"{', '.join(item.must_not_flag)} is a hard false positive.",
                        "",
                    ]
                )
            for role, model in result.roles:
                for record in result.for_role(role):
                    if record.case_id != case.id or record.condition != item.condition:
                        continue
                    lines.extend(
                        [
                            f"**{model}** (seed {record.seed}) - "
                            f"detection: {_mark(record.detected)}, "
                            f"directives: {record.directive_count}, "
                            f"containment: {', '.join(record.containment) or 'clean'}",
                            "",
                            "```",
                            record.critique.strip() or "(empty critique)",
                            "```",
                            "",
                        ]
                    )
    output_dir.joinpath("critiques.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    key = [
        "# Answer key",
        "",
        "Which defect each fixture draft carries. All content is fictional and",
        "belongs to the DEMO engagement.",
        "",
    ]
    for case in result.cases:
        key.extend([f"## {case.id} ({case.control_id})", ""])
        for item in case.drafts:
            summary = (
                "no seeded defect (restraint control)"
                if item.condition == CLEAN_CONDITION
                else f"seeded defect: {item.condition}"
            )
            key.append(f"- `{item.condition}` - {summary}")
        key.append("")
    output_dir.joinpath("answer-key.md").write_text("\n".join(key) + "\n", encoding="utf-8")

    output_dir.joinpath("summary.txt").write_text(render_summary(result), encoding="utf-8")


# --- Reporting --------------------------------------------------------------


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
        "Scope: critique quality only; the revision pipeline is not run and no "
        "engagement data is used"
    )
    if result.status != "completed":
        console.print(f"Run status: {result.status}")
    console.print()

    summaries = {role: summarize_role(result, role, model) for role, model in result.roles}

    console.print(
        "The two failure modes are not equal. The generator applies every change a critique\n"
        "demands, so an unwarranted one edits a correct draft into a wrong one, while a missed\n"
        "defect merely leaves the draft as it already was. Invention is disqualifying; omission\n"
        "is tolerable. Read the first table accordingly."
    )
    console.print()

    bottom_line = Table(title="Bottom line", box=box.SIMPLE_HEAVY)
    bottom_line.add_column("Reviewer")
    bottom_line.add_column("Unwarranted changes demanded")
    bottom_line.add_column("Per real defect found")
    bottom_line.add_column("Real defects found")
    for role, model in result.roles:
        summary = summaries[role]
        ratio = summary.unwarranted_per_find
        bottom_line.add_row(
            model,
            Text(
                str(summary.unwarranted_changes),
                style="bold red" if color and summary.unwarranted_changes else None,
            ),
            Text(
                "invented, found nothing" if ratio == float("inf") else f"{ratio:.1f} : 1",
                style="bold red" if color and ratio > 1 else None,
            ),
            f"{summary.detected} of {summary.detectable}",
        )
    console.print(bottom_line)
    console.print(
        "Every fixture draft carries at most one defect, so one requested change is warranted "
        "when the reviewer found it and none are otherwise. Everything beyond that asks the "
        "generator to alter content the fixture says is already correct."
    )
    console.print(
        "A reviewer that finds more defects while demanding far more unwarranted changes is "
        "the worse choice, not the better one."
    )
    console.print()

    cost = Table(title="Cost", box=box.SIMPLE_HEAVY)
    cost.add_column("Reviewer")
    cost.add_column("Critiques")
    cost.add_column("Avg time")
    cost.add_column("Avg critique")
    cost.add_column("Avg hidden reasoning")
    cost.add_column("Peak reviewer")
    cost.add_column("Peak with gen + embed")
    cost.add_column("All three resident")
    for role, model in result.roles:
        summary = summaries[role]
        cost.add_row(
            model,
            str(summary.items),
            f"{summary.mean_seconds:.1f}s",
            f"{summary.mean_critique_chars} chars",
            f"{summary.mean_thinking_chars} chars",
            _format_bytes(summary.peak_reviewer_bytes),
            _format_bytes(summary.peak_coexistence_bytes),
            f"{summary.coexistence_intact}/{summary.coexistence_samples}",
        )
    console.print(cost)
    console.print(
        "A review pass alternates generator and reviewer up to four times per control, so "
        "the coexistence peak governs, not the reviewer's own size."
    )
    console.print(
        "Hidden reasoning is a real cost even though it never reaches the generator: it is "
        "billed against the token budget and the clock, but excluded from the critique."
    )
    console.print()

    # Invention is reported before detection on purpose. Reading detection
    # first invites ranking reviewers by defects found, which is the wrong way
    # round for this role.
    restraint = Table(
        title="Invention: changes demanded on drafts with nothing wrong", box=box.SIMPLE_HEAVY
    )
    restraint.add_column("Reviewer")
    restraint.add_column("Clean drafts")
    restraint.add_column("Correctly left alone")
    restraint.add_column("Changes demanded per clean draft")
    restraint.add_column("Demands to alter correct content")
    restraint.add_column("Changes demanded per seeded draft")
    for role, model in result.roles:
        summary = summaries[role]
        restraint.add_row(
            model,
            str(summary.clean_items),
            Text(
                f"{summary.clean_silent}/{summary.clean_items}",
                style="bold red" if color and summary.clean_silent < summary.clean_items else None,
            ),
            Text(
                f"{summary.directives_per_clean_draft:.1f}",
                style="bold red" if color and summary.directives_per_clean_draft else None,
            ),
            Text(
                str(summary.hard_false_alarms),
                style="bold red" if color and summary.hard_false_alarms else None,
            ),
            f"{summary.directives_per_seeded_draft:.1f}",
        )
    console.print(restraint)
    console.print(
        "A clean draft is fully supported by its sources, so every change demanded on one is "
        "invented. The generator will apply it, turning a correct draft into a wrong one, and "
        "no later stage catches that."
    )
    console.print(
        "The last column is the same failure on drafts that do have a defect: one demand is "
        "warranted, the rest are not."
    )
    console.print()

    detection = Table(
        title="Detection: the tolerable failure (one observation per cell)",
        box=box.SIMPLE_HEAVY,
    )
    detection.add_column("Case")
    detection.add_column("Seeded defect")
    for _role, model in result.roles:
        detection.add_column(model)
    for row in detection_grid(result):
        detection.add_row(
            row["case_id"],
            row["condition"],
            *[_mark(row[role]) for role, _model in result.roles],
        )
    console.print(detection)
    for role, model in result.roles:
        summary = summaries[role]
        console.print(
            f"  {model}: found {summary.detected} of {summary.detectable} seeded defects."
        )
    console.print(
        "A miss leaves the draft exactly as the generator wrote it, where the human review "
        "SRG already requires can still catch it. Do not trade invention for detection."
    )
    console.print()

    containment = Table(title="Containment violations", box=box.SIMPLE_HEAVY)
    containment.add_column("Reviewer")
    for category in CONTAINMENT_CATEGORIES:
        containment.add_column(category.replace("_", " "))
    for role, model in result.roles:
        summary = summaries[role]
        containment.add_row(
            model, *[str(summary.containment[category]) for category in CONTAINMENT_CATEGORIES]
        )
    console.print(containment)
    console.print(
        "Each column is an explicit prohibition in the reviewer prompt. "
        "'other control' counts only critiques that ask the generator to *add* "
        "other-control coverage; naming a control to remove stray content is correct."
    )
    console.print()

    consistency = Table(title="Consistency and output discipline", box=box.SIMPLE_HEAVY)
    consistency.add_column("Reviewer")
    consistency.add_column("Critique distinctness")
    consistency.add_column("Schema failures")
    consistency.add_column("No output at all")
    consistency.add_column("Ran to token ceiling")
    for role, model in result.roles:
        summary = summaries[role]
        consistency.add_row(
            model,
            f"{summary.distinctness:.2f}",
            str(summary.schema_failures),
            f"{summary.empty_outputs}/{summary.items}",
            f"{summary.token_ceiling_hits}/{summary.items}",
        )
    console.print(consistency)
    console.print(
        f"A critique is capped at {REVIEWER_MAX_TOKENS} tokens. Reaching that cap means the "
        "model did not stop on its own; uncapped it would generate until the context window "
        "shifted and the request never returned."
    )
    console.print(
        "'No output at all' is scored as a failure, never as restraint. A high count next to "
        "heavy hidden reasoning means the model spent its whole budget thinking, and the "
        "other columns for that reviewer describe too few critiques to compare."
    )
    console.print(
        "Distinctness near zero means the reviewer emits nearly the same critique "
        "regardless of the draft, which no per-item metric can detect."
    )
    console.print(
        "Read it beside detection: a reviewer whose one fixed critique happens to contain a "
        "marker can score a detection without having found anything, and low distinctness is "
        "the tell."
    )
    console.print()

    console.print(
        "Smoke scale: one observation per defect per case. The candidate-versus-"
        "comparison difference is the signal; individual cells are anecdotes."
    )
    console.print(
        "This measures what the reviewer says, not what the generator does with it. "
        "Thresholds are uncalibrated; treat early runs as calibration data."
    )
    console.print(f"Critique worksheet: {result.output_dir / 'critiques.md'}")
    console.print(f"Full results:       {result.output_dir / 'results.json'}")
    console.print(f"Answer key:         {result.output_dir / 'answer-key.md'}")
    if result.pruned_runs:
        console.print(f"Retention: {len(result.pruned_runs)} older run(s) removed")
    return buffer.getvalue()
