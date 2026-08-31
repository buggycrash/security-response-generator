"""Generation-model comparison harness using committed fictional inputs."""

from __future__ import annotations

import json
import platform
import re
import shutil
import time
from collections import Counter
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
from security_response_generator.llm.ollama_client import (
    _local_client,
    _require_local_model,
    embed_query,
    review_messages,
)

COLD_LIMIT_SECONDS = 75.0
WARM_LIMIT_SECONDS = 40.0
MIN_ARTIFACT_FREE_BYTES = 10 * 1024 * 1024
SMOKE_ESTIMATE = "approximately 10-18 minutes"
UNLOAD_TIMEOUT_SECONDS = 10.0
UNLOAD_POLL_SECONDS = 0.1
MAX_EVALUATION_RUNS = 20
MEMORY_WARNING_BYTES = 7 * 1024**3
ANALYST_INCLUSION_MAX_TOKENS = 128
ANALYST_INCLUSION_TEMPERATURE = 0.0
_RUN_DIRECTORY_RE = re.compile(r"^\d{8}_\d{6}_.+")
_RUN_MARKER = ".srg-evaluation-run"
_PLACEHOLDER_RE = re.compile(r"\[\s*PLACEHOLDER(?:\s*:[^\]]*)?\s*\]", re.IGNORECASE)

ANALYST_INCLUSION_SCHEMA = {
    "type": "object",
    "properties": {
        "included": {"type": "boolean"},
        "evidence_quote": {"type": "string"},
    },
    "required": ["included", "evidence_quote"],
    "additionalProperties": False,
}

ANALYST_INCLUSION_SYSTEM_INSTRUCTION = """Decide only whether a draft narrative
retains the recognizable, analyst-specific substance of the supplied human context.
Reasonable paraphrases and generalization count; the context need not solve the control
or repeat every qualifier, actor, or relationship. Generic control language does not
substitute for the distinguishing analyst input. Set included false only when that
substance is absent. You receive no validations or other sources. When included is true,
evidence_quote must be a short verbatim quote copied from the narrative that demonstrates
the inclusion: one sentence fragment of at most 30 words, without commentary, ellipses,
or text assembled from separate passages. When included is false, evidence_quote must be
empty. Do not explain the decision or assess any other requirement. Return only the
required JSON."""

GRADE_SCHEMA = {
    "type": "object",
    "properties": {
        "assessment": {
            "type": "string",
            "enum": ["viable", "material_edits", "not_viable", "inconclusive"],
        },
        "strengths": {"type": "array", "items": {"type": "string"}},
        "issues": {"type": "array", "items": {"type": "string"}},
        "customer_standard_coverage": {
            "type": "string",
            "enum": ["not_provided", "none", "partial", "full"],
        },
        "private_context_coverage": {
            "type": "string",
            "enum": ["not_provided", "none", "partial", "full"],
        },
        "scope": {
            "type": "string",
            "enum": ["focused", "minor_drift", "material_drift"],
        },
        "human_review_focus": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "assessment",
        "strengths",
        "issues",
        "customer_standard_coverage",
        "private_context_coverage",
        "scope",
        "human_review_focus",
    ],
    "additionalProperties": False,
}

GRADE_SYSTEM_INSTRUCTION = """You evaluate one fictional SRG control-response draft.
An independent precheck handles analyst-context inclusion; do not reassess it. Assess the
response only against the supplied customer standard, private context, NIST baseline,
exact control scope, and task rubric. A viable response must be grounded, cover the
material requirements, honor customer-standard precedence, stay within the exact control,
follow the requested narrative structure, and suggest evidence tied to its claims. Treat
unsupported operational details, wrong customer parameters, and wrong-control content as
critical issues. Do not infer model identity from prose. Classify customer-standard and
private-context coverage independently; never combine facts from one source into another
source's coverage result:
- customer_standard_coverage compares the narrative only with customer_standard.
  Choose not_provided only when that input is empty, none when no supplied customer
  requirement is reflected, partial when at least one but not all material applicable
  requirements are reflected, and full when all material applicable requirements are
  reflected. None requires not_viable; partial requires material_edits or worse.
- private_context_coverage compares the narrative only with private_context. Choose
  not_provided only when that input is empty, then none, partial, or full according to
  coverage of its material, relevant, non-conflicting details. Missing or partial private
  context requires material_edits, but cannot alone make a response not_viable. Do not
  require irrelevant, explicitly stale, or higher-precedence-conflicting private details.
The NIST baseline informs requirement coverage and the overall assessment but must not
affect either source-coverage field. For any none or partial coverage label, the issues
field must name the source field and the concrete material content that is missing or
distorted. Never request details absent from that source.
Grounded does not automatically mean relevant: a true but unrelated detail remains a
scope defect. Classify scope using these definitions:
- focused: all substantive content supports the exact control; architecture or context
  supplied in the sources remains focused when it directly explains implementation.
- minor_drift: a small unnecessary detail is present but does not materially increase
  assessor effort or address another control.
- material_drift: a substantial passage addresses another control or an unrelated
  requirement and would materially increase assessor effort or cause confusion.
Choose material_drift only when the issues field identifies the specific offending
content and explains the unrelated control or topic. If the issues field cannot identify
such content, choose focused or minor_drift. A response with material_drift can be rated
no better than material_edits. Evaluate this response independently. Source material
counts as covered only when explicitly and correctly stated in the narrative before
[Validations]; validation suggestions cannot supply or repair narrative coverage.
Preserve material actors, direction, quantities, timeframes, and relationships. This is
draft-quality evaluation, not a determination that the control is implemented. Return
only the required JSON."""


@dataclass(frozen=True)
class EvaluationCase:
    id: str
    control_id: str
    context: str
    customer_chunks: list[str]
    baseline_chunks: list[str]
    private_chunks: list[str]
    rubric: list[str]


@dataclass(frozen=True)
class TrialSpec:
    case_id: str
    seed: int
    phase: str


SMOKE_SCHEDULE = (
    TrialSpec("si5-context", 42, "cold"),
    TrialSpec("si5-context", 43, "warm"),
    TrialSpec("si5-context", 44, "warm"),
    TrialSpec("ac2-negative-fact", 42, "quality"),
    TrialSpec("sc8-enhancement-scope", 42, "quality"),
)
SMOKE_RESPONSE_TRIALS = len(SMOKE_SCHEDULE) * 2
SMOKE_GRADER_CALLS = SMOKE_RESPONSE_TRIALS * 2


@dataclass
class GenerationOutput:
    response_text: str
    model_calls: list[dict[str, Any]]
    forced_completion: bool
    monitoring_seconds: float = 0.0


@dataclass
class TrialRecord:
    role: str
    model: str
    case_id: str
    control_id: str
    seed: int
    phase: str
    wall_seconds: float
    response_text: str
    model_calls: list[dict[str, Any]]
    forced_completion: bool
    residency: dict[str, Any] | None
    embedding_residency: dict[str, Any] | None
    trial_number: int = 1
    monitoring_seconds: float = 0.0
    residency_poll_seconds: float = 0.0
    placeholder_count: int = 0


