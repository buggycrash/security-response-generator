"""Diagnostic timing/benchmark support for `srg benchmark`.

Pure data structures and Rich rendering consumed by cli.py's `benchmark`
command, which drives a real, locally running Ollama daemon against the
active engagement's already-ingested data. Unlike the command itself, this
module's formatting/aggregation logic is fully unit-testable offline with
synthetic data.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field

from rich.console import Console
from rich.table import Table

from security_response_generator.generation.retrieval import RetrievalTiming

NS_PER_SECOND = 1_000_000_000

# Heuristic only, used to flag (not decide) likely cold loads -- a warm
# no-op load_duration is normally near-zero; a genuine model load takes
# multiple seconds.
COLD_LOAD_THRESHOLD_SECONDS = 1.0


def _ns_to_seconds(value: int | None) -> float | None:
    return None if value is None else value / NS_PER_SECOND


@dataclass
class ModelCallTiming:
    label: str
    model: str | None
    total_seconds: float | None
    load_seconds: float | None
    prompt_eval_seconds: float | None
    eval_seconds: float | None
    prompt_eval_count: int | None
    eval_count: int | None

    @classmethod
    def from_response(cls, label: str, response: Mapping) -> "ModelCallTiming":
        return cls(
            label=label,
            model=response.get("model"),
            total_seconds=_ns_to_seconds(response.get("total_duration")),
            load_seconds=_ns_to_seconds(response.get("load_duration")),
            prompt_eval_seconds=_ns_to_seconds(response.get("prompt_eval_duration")),
            eval_seconds=_ns_to_seconds(response.get("eval_duration")),
            prompt_eval_count=response.get("prompt_eval_count"),
            eval_count=response.get("eval_count"),
        )

    @property
    def likely_cold_load(self) -> bool | None:
        if self.load_seconds is None:
            return None
        return self.load_seconds >= COLD_LOAD_THRESHOLD_SECONDS


@dataclass
class ControlRunTiming:
    control_id: str
    iteration: int
    wall_seconds: float
    embedding: ModelCallTiming | None
    retrieval_embedding_seconds: float
    retrieval_chroma_seconds: float
    retrieval_chroma_by_collection: dict[str, float]
    model_calls: list[ModelCallTiming]
    forced_completion: bool
    has_baseline_match: bool

    @property
    def retrieval_wall_seconds(self) -> float:
        return self.retrieval_embedding_seconds + self.retrieval_chroma_seconds

    @property
    def model_calls_seconds(self) -> float:
        return sum(call.total_seconds or 0.0 for call in self.model_calls)

    @property
    def other_seconds(self) -> float:
        """Residual covering prompt assembly, JSON parsing, caveat/labeling --
        phases already known to be cheap, pure Python and therefore not
        separately instrumented. A surprisingly large value here would
        itself be worth a follow-up look.
        """
        accounted = self.retrieval_wall_seconds + self.model_calls_seconds
        return max(0.0, self.wall_seconds - accounted)


@dataclass
class BenchmarkReport:
    collection_load_seconds: float
    runs: list[ControlRunTiming] = field(default_factory=list)


class Recorder:
    """One instance per control run. Collects timing via the callbacks
    threaded through retrieve_for_control / _generate_control_response.

    Labels generation vs. review calls by a per-kind running counter (not
    fixed position), since forced-completion/blank-response retries can add
    an extra generation call before review even starts -- see cli.py's
    _run_conversation_tracked.
    """

    def __init__(self) -> None:
        self.retrieval_timing: RetrievalTiming | None = None
        self.embed_response: Mapping | None = None
        self.model_calls: list[ModelCallTiming] = []
        self._gen_count = 0
        self._review_count = 0

    def on_retrieval_timing(self, timing: RetrievalTiming) -> None:
        self.retrieval_timing = timing

    def on_embed_response(self, response: Mapping) -> None:
        self.embed_response = response

    def on_generation_response(self, response: Mapping) -> None:
        self._gen_count += 1
        label = "draft" if self._gen_count == 1 else f"revision (pass {self._gen_count - 1})"
        self.model_calls.append(ModelCallTiming.from_response(f"generation: {label}", response))

    def on_review_response(self, response: Mapping) -> None:
        self._review_count += 1
        self.model_calls.append(
            ModelCallTiming.from_response(f"review: pass {self._review_count}", response)
        )


def build_run_timing(
    control_id: str,
    iteration: int,
    wall_seconds: float,
    recorder: Recorder,
    *,
    forced_completion: bool,
    has_baseline_match: bool,
) -> ControlRunTiming:
    timing = recorder.retrieval_timing
    return ControlRunTiming(
        control_id=control_id,
        iteration=iteration,
        wall_seconds=wall_seconds,
        embedding=(
            ModelCallTiming.from_response("embedding", recorder.embed_response)
            if recorder.embed_response is not None
            else None
        ),
        retrieval_embedding_seconds=timing.embedding_seconds if timing else 0.0,
        retrieval_chroma_seconds=timing.chroma_seconds if timing else 0.0,
        retrieval_chroma_by_collection=timing.chroma_seconds_by_collection if timing else {},
        model_calls=recorder.model_calls,
        forced_completion=forced_completion,
        has_baseline_match=has_baseline_match,
    )


def _fmt_seconds(value: float | None) -> str:
    return "-" if value is None else f"{value:.3f}s"


def _fmt_count(value: int | None) -> str:
    return "-" if value is None else str(value)


def _run_table(run: ControlRunTiming) -> Table:
    title = f"{run.control_id} (iteration {run.iteration})"
    flags = []
    if not run.has_baseline_match:
        flags.append("no baseline match")
    if run.forced_completion:
        flags.append("forced completion")
    if flags:
        title += f" [{', '.join(flags)}]"

    table = Table(title=title)
    table.add_column("Phase")
    table.add_column("Model")
    table.add_column("Total")
    table.add_column("Load")
    table.add_column("Prompt eval")
    table.add_column("Eval")
    table.add_column("Prompt tok")
    table.add_column("Eval tok")
    table.add_column("Cold?")

    if run.embedding is not None:
        table.add_row(
            "Embedding",
            run.embedding.model or "-",
            _fmt_seconds(run.embedding.total_seconds),
            _fmt_seconds(run.embedding.load_seconds),
            _fmt_seconds(run.embedding.prompt_eval_seconds),
            _fmt_seconds(run.embedding.eval_seconds),
            _fmt_count(run.embedding.prompt_eval_count),
            _fmt_count(run.embedding.eval_count),
            "yes" if run.embedding.likely_cold_load else "",
        )
    else:
        table.add_row(
            "Embedding (wall)", "-", _fmt_seconds(run.retrieval_embedding_seconds), *(["-"] * 6)
        )

    for name, seconds in run.retrieval_chroma_by_collection.items():
        table.add_row(f"Chroma: {name}", "-", _fmt_seconds(seconds), *(["-"] * 6))
    table.add_row("Chroma: total", "-", _fmt_seconds(run.retrieval_chroma_seconds), *(["-"] * 6))
    table.add_row("Retrieval: total", "-", _fmt_seconds(run.retrieval_wall_seconds), *(["-"] * 6))

    for call in run.model_calls:
        table.add_row(
            call.label,
            call.model or "-",
            _fmt_seconds(call.total_seconds),
            _fmt_seconds(call.load_seconds),
            _fmt_seconds(call.prompt_eval_seconds),
            _fmt_seconds(call.eval_seconds),
            _fmt_count(call.prompt_eval_count),
            _fmt_count(call.eval_count),
            "yes" if call.likely_cold_load else "",
        )

    table.add_row(
        "Other (assembly/parsing/labeling)", "-", _fmt_seconds(run.other_seconds), *(["-"] * 6)
    )
    table.add_row(
        "TOTAL (wall-clock)", "-", _fmt_seconds(run.wall_seconds), *(["-"] * 6), style="bold"
    )
    return table


def _summary_table(report: BenchmarkReport) -> Table:
    table = Table(title="Summary")
    table.add_column("Control")
    table.add_column("Iter")
    table.add_column("Total")
    table.add_column("Retrieval")
    table.add_column("Generation")
    table.add_column("Review")
    table.add_column("Other")

    for run in report.runs:
        generation_seconds = sum(
            call.total_seconds or 0.0
            for call in run.model_calls
            if call.label.startswith("generation")
        )
        review_seconds = sum(
            call.total_seconds or 0.0 for call in run.model_calls if call.label.startswith("review")
        )
        table.add_row(
            run.control_id,
            str(run.iteration),
            _fmt_seconds(run.wall_seconds),
            _fmt_seconds(run.retrieval_wall_seconds),
            _fmt_seconds(generation_seconds),
            _fmt_seconds(review_seconds),
            _fmt_seconds(run.other_seconds),
        )
    return table


def _print_findings(report: BenchmarkReport, console: Console) -> None:
    """Empirically flag likely model-swap thrashing: a later call to a model
    that already ran warm earlier in the same request, but that now shows a
    cold-load-sized load_duration anyway. This only fires when the numbers
    actually show the pattern -- it is an observation drawn from real
    per-call data, not an assumption baked into the tool.
    """
    found_any = False
    for run in report.runs:
        seen_models: dict[str, float] = {}
        for call in run.model_calls:
            if call.model is None or call.load_seconds is None:
                continue
            if call.model in seen_models and call.likely_cold_load:
                console.print(
                    f"[yellow]{run.control_id} (iteration {run.iteration}):[/yellow] "
                    f"'{call.label}' shows a load_duration of {call.load_seconds:.1f}s, "
                    f"comparable to a cold load, even though '{call.model}' already ran "
                    f"warm earlier in this same request (load_duration "
                    f"{seen_models[call.model]:.1f}s then). This suggests Ollama may be "
                    "evicting/reloading models on every swap between the generation and "
                    "reviewer models. Run `ollama ps` during a `--review` request to see "
                    "whether both models stay resident, and check whether either is "
                    "spilling to system RAM/CPU instead of GPU (a common cause on memory-"
                    "constrained systems)."
                )
                found_any = True
            seen_models[call.model] = call.load_seconds

    if not found_any:
        console.print(
            "\nNo evidence of repeated cold loads between generation/reviewer model swaps."
        )


def render_report(report: BenchmarkReport, console: Console) -> None:
    console.print(f"\nCollection load: {report.collection_load_seconds:.3f}s\n")
    for run in report.runs:
        console.print(_run_table(run))
        console.print()
    console.print(_summary_table(report))
    _print_findings(report, console)