@dataclass
class GradeRecord:
    case_id: str
    response_a_role: str
    response_b_role: str
    parsed: dict[str, Any] | None
    raw: str | dict[str, str]
    trial_number: int = 1
    seed: int | None = None
    phase: str = "quality"
    analyst_checks: dict[str, Any] = field(default_factory=dict)


@dataclass
class EvaluationResult:
    candidate_model: str
    comparison_model: str
    grader_model: str
    profile: str
    output_dir: Path
    trials: list[TrialRecord]
    grades: list[GradeRecord]
    status: str = "completed"
    incomplete_operation: dict[str, Any] | None = None
    pruned_runs: list[str] = field(default_factory=list)


class EvaluationInterrupted(Exception):
    """Raised after a user interrupt has been preserved as a partial run."""

    def __init__(
        self,
        output_dir: Path,
        *,
        artifact_error: str | None = None,
        cleanup_error: str | None = None,
    ) -> None:
        super().__init__("Model evaluation interrupted by user.")
        self.output_dir = output_dir
        self.artifact_error = artifact_error
        self.cleanup_error = cleanup_error


class ModelEjectionError(OSError):
    """Raised when Ollama does not keep required evaluation models resident."""


def load_smoke_cases() -> list[EvaluationCase]:
    path = files("security_response_generator").joinpath("evaluation_data/smoke.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("profile") != "smoke" or not isinstance(data.get("cases"), list):
        raise ValueError("The bundled smoke evaluation profile is invalid.")
    rubric = data.get("rubric")
    if (
        not isinstance(rubric, list)
        or not rubric
        or not all(isinstance(item, str) and item.strip() for item in rubric)
    ):
        raise ValueError("The bundled smoke evaluation rubric is invalid.")
    return [EvaluationCase(**item, rubric=list(rubric)) for item in data["cases"]]


def normalize_model_name(model: str) -> str:
    value = model.strip().lower()
    if not value:
        raise ValueError("Model names must not be empty.")
    final_component = value.rsplit("/", maxsplit=1)[-1]
    return value if ":" in final_component else f"{value}:latest"


def _model_matches(requested: str, actual: str | None) -> bool:
    return actual is not None and normalize_model_name(requested) == normalize_model_name(actual)


def installed_model_names() -> list[str]:
    response = _local_client().list()
    return sorted(model.model for model in response.models if model.model)


def validate_preflight(candidate: str, comparison: str, grader: str, output_root: Path) -> None:
    required_models = (candidate, comparison, grader, config.EMBEDDING_MODEL)
    for model in required_models:
        _require_local_model(model)
    if normalize_model_name(candidate) == normalize_model_name(comparison):
        raise ValueError("The candidate and comparison model resolve to the same model.")
    if normalize_model_name(grader) in {
        normalize_model_name(candidate),
        normalize_model_name(comparison),
    }:
        raise ValueError(
            "A generation model under test is also the configured grader model; set "
            "SRG_REVIEW_MODEL to an independent local model for this evaluation."
        )

    installed = installed_model_names()
    missing = [
        model
        for model in required_models
        if not any(_model_matches(model, installed_name) for installed_name in installed)
    ]
    if missing:
        pulls = "\n".join(f"  ollama pull {model}" for model in missing)
        raise ValueError(f"Required local model(s) are not installed:\n{pulls}")

    load_smoke_cases()
    existing_parent = output_root
    while not existing_parent.exists() and existing_parent != existing_parent.parent:
        existing_parent = existing_parent.parent
    if shutil.disk_usage(existing_parent).free < MIN_ARTIFACT_FREE_BYTES:
        raise OSError("Less than 10 MB is available for model-evaluation artifacts.")


def _resident_process(model: str):
    for process in _local_client().ps().models:
        process_name = process.model or process.name
        if _model_matches(model, process_name):
            return process
    return None


def unload_models(models: list[str]) -> None:
    client = _local_client()
    # Record the models that actually need an unload. An absent model must not receive
    # a generate request: doing so could load it solely for the purpose of unloading it.
    resident_models = [
        model for model in dict.fromkeys(models) if _resident_process(model) is not None
    ]
    for model in resident_models:
        client.generate(model=model, prompt="", keep_alive=0)

    # Ollama can return from the zero-keep-alive request just before `ps` removes the
    # process. Poll the models we explicitly unloaded rather than treating that brief
    # transition as a failure. When nothing was resident, this loop is skipped.
    deadline = time.monotonic() + UNLOAD_TIMEOUT_SECONDS
    still_resident = resident_models
    while still_resident and time.monotonic() < deadline:
        still_resident = [
            model for model in resident_models if _resident_process(model) is not None
        ]
        if still_resident:
            time.sleep(UNLOAD_POLL_SECONDS)
    if still_resident:
        raise OSError(f"Ollama did not unload model(s): {', '.join(still_resident)}")


def _process_snapshot(process) -> dict[str, Any]:
    return {
        "model": process.model or process.name,
        "size_bytes": int(process.size) if process.size is not None else None,
        "size_vram_bytes": int(process.size_vram) if process.size_vram is not None else None,
        "context_length": process.context_length,
    }


def residency_snapshots(models: list[str]) -> dict[str, dict[str, Any] | None]:
    """Capture several model allocations from one Ollama process-table poll."""
    processes = _local_client().ps().models
    snapshots = {}
    for model in dict.fromkeys(models):
        process = next(
            (
                process
                for process in processes
                if _model_matches(model, process.model or process.name)
            ),
            None,
        )
        snapshots[model] = _process_snapshot(process) if process is not None else None
    return snapshots


def require_models_resident(models: list[str], *, checkpoint: str) -> None:
    snapshots = residency_snapshots(models)
    missing = [model for model, snapshot in snapshots.items() if snapshot is None]
    if missing:
        raise ModelEjectionError(
            f"Ollama ejected required model(s) {checkpoint}: {', '.join(missing)}"
        )


def _blind_roles(case_index: int) -> tuple[str, str]:
    return ("candidate", "comparison") if case_index % 2 == 0 else ("comparison", "candidate")


def _numbered_schedule() -> list[tuple[TrialSpec, int]]:
    case_counts: Counter[str] = Counter()
    numbered = []
    for spec in SMOKE_SCHEDULE:
        case_counts[spec.case_id] += 1
        numbered.append((spec, case_counts[spec.case_id]))
    return numbered


def _completed_grader_calls(grades: list[GradeRecord]) -> int:
    assessment_calls = sum(len(grade.raw) for grade in grades if isinstance(grade.raw, dict))
    analyst_calls = sum(
        1
        for grade in grades
        for check in grade.analyst_checks.values()
        if isinstance(check, dict) and isinstance(check.get("raw"), str)
    )
    return assessment_calls + analyst_calls


def _response_sections(response: str) -> dict[str, str]:
    narrative, separator, validations = response.partition("\n[Validations]\n")
    return {
        "narrative": narrative.strip(),
        "validations": validations.strip() if separator else "",
    }


def _grade_messages(
    case: EvaluationCase,
    response: str,
) -> list[dict]:
    payload = {
        "control_id": case.control_id,
        "customer_standard": case.customer_chunks,
        "nist_baseline": case.baseline_chunks,
        "private_context": case.private_chunks,
        "rubric": case.rubric,
        "response": _response_sections(response),
    }
    return [
        {"role": "system", "content": GRADE_SYSTEM_INSTRUCTION},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False, indent=2)},
    ]


def _analyst_inclusion_messages(case: EvaluationCase, response: str) -> list[dict]:
    payload = {
        "analyst_context": case.context,
        "narrative": _response_sections(response)["narrative"],
    }
    return [
        {"role": "system", "content": ANALYST_INCLUSION_SYSTEM_INSTRUCTION},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False, indent=2)},
    ]


def _parse_grade(raw: str) -> dict[str, Any] | None:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _normalize_evidence(value: str) -> str:
    return " ".join(value.split()).casefold()


def _parse_analyst_inclusion(raw: str, narrative: str) -> dict[str, Any]:
    parsed = _parse_grade(raw)
    if not isinstance(parsed, dict):
        return {
            "analyst_context_included": None,
            "analyst_context_evidence": "",
            "analyst_context_evidence_verified": False,
            "analyst_context_check_reason": "The analyst-inclusion output could not be parsed.",
        }

    reported_included = parsed.get("included")
    evidence = parsed.get("evidence_quote")
    evidence = evidence if isinstance(evidence, str) else ""
    if reported_included is False:
        return {
            "analyst_context_included": False,
            "analyst_context_evidence": "",
            "analyst_context_evidence_verified": None,
            "analyst_context_check_reason": (
                "The isolated analyst precheck reported that the analyst-specific "
                "substance was absent."
            ),
        }

    evidence_verified = bool(evidence.strip()) and _normalize_evidence(
        evidence
    ) in _normalize_evidence(narrative)
    return {
        "analyst_context_included": True if evidence_verified else None,
        "analyst_context_evidence": evidence,
        "analyst_context_evidence_verified": evidence_verified,
        "analyst_context_check_reason": (
            "The evidence quote was verified in the narrative."
            if evidence_verified
            else "The positive analyst-inclusion result lacked a verifiable narrative quote."
        ),
    }


def count_placeholders(response: str) -> int:
    """Count explicit SRG placeholder markers in a rendered response."""
    return len(_PLACEHOLDER_RE.findall(response))


def _apply_finding_policy(
    finding: dict[str, Any] | None, case: EvaluationCase
) -> dict[str, Any] | None:
    """Apply deterministic severity rules to independent source-coverage findings."""
    if not isinstance(finding, dict):
        return finding

    customer_coverage = finding.get("customer_standard_coverage")
    if case.customer_chunks:
        if customer_coverage == "not_provided":
            customer_coverage = "none"
    else:
        customer_coverage = "not_provided"
    finding["customer_standard_coverage"] = customer_coverage

    private_coverage = finding.get("private_context_coverage")
    if case.private_chunks:
        if private_coverage == "not_provided":
            private_coverage = "none"
    else:
        private_coverage = "not_provided"
    finding["private_context_coverage"] = private_coverage

    assessment = finding.get("assessment")
    hard_failures = []
    if finding.get("analyst_context_included") is False:
        hard_failures.append("mandatory analyst context was missing from the narrative")
    if customer_coverage == "none":
        hard_failures.append("no supplied customer-standard requirements were reflected")
    if hard_failures:
        if assessment != "not_viable":
            finding["assessment"] = "not_viable"
            finding["policy_adjustment"] = (
                f"Changed {assessment} to not_viable because " + "; ".join(hard_failures) + "."
            )
        return finding

    if finding.get("analyst_context_included") is None and assessment == "viable":
        finding["assessment"] = "inconclusive"
        finding["policy_adjustment"] = (
            "Changed viable to inconclusive because analyst-context inclusion could not "
            "be verified from a narrative evidence quote."
        )
        assessment = "inconclusive"

    material_edit_reasons = []
    if customer_coverage == "partial":
        material_edit_reasons.append("customer-standard coverage was partial")
    if private_coverage in {"none", "partial"}:
        material_edit_reasons.append(f"private-context coverage was {private_coverage}")
    if finding.get("scope") == "material_drift":
        material_edit_reasons.append("material scope drift was reported")
    if assessment in {"viable", "inconclusive"} and material_edit_reasons:
        finding["assessment"] = "material_edits"
        finding["policy_adjustment"] = (
            f"Changed {assessment} to material_edits because "
            + "; ".join(material_edit_reasons)
            + "."
        )
    return finding


def _apply_completeness_policy(
    finding: dict[str, Any] | None, trial: TrialRecord
) -> dict[str, Any] | None:
    """Apply deterministic placeholder thresholds to one generation finding."""
    if not isinstance(finding, dict):
        return finding
    assessment = finding.get("assessment")
    if trial.placeholder_count >= 2 and assessment != "not_viable":
        finding["assessment"] = "not_viable"
        finding["completeness_adjustment"] = (
            f"Changed {assessment} to not_viable because the response contains "
            f"{trial.placeholder_count} explicit placeholders."
        )
    elif trial.placeholder_count == 1 and assessment == "viable":
        finding["assessment"] = "material_edits"
        finding["completeness_adjustment"] = (
            "Changed viable to material_edits because the response contains one "
            "explicit placeholder."
        )
    return finding


def _net_generation_seconds(elapsed_seconds: float, monitoring_seconds: float) -> float:
    return max(0.0, elapsed_seconds - monitoring_seconds)


def run_smoke_evaluation(
    candidate: str,
    comparison: str,
    *,
    generate: Callable[[EvaluationCase, str, int, str], GenerationOutput],
    output_root: Path,
    on_status: Callable[[str], None] | None = None,
) -> EvaluationResult:
    cases = load_smoke_cases()
    cases_by_id = {case.id: case for case in cases}
    case_positions = {case.id: index for index, case in enumerate(cases)}
    trials: list[TrialRecord] = []
    grades: list[GradeRecord] = []
    output_dir = _create_output_dir(output_root, candidate)
    incomplete_operation: dict[str, Any] | None = None

    def current_result(status: str = "completed") -> EvaluationResult:
        return EvaluationResult(
            candidate_model=candidate,
            comparison_model=comparison,
            grader_model=config.REVIEW_MODEL,
            profile="smoke",
            output_dir=output_dir,
            trials=trials,
            grades=grades,
            status=status,
            incomplete_operation=incomplete_operation,
        )

    try:
        for role, model in (("candidate", candidate), ("comparison", comparison)):
            incomplete_operation = {
                "stage": "preparation",
                "role": role,
                "model": model,
            }
            if on_status:
                on_status(f"Preparing cold start for {role} model...")
            unload_models([candidate, comparison, config.REVIEW_MODEL])
            # The frozen suite bypasses live retrieval so every candidate receives
            # identical chunks. Keep the production embedding model resident anyway,
            # since a viable workstation default must coexist with it during normal use.
            embed_query("SRG generation-model evaluation warm-up")
            for index, (spec, case_trial_number) in enumerate(_numbered_schedule(), start=1):
                case = cases_by_id[spec.case_id]
                incomplete_operation = {
                    "stage": "generation",
                    "role": role,
                    "model": model,
                    "trial_number": index,
                    "case_id": case.id,
                    "control_id": case.control_id,
                    "seed": spec.seed,
                    "phase": spec.phase,
                }
                if on_status:
                    on_status(
                        f"Generating {role} trial {index}/{len(SMOKE_SCHEDULE)} "
                        f"({case.control_id}, {spec.phase})..."
                    )
                start = time.perf_counter()
                output = generate(case, model, spec.seed, spec.phase)
                elapsed_seconds = time.perf_counter() - start
                wall_seconds = _net_generation_seconds(elapsed_seconds, output.monitoring_seconds)
                residency_poll_start = time.perf_counter()
                snapshots = residency_snapshots([model, config.EMBEDDING_MODEL])
                residency_poll_seconds = time.perf_counter() - residency_poll_start
                model_residency = snapshots[model]
                embedding_residency = snapshots[config.EMBEDDING_MODEL]
                trials.append(
                    TrialRecord(
                        role=role,
                        model=model,
                        case_id=case.id,
                        control_id=case.control_id,
                        seed=spec.seed,
                        phase=spec.phase,
                        wall_seconds=wall_seconds,
                        response_text=output.response_text,
                        model_calls=output.model_calls,
                        forced_completion=output.forced_completion,
                        residency=model_residency,
                        embedding_residency=embedding_residency,
                        trial_number=case_trial_number,
                        monitoring_seconds=output.monitoring_seconds,
                        residency_poll_seconds=residency_poll_seconds,
                        placeholder_count=count_placeholders(output.response_text),
                    )
                )
                incomplete_operation = None
                missing = []
                if model_residency is None:
                    missing.append(model)
                if embedding_residency is None:
                    missing.append(config.EMBEDDING_MODEL)
                if missing:
                    raise ModelEjectionError(
                        "Ollama ejected required model(s) after the completed "
                        f"{role} trial: {', '.join(missing)}"
                    )

        for grade_index, (spec, case_trial_number) in enumerate(_numbered_schedule(), start=1):
            case = cases_by_id[spec.case_id]
            case_index = case_positions[case.id]
            role_a, role_b = _blind_roles(case_index)
            trial_records = {
                role: trial
                for role in ("candidate", "comparison")
                for trial in trials
                if trial.role == role
                and trial.case_id == case.id
                and trial.trial_number == case_trial_number
            }
            raw_findings: dict[str, str] = {}
            parsed_findings: dict[str, Any] = {"response_a": None, "response_b": None}
            analyst_checks: dict[str, Any] = {}
            grade_record = GradeRecord(
                case_id=case.id,
                response_a_role=role_a,
                response_b_role=role_b,
                parsed=parsed_findings,
                raw=raw_findings,
                trial_number=case_trial_number,
                seed=spec.seed,
                phase=spec.phase,
                analyst_checks=analyst_checks,
            )
            grades.append(grade_record)
            for response_index, (label, role) in enumerate(
                (("response_a", role_a), ("response_b", role_b)), start=1
            ):
                response_call_index = ((grade_index - 1) * 2) + response_index
                analyst_call_index = (response_call_index * 2) - 1
                narrative = _response_sections(trial_records[role].response_text)["narrative"]
                incomplete_operation = {
                    "stage": "analyst_inclusion",
                    "case_id": case.id,
                    "control_id": case.control_id,
                    "trial_number": case_trial_number,
                    "seed": spec.seed,
                    "phase": spec.phase,
                    "blinded_response": label,
                }
                if on_status:
                    on_status(
                        f"Checking analyst context for {case.control_id} trial "
                        f"{case_trial_number} {label.replace('_', ' ').title()} "
                        f"({analyst_call_index}/{SMOKE_GRADER_CALLS})..."
                    )
                analyst_raw = review_messages(
                    _analyst_inclusion_messages(case, trial_records[role].response_text),
                    response_format=ANALYST_INCLUSION_SCHEMA,
                    num_predict=ANALYST_INCLUSION_MAX_TOKENS,
                    temperature=ANALYST_INCLUSION_TEMPERATURE,
                )
                analyst_check = _parse_analyst_inclusion(analyst_raw, narrative)
                analyst_checks[label] = {"raw": analyst_raw, "parsed": analyst_check}

                incomplete_operation["stage"] = "grading"
                if on_status:
                    on_status(
                        f"Grading {case.control_id} trial {case_trial_number} "
                        f"{label.replace('_', ' ').title()} independently "
                        f"({analyst_call_index + 1}/{SMOKE_GRADER_CALLS})..."
                    )
                raw = review_messages(
                    _grade_messages(case, trial_records[role].response_text),
                    response_format=GRADE_SCHEMA,
                )
                raw_findings[label] = raw
                finding = _parse_grade(raw)
                if isinstance(finding, dict):
                    finding.update(analyst_check)
                finding = _apply_finding_policy(finding, case)
                parsed_findings[label] = _apply_completeness_policy(finding, trial_records[role])
            incomplete_operation = None
    except KeyboardInterrupt as exc:
        if on_status:
            on_status("Interrupt received; preserving completed work...")
        partial_result = current_result(status="interrupted")
        artifact_error = None
        cleanup_error = None
        try:
            write_artifacts(partial_result, cases)
        except OSError as write_exc:
            artifact_error = str(write_exc)
        try:
            unload_models([candidate, comparison, config.REVIEW_MODEL])
        except Exception as cleanup_exc:
            cleanup_error = str(cleanup_exc)
        interruption_note = [
            "Model evaluation interrupted by user.",
            f"Completed response trials: {len(trials)}/{len(SMOKE_SCHEDULE) * 2}",
            f"Completed grader calls: {_completed_grader_calls(grades)}/{SMOKE_GRADER_CALLS}",
            "The incomplete operation is excluded from recorded timings and conclusions.",
            f"Incomplete operation: {json.dumps(incomplete_operation, ensure_ascii=False)}",
            (
                f"Evaluation-model cleanup failed: {cleanup_error}"
                if cleanup_error
                else "Evaluation-model cleanup completed."
            ),
        ]
        try:
            output_dir.joinpath("INTERRUPTED.txt").write_text(
                "\n".join(interruption_note) + "\n", encoding="utf-8"
            )
            partial_result.pruned_runs = [str(path) for path in prune_evaluation_runs(output_root)]
            if partial_result.pruned_runs and artifact_error is None:
                write_artifacts(partial_result, cases)
        except OSError as write_exc:
            artifact_error = artifact_error or str(write_exc)
        raise EvaluationInterrupted(
            output_dir,
            artifact_error=artifact_error,
            cleanup_error=cleanup_error,
        ) from exc
    except Exception as exc:
        partial_result = current_result(status="failed")
        cleanup_error = None
        if isinstance(exc, ModelEjectionError):
            try:
                unload_models([candidate, comparison, config.REVIEW_MODEL])
            except Exception as cleanup_exc:
                cleanup_error = str(cleanup_exc)
        try:
            write_artifacts(partial_result, cases)
            error_lines = [f"{type(exc).__name__}: {exc}"]
            if cleanup_error:
                error_lines.append(f"Evaluation-model cleanup failed: {cleanup_error}")
            output_dir.joinpath("ERROR.txt").write_text(
                "\n".join(error_lines) + "\n", encoding="utf-8"
            )
            partial_result.pruned_runs = [str(path) for path in prune_evaluation_runs(output_root)]
            if partial_result.pruned_runs:
                write_artifacts(partial_result, cases)
        except OSError:
            pass
        raise OSError(f"Evaluation failed; partial artifacts are in {output_dir}: {exc}") from exc

    result = current_result()
    write_artifacts(result, cases)
    result.pruned_runs = [str(path) for path in prune_evaluation_runs(output_root)]
    if result.pruned_runs:
        write_artifacts(result, cases)
    return result


def _slug_model(model: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9._-]+", "_", model).strip("_.-")
    return value or "model"


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


def prune_evaluation_runs(output_root: Path, keep: int = MAX_EVALUATION_RUNS) -> list[Path]:
    """Delete recognized SRG run directories older than the newest ``keep``.

    Recognition requires both SRG's timestamped naming pattern and either the
    run marker used by current versions or a results file written by older
    versions. Symlinks and unrelated directories are never removed.
    """
    if keep < 1:
        raise ValueError("At least one model-evaluation run must be retained.")
    root = output_root.resolve()
    if not root.is_dir():
        return []
    runs = []
    for path in root.iterdir():
        if path.is_symlink() or not path.is_dir() or not _RUN_DIRECTORY_RE.fullmatch(path.name):
            continue
        if not (path.joinpath(_RUN_MARKER).is_file() or path.joinpath("results.json").is_file()):
            continue
        resolved = path.resolve()
        if resolved.parent == root:
            runs.append(resolved)
    runs.sort(key=lambda path: (path.stat().st_mtime_ns, path.name), reverse=True)
    removed = runs[keep:]
    for path in removed:
        shutil.rmtree(path)
    return removed


def _performance(result: EvaluationResult, role: str) -> dict[str, Any]:
    cold = [
        trial.wall_seconds
        for trial in result.trials
        if trial.role == role and trial.phase == "cold"
    ]
    warm = [
        trial.wall_seconds
        for trial in result.trials
        if trial.role == role and trial.phase == "warm"
    ]
    timed_trials = [
        trial for trial in result.trials if trial.role == role and trial.phase in {"cold", "warm"}
    ]
    residency_ok = bool(timed_trials) and all(
        trial.residency is not None and trial.embedding_residency is not None
        for trial in timed_trials
    )
    passed = (
        bool(cold and warm)
        and all(value < COLD_LIMIT_SECONDS for value in cold)
        and all(value < WARM_LIMIT_SECONDS for value in warm)
        and residency_ok
    )
    return {
        "passed": passed,
        "cold": cold,
        "warm": warm,
        "residency": "yes" if residency_ok else "no",
    }


def _average_bytes(values: list[int]) -> int | None:
    return round(sum(values) / len(values)) if values else None


def _format_bytes(value: int | None) -> str:
    if value is None:
        return "unknown"
    gibibyte = 1024**3
    mebibyte = 1024**2
    if value >= gibibyte:
        return f"{value / gibibyte:.2f} GiB"
    return f"{value / mebibyte:.0f} MiB"


def _memory_summary(result: EvaluationResult, role: str) -> dict[str, Any]:
    samples = [trial for trial in result.trials if trial.role == role and trial.residency]
    model_sizes = [
        trial.residency["size_bytes"]
        for trial in samples
        if trial.residency.get("size_bytes") is not None
    ]
    gpu_sizes = [
        trial.residency["size_vram_bytes"]
        for trial in samples
        if trial.residency.get("size_vram_bytes") is not None
    ]
    combined_sizes = [
        trial.residency["size_bytes"] + trial.embedding_residency["size_bytes"]
        for trial in samples
        if trial.residency.get("size_bytes") is not None
        and trial.embedding_residency is not None
        and trial.embedding_residency.get("size_bytes") is not None
    ]
    gpu_observations = [
        trial.residency["size_vram_bytes"] >= trial.residency["size_bytes"]
        for trial in samples
        if trial.residency.get("size_bytes") is not None
        and trial.residency.get("size_vram_bytes") is not None
    ]
    if len(gpu_observations) != len(samples) or not samples:
        stayed_on_gpu = "unknown"
    elif all(gpu_observations):
        stayed_on_gpu = f"yes ({len(samples)}/{len(samples)})"
    else:
        stayed_on_gpu = f"no ({sum(gpu_observations)}/{len(samples)} full)"
    return {
        "samples": len(samples),
        "average_model": _average_bytes(model_sizes),
        "peak_model": max(model_sizes) if model_sizes else None,
        "average_gpu": _average_bytes(gpu_sizes),
        "average_combined": _average_bytes(combined_sizes),
        "stayed_on_gpu": stayed_on_gpu,
    }


def _timing_cell(values: list[float], limit: float, *, color: bool) -> Text:
    if not values:
        return Text("missing", style="bold red" if color else None)

    cell = Text()
    for index, value in enumerate(values):
        if index:
            cell.append(", ")
        style = "bold dark_orange3" if color and value >= limit else None
        cell.append(f"{value:.1f}s", style=style)
    return cell


def _role_assessment(grade: GradeRecord, role: str) -> str:
    if grade.parsed is None:
        return "inconclusive"
    label = "response_a" if grade.response_a_role == role else "response_b"
    finding = grade.parsed.get(label, {})
    return (
        finding.get("assessment", "inconclusive") if isinstance(finding, dict) else "inconclusive"
    )


def _aggregate_assessment(assessments: list[str]) -> str:
    if not assessments:
        return "inconclusive"
    for assessment in ("not_viable", "material_edits", "inconclusive"):
        if assessment in assessments:
            return assessment
    return "viable" if all(value == "viable" for value in assessments) else "inconclusive"


def _assessment_counts(assessments: list[str]) -> str:
    counts = Counter(assessments)
    order = ("viable", "material_edits", "not_viable", "inconclusive")
    return ", ".join(f"{counts[value]} {value}" for value in order if counts[value])


def _preference_rank(assessments: list[str]) -> tuple[int, int, int, int]:
    """Rank a model's trial distribution, with viable count always dominant."""
    counts = Counter(assessments)
    return (
        counts["viable"],
        -counts["not_viable"],
        -counts["inconclusive"],
        -counts["material_edits"],
    )


def _case_grade_summaries(result: EvaluationResult) -> list[dict[str, Any]]:
    summaries = []
    for case_id in dict.fromkeys(grade.case_id for grade in result.grades):
        case_grades = [grade for grade in result.grades if grade.case_id == case_id]
        assessments = {
            role: [_role_assessment(grade, role) for grade in case_grades]
            for role in ("candidate", "comparison")
        }
        aggregates = {role: _aggregate_assessment(values) for role, values in assessments.items()}
        ranks = {role: _preference_rank(values) for role, values in assessments.items()}
        if ranks["candidate"] != ranks["comparison"]:
            preferred_role = max(ranks, key=ranks.get)
        else:
            preferred_role = "tie"
        summaries.append(
            {
                "case_id": case_id,
                "assessments": assessments,
                "aggregates": aggregates,
                "preferred_role": preferred_role,
            }
        )
    return summaries


def _review_priorities(
    result: EvaluationResult, summaries: list[dict[str, Any]]
) -> list[dict[str, str]]:
    model_for_role = {
        "candidate": result.candidate_model,
        "comparison": result.comparison_model,
    }
    priorities = []
    for summary in summaries:
        case_grades = [grade for grade in result.grades if grade.case_id == summary["case_id"]]
        for role in ("candidate", "comparison"):
            grouped_trials: dict[str, list[str]] = {}
            adjusted_trials = []
            for grade in case_grades:
                assessment = _role_assessment(grade, role)
                if assessment != "viable":
                    grouped_trials.setdefault(assessment, []).append(str(grade.trial_number))
                if grade.parsed is not None:
                    label = "response_a" if grade.response_a_role == role else "response_b"
                    finding = grade.parsed.get(label, {})
                    if isinstance(finding, dict) and finding.get("policy_adjustment"):
                        adjusted_trials.append(str(grade.trial_number))
            review_trials = [
                f"{', '.join(trial_numbers)} ({assessment})"
                for assessment, trial_numbers in grouped_trials.items()
            ]
            if review_trials or adjusted_trials:
                priorities.append(
                    {
                        "case_id": summary["case_id"],
                        "model": model_for_role[role],
                        "review_trials": "; ".join(review_trials) or "-",
                        "policy_adjustments": ", ".join(adjusted_trials) or "-",
                    }
                )
    return priorities


def _completeness_rows(result: EvaluationResult) -> list[dict[str, Any]]:
    model_for_role = {
        "candidate": result.candidate_model,
        "comparison": result.comparison_model,
    }
    rows = []
    for grade in result.grades:
        for role in ("candidate", "comparison"):
            label = "response_a" if grade.response_a_role == role else "response_b"
            finding = grade.parsed.get(label) if grade.parsed is not None else None
            trial = next(
                (
                    trial
                    for trial in result.trials
                    if trial.role == role
                    and trial.case_id == grade.case_id
                    and trial.trial_number == grade.trial_number
                ),
                None,
            )
            if trial is None or not isinstance(finding, dict):
                continue
            analyst_included = finding.get("analyst_context_included")
            customer_coverage = finding.get("customer_standard_coverage")
            private_coverage = finding.get("private_context_coverage")
            if not (
                analyst_included is not True
                or customer_coverage in {"none", "partial"}
                or private_coverage in {"none", "partial"}
                or trial.placeholder_count > 0
                or trial.forced_completion
            ):
                continue
            rows.append(
                {
                    "case_id": grade.case_id,
                    "model": model_for_role[role],
                    "trial_number": grade.trial_number,
                    "analyst_included": analyst_included,
                    "customer_coverage": customer_coverage,
                    "private_coverage": private_coverage,
                    "placeholder_count": trial.placeholder_count,
                    "forced_completion": trial.forced_completion,
                    "assessment": finding.get("assessment", "inconclusive"),
                }
            )
    return rows


def render_summary(result: EvaluationResult, *, color: bool = False) -> str:
    buffer = StringIO()
    report_console = Console(
        file=buffer,
        width=150,
        force_terminal=color,
        color_system="256" if color else None,
        highlight=False,
    )
    report_console.print("SMOKE EVALUATION - NOT A MODEL-QUALIFICATION RESULT")
    report_console.print(f"Grader model: {result.grader_model}")
    report_console.print("Evaluation target: generation model; review/revision pipeline disabled")
    if result.status != "completed":
        report_console.print(
            Text(
                f"Run status: {result.status.upper()} - partial results only; "
                "the incomplete operation is excluded.",
                style="bold yellow" if color else None,
            )
        )
    report_console.print()

    performance_table = Table(title="Performance", box=box.SIMPLE_HEAVY)
    performance_table.add_column("Model")
    performance_table.add_column(f"Cold (<{COLD_LIMIT_SECONDS:g}s)")
    performance_table.add_column(f"Warm runs (<{WARM_LIMIT_SECONDS:g}s)")
    performance_table.add_column("With embedding")
    performance_table.add_column("Result")
    for role, model in (
        ("candidate", result.candidate_model),
        ("comparison", result.comparison_model),
    ):
        performance = _performance(result, role)
        result_label = (
            "INCOMPLETE"
            if result.status != "completed"
            else "PASS"
            if performance["passed"]
            else "FAIL"
        )
        performance_table.add_row(
            model,
            _timing_cell(performance["cold"], COLD_LIMIT_SECONDS, color=color),
            _timing_cell(performance["warm"], WARM_LIMIT_SECONDS, color=color),
            performance["residency"],
            Text(
                result_label,
                style=(
                    "bold yellow"
                    if color and result.status != "completed"
                    else "bold red"
                    if color and not performance["passed"]
                    else None
                ),
            ),
        )
    report_console.print(performance_table)

    memory_table = Table(title="Observed memory residency", box=box.SIMPLE_HEAVY)
    memory_table.add_column("Model")
    memory_table.add_column("Samples")
    memory_table.add_column("Avg model")
    memory_table.add_column("Peak model")
    memory_table.add_column("Avg GPU")
    memory_table.add_column("Avg with embedding")
    memory_table.add_column("Stayed fully on GPU")
    for role, model in (
        ("candidate", result.candidate_model),
        ("comparison", result.comparison_model),
    ):
        memory = _memory_summary(result, role)
        warn_memory = (
            memory["peak_model"] is not None and memory["peak_model"] > MEMORY_WARNING_BYTES
        )
        memory_style = "bold dark_orange3" if color and warn_memory else None
        gpu_style = (
            "bold dark_orange3" if color and not memory["stayed_on_gpu"].startswith("yes") else None
        )
        memory_table.add_row(
            model,
            str(memory["samples"]),
            Text(_format_bytes(memory["average_model"]), style=memory_style),
            Text(_format_bytes(memory["peak_model"]), style=memory_style),
            _format_bytes(memory["average_gpu"]),
            _format_bytes(memory["average_combined"]),
            Text(memory["stayed_on_gpu"], style=gpu_style),
        )
    report_console.print(memory_table)
    report_console.print(
        "Memory values are Ollama-reported allocations sampled after each generation; "
        "process-table polling is excluded from generation timings. Model allocations "
        f"over {_format_bytes(MEMORY_WARNING_BYTES)} are highlighted."
    )
    report_console.print()

    review_table = Table(title="Automated independent review", box=box.SIMPLE_HEAVY)
    review_table.add_column("Case")
    review_table.add_column("Model")
    review_table.add_column("Aggregate")
    review_table.add_column("Trial results")
    review_table.add_column("Preferred model")
    grade_summaries = _case_grade_summaries(result)
    for summary in grade_summaries:
        preferred_role = summary["preferred_role"]
        preferred_model = (
            result.candidate_model
            if preferred_role == "candidate"
            else result.comparison_model
            if preferred_role == "comparison"
            else preferred_role
        )
        for row_index, (role, model) in enumerate(
            (
                ("candidate", result.candidate_model),
                ("comparison", result.comparison_model),
            )
        ):
            assessment = summary["aggregates"][role]
            trial_results = _assessment_counts(summary["assessments"][role])
            review_table.add_row(
                summary["case_id"] if row_index == 0 else "",
                model,
                Text(
                    assessment,
                    style="bold red" if color and assessment == "not_viable" else None,
                ),
                Text(
                    trial_results,
                    style="bold red" if color and "not_viable" in trial_results else None,
                ),
                preferred_model if row_index == 0 else "",
            )
    report_console.print(review_table)

    completeness_rows = _completeness_rows(result)
    completeness_table = Table(
        title="Automated coverage and completeness checks", box=box.SIMPLE_HEAVY
    )
    completeness_table.add_column("Case")
    completeness_table.add_column("Model")
    completeness_table.add_column("Trial")
    completeness_table.add_column("Analyst")
    completeness_table.add_column("Customer")
    completeness_table.add_column("Private")
    completeness_table.add_column("Placeholders")
    completeness_table.add_column("Forced call")
    completeness_table.add_column("Result")
    if completeness_rows:
        previous_case = None
        for row in completeness_rows:
            analyst_label = (
                "included"
                if row["analyst_included"] is True
                else "missing"
                if row["analyst_included"] is False
                else "unverified"
            )
            customer_coverage = row["customer_coverage"] or "not reported"
            private_coverage = row["private_coverage"] or "not reported"
            placeholder_style = (
                "bold red"
                if color and row["placeholder_count"] >= 2
                else "bold dark_orange3"
                if color and row["placeholder_count"] == 1
                else None
            )
            completeness_table.add_row(
                row["case_id"] if row["case_id"] != previous_case else "",
                row["model"],
                str(row["trial_number"]),
                Text(
                    analyst_label,
                    style=(
                        "bold red"
                        if color and analyst_label == "missing"
                        else "bold dark_orange3"
                        if color and analyst_label == "unverified"
                        else None
                    ),
                ),
                Text(
                    customer_coverage,
                    style=(
                        "bold red"
                        if color and customer_coverage == "none"
                        else "bold dark_orange3"
                        if color and customer_coverage == "partial"
                        else None
                    ),
                ),
                Text(
                    private_coverage,
                    style=(
                        "bold dark_orange3"
                        if color and private_coverage in {"none", "partial"}
                        else None
                    ),
                ),
                Text(str(row["placeholder_count"]), style=placeholder_style),
                "yes" if row["forced_completion"] else "no",
                Text(
                    row["assessment"],
                    style=("bold red" if color and row["assessment"] == "not_viable" else None),
                ),
            )
            previous_case = row["case_id"]
    else:
        completeness_table.add_row(
            "All cases",
            "All models",
            "-",
            "included",
            "full/n/a",
            "full/n/a",
            "0",
            "no",
            "clear",
        )
    report_console.print(completeness_table)
    report_console.print(
        "Policy: analyst=missing, customer=none, or 2+ placeholders => not_viable."
    )
    report_console.print(
        "        customer=partial, private=none/partial, or one placeholder => material_edits."
    )
    report_console.print("        analyst=unverified prevents a viable result.")
    report_console.print()

    report_console.print(
        "Automated review checks requirement coverage and defects; it does not score "
        "prose quality or writing style."
    )
    report_console.print()

    review_priorities = _review_priorities(result, grade_summaries)
    priority_table = Table(title="Human review priorities", box=box.SIMPLE_HEAVY)
    priority_table.add_column("Case")
    priority_table.add_column("Model")
    priority_table.add_column("Review trials")
    priority_table.add_column("Contradictory grader findings")
    if review_priorities:
        previous_case = None
        for priority in review_priorities:
            priority_table.add_row(
                priority["case_id"] if priority["case_id"] != previous_case else "",
                priority["model"],
                priority["review_trials"],
                priority["policy_adjustments"],
            )
            previous_case = priority["case_id"]
        review_instruction = (
            "Start with review-priority trials; a contradictory grader finding means a "
            "critical finding overrode its assessment label."
        )
    elif result.status == "completed" and result.grades:
        priority_table.add_row("All cases", "All models", "None identified", "None identified")
        review_instruction = (
            "No automated review priorities were identified; human prose and style "
            "spot-checking is still required."
        )
    else:
        priority_table.add_row(
            "-", "-", "Automated review incomplete", "Automated review incomplete"
        )
        review_instruction = (
            "Automated review is incomplete; inspect the preserved responses and findings."
        )
    report_console.print(priority_table)

    output_dir = result.output_dir.resolve()
    report = buffer.getvalue().rstrip()
    paths = (
        "\n\nNext steps for the human reviewer\n"
        "Automated findings are advisory. Review the blinded responses before the answer key.\n"
        f"{review_instruction}\n"
        f"Human review:    {output_dir / 'human-review.md'}\n"
        f"Grader findings: {output_dir / 'grader-findings.md'}\n"
        f"Answer key:      {output_dir / 'answer-key.md'}\n"
        f"Full results:    {output_dir / 'results.json'}\n"
        f"Retention:       newest {MAX_EVALUATION_RUNS} runs kept; "
        f"{len(result.pruned_runs)} older run(s) removed"
    )
    return report + paths


def _jsonable_result(result: EvaluationResult) -> dict[str, Any]:
    return {
        "candidate_model": result.candidate_model,
        "comparison_model": result.comparison_model,
        "grader_model": result.grader_model,
        "profile": result.profile,
        "status": result.status,
        "incomplete_operation": result.incomplete_operation,
        "platform": platform.platform(),
        "python_version": platform.python_version(),
        "cold_limit_seconds": COLD_LIMIT_SECONDS,
        "warm_limit_seconds": WARM_LIMIT_SECONDS,
        "memory_warning_bytes": MEMORY_WARNING_BYTES,
        "pruned_runs": result.pruned_runs,
        "trials": [asdict(trial) for trial in result.trials],
        "grades": [asdict(grade) for grade in result.grades],
    }


def write_artifacts(result: EvaluationResult, cases: list[EvaluationCase]) -> None:
    result.output_dir.joinpath("results.json").write_text(
        json.dumps(_jsonable_result(result), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    result.output_dir.joinpath("summary.txt").write_text(
        render_summary(result) + "\n", encoding="utf-8"
    )

    grader_findings = [
        "# Automated independent grader findings",
        "",
        "> Advisory draft-quality findings; complete the blinded human review as well.",
        "",
    ]
    for summary in _case_grade_summaries(result):
        grader_findings.extend((f"## {summary['case_id']}", "", "Aggregate findings:", ""))
        for role in ("candidate", "comparison"):
            grader_findings.append(
                f"- {role.title()}: {summary['aggregates'][role]} "
                f"({_assessment_counts(summary['assessments'][role])})"
            )
        grader_findings.extend((f"- Preferred role: {summary['preferred_role']}", ""))
        case_grades = [grade for grade in result.grades if grade.case_id == summary["case_id"]]
        for grade in case_grades:
            grader_findings.extend(
                (
                    f"### Trial {grade.trial_number} - seed {grade.seed}, {grade.phase}",
                    "",
                )
            )
            if grade.parsed is None:
                grader_findings.extend(
                    ("The grader output could not be parsed:", "", grade.raw, "")
                )
                continue
            for label, role in (
                ("response_a", grade.response_a_role),
                ("response_b", grade.response_b_role),
            ):
                finding = grade.parsed.get(label, {})
                heading = f"#### {role.title()} ({label.replace('_', ' ').title()})"
                if not isinstance(finding, dict):
                    raw_finding = (
                        grade.raw.get(label, "") if isinstance(grade.raw, dict) else grade.raw
                    )
                    grader_findings.extend(
                        (
                            heading,
                            "",
                            "The independent grader output could not be parsed:",
                            "",
                            raw_finding or "No completed grader output.",
                            "",
                        )
                    )
                    continue
                grader_findings.extend(
                    (
                        heading,
                        "",
                        f"Assessment: {finding.get('assessment', 'inconclusive')}",
                        "",
                        "Analyst context included: "
                        f"{finding.get('analyst_context_included', 'not reported')}",
                        "",
                        "Analyst narrative evidence: "
                        f"{finding.get('analyst_context_evidence') or 'None'}",
                        "",
                        "Analyst evidence verified: "
                        f"{finding.get('analyst_context_evidence_verified', 'not reported')}",
                        "",
                        "Analyst inclusion reason: "
                        f"{finding.get('analyst_context_check_reason', 'not reported')}",
                        "",
                        "Customer standard coverage: "
                        f"{finding.get('customer_standard_coverage', 'not reported')}",
                        "",
                        "Private context coverage: "
                        f"{finding.get('private_context_coverage', 'not reported')}",
                        "",
                        f"Scope: {finding.get('scope', 'not reported')}",
                        "",
                    )
                )
                trial = next(
                    (
                        trial
                        for trial in result.trials
                        if trial.role == role
                        and trial.case_id == grade.case_id
                        and trial.trial_number == grade.trial_number
                    ),
                    None,
                )
                if trial is not None:
                    grader_findings.extend(
                        (
                            f"Explicit placeholders: {trial.placeholder_count}",
                            "",
                            f"Forced completion: {'yes' if trial.forced_completion else 'no'}",
                            "",
                        )
                    )
                if finding.get("policy_adjustment"):
                    grader_findings.extend(
                        (f"Policy adjustment: {finding['policy_adjustment']}", "")
                    )
                if finding.get("completeness_adjustment"):
                    grader_findings.extend(
                        (
                            f"Completeness adjustment: {finding['completeness_adjustment']}",
                            "",
                        )
                    )
                grader_findings.append("Strengths:")
                grader_findings.extend(
                    f"- {item}" for item in finding.get("strengths", []) or ["None reported."]
                )
                grader_findings.extend(("", "Issues:"))
                grader_findings.extend(
                    f"- {item}" for item in finding.get("issues", []) or ["None reported."]
                )
                grader_findings.extend(("", "Human review focus:"))
                grader_findings.extend(
                    f"- {item}"
                    for item in finding.get("human_review_focus", []) or ["None reported."]
                )
                grader_findings.append("")
    result.output_dir.joinpath("grader-findings.md").write_text(
        "\n".join(grader_findings) + "\n", encoding="utf-8"
    )

    responses_dir = result.output_dir / "responses"
    responses_dir.mkdir(exist_ok=True)
    for trial in result.trials:
        filename = (
            f"{trial.role}_{trial.case_id}_trial-{trial.trial_number}_"
            f"seed-{trial.seed}_{trial.phase}.md"
        )
        responses_dir.joinpath(filename).write_text(trial.response_text + "\n", encoding="utf-8")

    case_index = {case.id: index for index, case in enumerate(cases)}
    worksheet = [
        "# Blinded human review",
        "",
        "> SMOKE EVALUATION - NOT A MODEL-QUALIFICATION RESULT",
        "",
        "Review each pair before opening `answer-key.md`. Record whether A, B, neither,",
        "or both are viable SRG drafts and note any unsupported claims or material edits.",
        "",
    ]
    answer_key = [
        "# Blinded response answer key",
        "",
        f"- Candidate role: {result.candidate_model}",
        f"- Comparison role: {result.comparison_model}",
        "",
    ]
    for case in cases:
        role_a, role_b = _blind_roles(case_index[case.id])
        answer_key.append(f"- {case.id}: A = {role_a}; B = {role_b}")
        worksheet.extend((f"## {case.control_id} - {case.id}", "", "### Response A", ""))
        response_a_trials = [
            trial for trial in result.trials if trial.case_id == case.id and trial.role == role_a
        ]
        response_a_trials.sort(key=lambda trial: trial.trial_number)
        for trial in response_a_trials:
            if len(response_a_trials) > 1:
                worksheet.extend((f"#### Trial {trial.trial_number}", ""))
            worksheet.extend((trial.response_text, ""))
        worksheet.extend(("", "### Response B", ""))
        response_b_trials = [
            trial for trial in result.trials if trial.case_id == case.id and trial.role == role_b
        ]
        response_b_trials.sort(key=lambda trial: trial.trial_number)
        for trial in response_b_trials:
            if len(response_b_trials) > 1:
                worksheet.extend((f"#### Trial {trial.trial_number}", ""))
            worksheet.extend((trial.response_text, ""))
        worksheet.extend(
            (
                "",
                "**Human judgment:**",
                "",
                "**Material issues or differences:**",
                "",
            )
        )
    result.output_dir.joinpath("human-review.md").write_text(
        "\n".join(worksheet) + "\n", encoding="utf-8"
    )
    result.output_dir.joinpath("answer-key.md").write_text(
        "\n".join(answer_key) + "\n", encoding="utf-8"
    )
