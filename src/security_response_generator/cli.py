"""Command-line interface for security-response-generator."""

import re
import time
from collections.abc import Callable, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import ollama
import typer
from rich.console import Console
from rich.progress import BarColumn, MofNCompleteColumn, Progress, TextColumn, TimeElapsedColumn

from security_response_generator import (
    benchmark,
    config,
    engagements,
    model_evaluation,
    model_evaluation_sampling,
    model_evaluation_stats,
)
from security_response_generator.generation import bulk_csv
from security_response_generator.generation.formatting import normalize_to_ascii
from security_response_generator.generation.prompt import (
    BLANK_RESPONSE_RETRY_INSTRUCTION,
    FORCED_COMPLETION_INSTRUCTION,
    RESPONSE_SCHEMA,
    AssembledPrompt,
    OutputFormat,
    assemble_chat_prompt,
    assemble_prompt,
    parse_model_reply,
)
from security_response_generator.generation.retrieval import (
    RetrievalTiming,
    RetrievedChunk,
    retrieve_for_chat,
    retrieve_for_control,
)
from security_response_generator.generation.review import (
    REVIEW_SCHEMA,
    assemble_review_messages,
    parse_critique,
    revision_instruction,
)
from security_response_generator.ingest import loaders, nist_oscal
from security_response_generator.ingest import manifest as manifest_module
from security_response_generator.ingest.chunking import chunk_text
from security_response_generator.ingest.store import (
    delete_source,
    get_client,
    get_collection,
    upsert_chunks,
)
from security_response_generator.llm.ollama_client import (
    chat_messages,
    embed_query,
    generate_messages,
    review_messages,
)

_SYSTEMIC_ERRORS = (ConnectionError, ollama.ResponseError, ValueError, OSError)

app = typer.Typer(help="Local RAG CLI for drafting security control responses.")
console = Console(stderr=True)

_INLINE_VALIDATIONS_RE = re.compile(
    r"\n\s*(?:#{1,6}\s*)?\[Validations\]\s*:?\s*\n.*\Z",
    re.IGNORECASE | re.DOTALL,
)


@app.command("update-nist")
def update_nist(
    source: str = typer.Option(
        nist_oscal.DEFAULT_NIST_OSCAL_URL,
        "--source",
        help="Official HTTPS URL or local path to a NIST SP 800-53 OSCAL JSON catalog.",
    ),
    output: Path = typer.Option(
        config.NIST_CATALOG_PATH,
        "--output",
        help="Markdown destination used by SRG's knowledge-base ingest.",
    ),
) -> None:
    """Download and convert a NIST SP 800-53 OSCAL catalog for local ingest."""
    try:
        result = nist_oscal.update_catalog(source, output)
    except nist_oscal.CatalogError as exc:
        typer.echo(f"Unable to update the NIST catalog: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(
        f"Wrote NIST SP 800-53 {result.version} to {output}\n"
        f"Converted {result.control_count} controls and "
        f"{result.enhancement_count} enhancements.\n\n"
        "Next:\n"
        "  srg ingest --source knowledge_base"
    )


@app.command()
def ingest(
    source: str = typer.Option(
        "all",
        help=(
            "Which source tier to ingest: knowledge_base, customer_standards, "
            "private_context, or all."
        ),
    ),
    rebuild: bool = typer.Option(
        False,
        "--rebuild",
        help="Wipe the active engagement's customer/private index before ingesting.",
    ),
    rebuild_baseline: bool = typer.Option(
        False,
        "--rebuild-baseline",
        help="Also wipe and re-ingest the shared NIST 800-53 baseline.",
    ),
) -> None:
    """Ingest documents from the source folders into the local vector store."""
    engagement = _active_engagement_or_exit()
    if source == "all":
        collection_names = list(config.SOURCE_NAMES)
    elif source in config.SOURCE_NAMES:
        collection_names = [source]
    else:
        typer.echo(f"Unknown source: {source}", err=True)
        raise typer.Exit(code=1)

    if rebuild and source == config.COLLECTION_KNOWLEDGE_BASE:
        typer.echo(
            "--rebuild applies to engagement data; use --rebuild-baseline for knowledge_base.",
            err=True,
        )
        raise typer.Exit(code=1)
    if rebuild_baseline and source not in ("all", config.COLLECTION_KNOWLEDGE_BASE):
        typer.echo(
            "--rebuild-baseline requires --source all or --source knowledge_base.",
            err=True,
        )
        raise typer.Exit(code=1)

    baseline_client = get_client(config.CHROMA_DIR)
    engagement_client = get_client(engagement.chroma_dir)

    rebuilt_engagement_source = None
    if rebuild:
        engagement_names = (
            (
                config.COLLECTION_CUSTOMER_STANDARDS,
                config.COLLECTION_PRIVATE_CONTEXT,
            )
            if source == "all"
            else (source,)
        )
        for name in engagement_names:
            _delete_collection_if_present(engagement_client, name)
        if source == "all" and engagement.manifest_path.exists():
            engagement.manifest_path.unlink()
        elif source != "all":
            rebuilt_engagement_source = source

    if rebuild_baseline:
        _delete_collection_if_present(baseline_client, config.COLLECTION_KNOWLEDGE_BASE)
        if config.MANIFEST_PATH.exists():
            config.MANIFEST_PATH.unlink()

    baseline_manifest = manifest_module.load_manifest(config.MANIFEST_PATH)
    engagement_manifest = manifest_module.load_manifest(engagement.manifest_path)
    if rebuilt_engagement_source:
        prefix = f"{rebuilt_engagement_source}/"
        engagement_manifest = {
            key: value for key, value in engagement_manifest.items() if not key.startswith(prefix)
        }
    source_dirs = _source_dirs(engagement)

    for name in collection_names:
        if name == config.COLLECTION_KNOWLEDGE_BASE:
            _ingest_source(
                baseline_client,
                name,
                source_dirs[name],
                baseline_manifest,
            )
        else:
            _ingest_source(
                engagement_client,
                name,
                source_dirs[name],
                engagement_manifest,
            )

    manifest_module.save_manifest(config.MANIFEST_PATH, baseline_manifest)
    manifest_module.save_manifest(engagement.manifest_path, engagement_manifest)
    _show_demo_reminder(engagement)


def _delete_collection_if_present(client, name: str) -> None:
    try:
        client.delete_collection(name=name)
    except Exception:
        pass


def _source_dirs(engagement: engagements.Engagement) -> dict[str, Path]:
    return {
        config.COLLECTION_KNOWLEDGE_BASE: config.KNOWLEDGE_BASE_DIR,
        config.COLLECTION_CUSTOMER_STANDARDS: engagement.customer_standards_dir,
        config.COLLECTION_PRIVATE_CONTEXT: engagement.private_context_dir,
    }


def _ingest_source(client, collection_name: str, source_dir: Path, manifest: dict) -> None:
    collection = get_collection(client, collection_name)
    prefix = f"{collection_name}/"

    current_files = {
        str(path.relative_to(source_dir)): manifest_module.compute_hash(path)
        for path in loaders.iter_source_files(source_dir)
    }
    previous_files = {
        key[len(prefix) :]: digest for key, digest in manifest.items() if key.startswith(prefix)
    }

    changed, _unchanged, deleted = manifest_module.diff_manifest(previous_files, current_files)

    for relative_path in deleted:
        delete_source(collection, relative_path)
        manifest.pop(f"{prefix}{relative_path}", None)

    for relative_path in changed:
        full_path = source_dir / relative_path
        delete_source(collection, relative_path)
        document = loaders.load_document(full_path, source_dir)
        chunks = chunk_text(document.text)
        _upsert_with_progress(collection, relative_path, collection_name, chunks)
        manifest[f"{prefix}{relative_path}"] = current_files[relative_path]

    typer.echo(f"{collection_name}: {len(changed)} file(s) (re)embedded, {len(deleted)} removed")


def _upsert_with_progress(collection, relative_path: str, collection_name: str, chunks) -> None:
    """Embed and store chunks for one file, showing a progress bar for large files."""
    if not chunks:
        return
    with Progress(
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
        console=console,
        transient=True,
    ) as progress:
        task = progress.add_task(f"Processing {relative_path}...", total=len(chunks))
        upsert_chunks(
            collection,
            relative_path,
            collection_name,
            chunks,
            on_batch=lambda count: progress.advance(task, count),
        )


def _build_collections(engagement: engagements.Engagement) -> dict:
    baseline_client = get_client(config.CHROMA_DIR)
    engagement_client = get_client(engagement.chroma_dir)
    return {
        config.COLLECTION_KNOWLEDGE_BASE: get_collection(
            baseline_client, config.COLLECTION_KNOWLEDGE_BASE
        ),
        config.COLLECTION_CUSTOMER_STANDARDS: get_collection(
            engagement_client, config.COLLECTION_CUSTOMER_STANDARDS
        ),
        config.COLLECTION_PRIVATE_CONTEXT: get_collection(
            engagement_client, config.COLLECTION_PRIVATE_CONTEXT
        ),
    }


def _label_response(
    response_text: str, engagement: engagements.Engagement, output_format: OutputFormat
) -> str:
    if output_format == OutputFormat.text:
        response_text = normalize_to_ascii(response_text)
        customer_label = normalize_to_ascii(engagement.response_customer_name)
        return f"Customer: {customer_label}\n\n{response_text}"
    return f"# Customer: {engagement.response_customer_name}\n\n{response_text}"


@dataclass
class ControlGenerationResult:
    response_text: str
    has_baseline_match: bool
    forced_completion: bool  # meaningless when has_baseline_match is False


def _generate_control_response(
    control_id: str,
    context: str,
    collections: dict,
    engagement: engagements.Engagement,
    output_format: OutputFormat,
    max_followups: int | None,
    review: bool,
    *,
    generation_model: str | None = None,
    generation_seed: int | None = None,
    on_retrieval_timing: Callable[[RetrievalTiming], None] | None = None,
    on_embed_response: Callable[[Mapping], None] | None = None,
    on_generation_response: Callable[[Mapping], None] | None = None,
    on_review_response: Callable[[Mapping], None] | None = None,
    set_status: Callable[[str], None] | None = None,
) -> ControlGenerationResult:
    """Run retrieval + generation for one control. Reused by `generate` (which
    treats a missing baseline match as fatal) and `bulk-generate` (which
    instead writes a note into that control's own output file).
    """
    result = retrieve_for_control(
        control_id,
        context,
        collections,
        on_timing=on_retrieval_timing,
        on_embed_response=on_embed_response,
    )

    if not result.has_baseline_match:
        note = (
            f"No matching NIST baseline content found for control ID '{control_id}' — "
            "check the ID or run `srg ingest`."
        )
        return ControlGenerationResult(
            _label_response(note, engagement, output_format), False, False
        )

    instructions = config.INSTRUCTIONS_PATH.read_text(encoding="utf-8")
    prompt = assemble_prompt(
        instructions=instructions,
        control_id=control_id,
        context_notes=context,
        customer_chunks=result.customer_chunks if result.has_customer_match else [],
        baseline_chunks=result.baseline_chunks,
        private_chunks=result.private_chunks,
        output_format=output_format,
    )

    outcome = _run_conversation_tracked(
        prompt,
        output_format,
        max_followups,
        review=review,
        generation_model=generation_model,
        generation_seed=generation_seed,
        on_generation_response=on_generation_response,
        on_review_response=on_review_response,
        set_status=set_status,
    )
    response_text = outcome.text
    if not result.has_customer_match:
        # The generator is never asked to determine or state this itself (small
        # local models handled it unreliably both ways -- sometimes omitting the
        # caveat when no standard existed, sometimes claiming one was missing when
        # it wasn't) -- SRG already knows this deterministically from retrieval,
        # so it prepends the caveat here instead.
        caveat = (
            f"[No customer- or state-specific standard was located for {control_id}. "
            "This response is based on the NIST baseline and any available system "
            "context.]"
        )
        response_text = f"{caveat}\n\n{response_text}"
    labeled = _label_response(response_text, engagement, output_format)
    return ControlGenerationResult(labeled, True, outcome.forced_completion)


@app.command()
def generate(
    control_id: str = typer.Argument(..., help="Control ID, e.g. SI-5"),
    context: str = typer.Option(
        "", "--context", help="Freeform notes about this specific control/system."
    ),
    output_format: OutputFormat = typer.Option(
        OutputFormat.markdown,
        "--format",
        help=(
            "Output format: markdown (default) or text (plain ASCII, no Markdown syntax "
            "or special characters -- for evidence/GRC systems that reject formatting)."
        ),
    ),
    output: Path = typer.Option(
        None, "-o", "--output", help="Also write the response to this file or directory."
    ),
    review: bool = typer.Option(
        False,
        "--review",
        help="Run two local reviewer critiques and generator revisions before output.",
    ),
) -> None:
    """Generate a security control response grounded in the local knowledge base."""
    engagement = _active_engagement_or_exit()
    # A single status spans collection load, retrieval, and generation so the
    # spinner appears immediately rather than after several silent seconds of
    # Chroma/embedding work; the label is updated at each real phase transition.
    with console.status("Loading engagement data...") as status:
        set_status = _throttled_status_setter(status)
        collections = _build_collections(engagement)
        set_status("Retrieving context...")

        result = _generate_control_response(
            control_id,
            context,
            collections,
            engagement,
            output_format,
            max_followups=None,
            review=review,
            set_status=set_status,
        )
    if not result.has_baseline_match:
        typer.echo(
            f"No matching NIST baseline content found for control ID '{control_id}' — "
            "check the ID or run `srg ingest`.",
            err=True,
        )
        raise typer.Exit(code=1)

    response_text = result.response_text

    typer.echo()
    typer.echo(response_text)

    if output is not None:
        target = output
        if target.is_dir():
            extension = "txt" if output_format == OutputFormat.text else "md"
            target = target / f"{engagement.slug}_{control_id}_{date.today():%Y%m%d}.{extension}"
        write_encoding = "ascii" if output_format == OutputFormat.text else "utf-8"
        target.write_text(response_text, encoding=write_encoding)
        typer.echo(f"Written to {target}", err=True)
    _show_demo_reminder(engagement)


@app.command()
def chat(
    question: str = typer.Argument(
        ...,
        help="A question about the active engagement's standards, NIST baseline, or context.",
    ),
) -> None:
    """Answer a freeform question grounded in the active engagement's indexed material."""
    engagement = _active_engagement_or_exit()
    # A single status spans collection load, retrieval, and generation so the
    # spinner appears immediately rather than after several silent seconds of
    # Chroma/embedding work.
    with console.status("Loading engagement data...") as status:
        set_status = _throttled_status_setter(status)
        collections = _build_collections(engagement)
        set_status("Retrieving context...")

        result = retrieve_for_chat(question, collections)

        instructions = config.CHAT_INSTRUCTIONS_PATH.read_text(encoding="utf-8")
        prompt = assemble_chat_prompt(
            instructions=instructions,
            question=question,
            customer_chunks=result.customer_chunks,
            baseline_chunks=result.baseline_chunks,
            private_chunks=result.private_chunks,
        )
        messages = [
            {"role": "system", "content": prompt.system},
            {"role": "user", "content": prompt.user},
        ]
        set_status("Thinking...")
        response_text = chat_messages(messages)

    typer.echo()
    typer.echo(
        f"Customer: {engagement.response_customer_name}\n\n{response_text}\n\n"
        "(Draft answer -- verify against source material before relying on it.)"
    )
    _show_demo_reminder(engagement)


@app.command("bulk-generate")
def bulk_generate(
    csv_path: Path = typer.Argument(
        ...,
        exists=True,
        dir_okay=False,
        readable=True,
        help="CSV file with 'Control ID' and 'User added context' columns.",
    ),
    output_dir: Path = typer.Option(
        ...,
        "-o",
        "--output-dir",
        file_okay=False,
        help="Directory to write one response file per control (created if missing).",
    ),
    output_format: OutputFormat = typer.Option(
        OutputFormat.markdown,
        "--format",
        help=(
            "Output format: markdown (default) or text (plain ASCII, no Markdown syntax "
            "or special characters -- for evidence/GRC systems that reject formatting)."
        ),
    ),
) -> None:
    """Generate responses for every row of a CSV file, fully noninteractively.

    Up to config.MAX_BULK_CONTROLS rows per file. No follow-up questions are ever
    asked; a control that would have needed one gets a best-effort response with
    an inline note instead. Every completed draft receives two local review and
    revision passes. A problem specific to one control (unknown control ID, a
    suppressed question) is written into that control's own file; a problem
    affecting every remaining row (Ollama unreachable, knowledge base not
    ingested) aborts the whole run immediately, leaving already-written files in
    place.
    """
    try:
        rows = bulk_csv.parse_bulk_csv(csv_path)
    except bulk_csv.CsvValidationError as exc:
        typer.echo("Invalid bulk CSV:", err=True)
        for error in exc.errors:
            typer.echo(f"  {error}", err=True)
        raise typer.Exit(code=1) from exc

    engagement = _active_engagement_or_exit()
    with console.status("Loading engagement data..."):
        collections = _build_collections(engagement)

    if collections[config.COLLECTION_KNOWLEDGE_BASE].count() == 0:
        typer.echo(
            "The NIST knowledge base has no ingested content — run `srg ingest` first.",
            err=True,
        )
        raise typer.Exit(code=1)

    output_dir.mkdir(parents=True, exist_ok=True)

    total = len(rows)
    extension = "txt" if output_format == OutputFormat.text else "md"
    write_encoding = "ascii" if output_format == OutputFormat.text else "utf-8"
    console.print(f"Bulk generating {total} control response(s) into {output_dir}...")

    completed: list[str] = []
    noted: list[str] = []

    for index, row in enumerate(rows, start=1):
        try:
            result = _generate_control_response(
                row.control_id,
                row.context,
                collections,
                engagement,
                output_format,
                max_followups=0,
                review=True,
            )
            target = (
                output_dir / f"{engagement.slug}_{row.control_id}_{date.today():%Y%m%d}.{extension}"
            )
            target.write_text(result.response_text, encoding=write_encoding)
        except _SYSTEMIC_ERRORS as exc:
            not_attempted = [r.control_id for r in rows[index - 1 :]]
            console.print(f"[red]Aborted after {len(completed)}/{total} control(s): {exc}[/red]")
            console.print(f"Completed: {', '.join(completed) or '(none)'}")
            console.print(f"Not attempted: {', '.join(not_attempted)}")
            raise typer.Exit(code=1) from exc

        has_note = not result.has_baseline_match or result.forced_completion
        completed.append(row.control_id)
        if has_note:
            noted.append(row.control_id)
            console.print(
                f"[yellow][{index}/{total}][/yellow] {row.control_id} -> {target} (note: see file)"
            )
        else:
            console.print(f"[green][{index}/{total}][/green] {row.control_id} -> {target}")

    clean_count = total - len(noted)
    console.print(
        f"\nDone: {clean_count} clean, {len(noted)} with notes, {total} total -> {output_dir}"
    )
    _show_demo_reminder(engagement)


@app.command("evaluate-model")
def evaluate_model_command(
    candidate_model: str = typer.Argument(
        ..., help="Installed local generation model to compare with the SRG default."
    ),
    compare_to: str = typer.Option(
        config.DEFAULT_GENERATION_MODEL,
        "--compare-to",
        help="Comparison generation model (default: SRG's shipped generation model).",
    ),
    profile: str = typer.Option(
        "standard",
        "--profile",
        help=(
            "Evaluation profile: 'standard' (task-balanced advisory qualification "
            "evidence) or 'smoke' (fast development feedback)."
        ),
    ),
    output_dir: Path = typer.Option(
        config.MODEL_EVALUATION_DIR,
        "--output-dir",
        help="Parent directory for the timestamped evaluation artifact folder.",
    ),
    yes: bool = typer.Option(
        False,
        "--yes",
        "-y",
        help="Accept the displayed evaluation plan without an interactive confirmation.",
    ),
) -> None:
    """Compare a candidate generation model with SRG's shipped default.

    The default standard profile scales a shared harness to task-balanced
    advisory qualification evidence. The smoke profile (`--profile smoke`)
    uses the same committed fictional inputs at a much smaller scale, for
    rapid development feedback rather than qualification evidence. Neither
    profile invokes SRG's review/revision pipeline or reads an active
    customer engagement.
    """
    if profile not in model_evaluation.PROFILES:
        typer.echo(
            "Only '--profile smoke' or '--profile standard' are currently available.",
            err=True,
        )
        raise typer.Exit(code=2)

    # Ollama's installed-model query can occasionally take several seconds.
    # Print the heading first so command startup never appears unresponsive.
    typer.echo("\nSRG generation-model evaluation\n")
    try:
        with console.status("Checking evaluation prerequisites..."):
            model_evaluation.validate_preflight(
                candidate_model, compare_to, config.REVIEW_MODEL, output_dir, profile=profile
            )
    except _SYSTEMIC_ERRORS as exc:
        typer.echo(f"Model evaluation preflight failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    cases = model_evaluation.PROFILES[profile].load_cases()
    comparison_label = (
        "SRG shipped default"
        if model_evaluation.normalize_model_name(compare_to)
        == model_evaluation.normalize_model_name(config.DEFAULT_GENERATION_MODEL)
        else "explicit override"
    )
    if profile == "smoke":
        plan = (
            f"Candidate model:  {candidate_model}\n"
            f"Comparison model: {compare_to} ({comparison_label})\n"
            f"Grader model:     {config.REVIEW_MODEL}\n"
            "Scope:            Generation model only; review/revision is disabled\n"
            "Profile:          SMOKE - development feedback, not qualification\n"
            f"Cases:            {len(cases)} fictional control-response tasks\n"
            f"Response trials:  {model_evaluation.SMOKE_RESPONSE_TRIALS} total\n"
            f"Grader calls:     {model_evaluation.SMOKE_GRADER_CALLS} total "
            "(analyst check + assessment per response)\n"
            "Measurements:     Generation time, memory/GPU residency, requirement coverage\n"
            "Coverage:         Missing analyst context or no customer coverage = not viable\n"
            "                  Partial customer or incomplete private coverage = edits\n"
            "Placeholders:     2+ = not viable; 1 = edits\n"
            "Quality limit:    Automated review does not score prose quality or writing style\n"
            f"Estimated time:   {model_evaluation.SMOKE_ESTIMATE}\n"
            "    Hardware note: Models larger than SRG's default—or mixture-of-experts "
            "(MoE) models—may take much longer on typical workstations\n"
            f"Artifacts:        timestamped folder under {output_dir}\n"
            "Customer data:    No active engagement data will be used\n\n"
            "Ollama generation models will be loaded and unloaded. After confirmation,\n"
            "the run is fully noninteractive. If a model asks for missing information,\n"
            "SRG automatically makes one final call requiring a placeholder-annotated\n"
            "response based only on the supplied fictional context.\n"
            "No source documents, indexes, or engagement data will be modified."
        )
    else:
        plan = (
            f"Candidate model:  {candidate_model}\n"
            f"Comparison model: {compare_to} ({comparison_label})\n"
            f"Grader model:     {config.REVIEW_MODEL}\n"
            "Scope:            Generation model only; review/revision is disabled\n"
            "Profile:          STANDARD - advisory qualification evidence, not an "
            "automated decision\n"
            f"Cases:            {len(cases)} fictional control-response tasks\n"
            f"Trials per task/model: {len(model_evaluation.STANDARD_SEEDS)} (seeds "
            f"{', '.join(str(seed) for seed in model_evaluation.STANDARD_SEEDS)})\n"
            f"Response trials:  {model_evaluation.STANDARD_RESPONSE_TRIALS} total\n"
            f"Grader calls:     {model_evaluation.STANDARD_GRADER_CALLS} total "
            "(analyst check + assessment per response)\n"
            "Measurements:     Generation time, memory/GPU residency, requirement coverage, "
            "task-balanced statistics\n"
            "Coverage:         Missing analyst context or no customer coverage = not viable\n"
            "                  Partial customer or incomplete private coverage = edits\n"
            "Placeholders:     2+ = not viable; 1 = edits\n"
            "Quality limit:    Automated review does not score prose quality or writing style\n"
            "Qualification:    Advisory evidence only; qualification gates are not yet "
            "calibrated and this command never changes SRG's shipped default\n"
            f"Estimated time:   {model_evaluation.STANDARD_ESTIMATE}\n"
            f"Artifacts:        timestamped folder under {output_dir}\n"
            "Customer data:    No active engagement data will be used\n\n"
            "Ollama generation models will be loaded and unloaded. After confirmation,\n"
            "the run is fully noninteractive. If a model asks for missing information,\n"
            "SRG automatically makes one final call requiring a placeholder-annotated\n"
            "response based only on the supplied fictional context.\n"
            "No source documents, indexes, or engagement data will be modified."
        )
    typer.echo(plan)
    if not yes and not typer.confirm("\nProceed?", default=False):
        typer.echo("Evaluation cancelled.")
        return

    # Print a persistent line before any setup work. Rich's animated status can
    # take a moment to become visible while Ollama starts an unloaded embedding
    # model, so this guarantees immediate feedback after confirmation.
    console.print(f"\nStarting {profile} evaluation...")
    instructions = config.INSTRUCTIONS_PATH.read_text(encoding="utf-8")

    def generate_trial(
        case: model_evaluation.EvaluationCase, model: str, seed: int, phase: str
    ) -> model_evaluation.GenerationOutput:
        # Exercise the same query-embedding model used by normal retrieval while
        # keeping the actual grounding chunks frozen across candidate models.
        embed_query(f"{case.control_id} {case.context}")
        expected_resident = [config.EMBEDDING_MODEL]
        if phase != "cold":
            expected_resident.insert(0, model)
        monitoring_start = time.perf_counter()
        model_evaluation.require_models_resident(
            expected_resident,
            checkpoint=f"while embedding before the {phase} generation trial",
        )
        monitoring_seconds = time.perf_counter() - monitoring_start

        def chunks(values: list[str], source: str) -> list:
            return [
                RetrievedChunk(text=value, source_path=source, chunk_id=f"{source}::{index}")
                for index, value in enumerate(values)
            ]

        prompt = assemble_prompt(
            instructions=instructions,
            control_id=case.control_id,
            context_notes=case.context,
            customer_chunks=chunks(case.customer_chunks, f"eval/{case.id}/customer.md"),
            baseline_chunks=chunks(case.baseline_chunks, f"eval/{case.id}/nist.md"),
            private_chunks=chunks(case.private_chunks, f"eval/{case.id}/private.md"),
            output_format=OutputFormat.markdown,
        )
        model_calls: list[dict] = []

        def record_response(response: Mapping) -> None:
            keys = (
                "model",
                "total_duration",
                "load_duration",
                "prompt_eval_duration",
                "eval_duration",
                "prompt_eval_count",
                "eval_count",
            )
            model_calls.append({key: response.get(key) for key in keys})

        outcome = _run_conversation_tracked(
            prompt,
            OutputFormat.markdown,
            max_followups=0,
            review=False,
            generation_model=model,
            generation_seed=seed,
            on_generation_response=record_response,
        )
        return model_evaluation.GenerationOutput(
            response_text=outcome.text,
            model_calls=model_calls,
            forced_completion=outcome.forced_completion,
            monitoring_seconds=monitoring_seconds,
        )

    try:
        with console.status("Preparing model evaluation...") as status:
            result = model_evaluation.run_evaluation(
                profile,
                candidate_model,
                compare_to,
                generate=generate_trial,
                output_root=output_dir,
                on_status=status.update,
            )
    except model_evaluation.EvaluationInterrupted as exc:
        typer.echo("Model evaluation interrupted; completed work was preserved.", err=True)
        typer.echo(f"Partial artifacts: {exc.output_dir.resolve()}", err=True)
        if exc.artifact_error:
            typer.echo(f"Artifact warning: {exc.artifact_error}", err=True)
        if exc.cleanup_error:
            typer.echo(f"Model cleanup warning: {exc.cleanup_error}", err=True)
        raise typer.Exit(code=130) from exc
    except _SYSTEMIC_ERRORS as exc:
        typer.echo(f"Model evaluation aborted: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    standard_stats = None
    standard_sampling_manifest = None
    if profile == "standard" and result.status == "completed":
        try:
            metadata = model_evaluation.load_standard_case_metadata()
            standard_stats = model_evaluation_stats.compute_standard_stats(result, metadata)
            standard_sampling_manifest = model_evaluation_sampling.build_human_review_sample(result)
            model_evaluation.write_artifacts(
                result,
                cases,
                stats=standard_stats,
                sampling_manifest=standard_sampling_manifest,
            )
        except Exception as exc:
            standard_stats = None
            standard_sampling_manifest = None
            typer.echo(
                f"Warning: standard-profile statistics could not be computed: {exc}", err=True
            )

    typer.echo()
    typer.echo(
        model_evaluation.render_summary(
            result,
            color=console.color_system is not None,
            stats=standard_stats,
            sampling_manifest=standard_sampling_manifest,
        )
    )


@app.command("benchmark")
def benchmark_command(
    control_ids: list[str] = typer.Argument(
        ..., help="Control ID(s) to benchmark, e.g. SI-5 AC-2."
    ),
    iterations: int = typer.Option(
        1,
        "--iterations",
        "-n",
        min=1,
        help=(
            "Repeat each control back-to-back, to see whether later calls reuse an "
            "already-loaded model."
        ),
    ),
    review: bool = typer.Option(
        False,
        "--review/--no-review",
        help="Include the two review/revision passes (off by default, matching `generate`).",
    ),
    context: str = typer.Option(
        "", "--context", help="Freeform analyst context applied to every benchmarked control."
    ),
) -> None:
    """Time every phase of control-response generation against the active
    engagement's already-ingested data, to find which phase(s) are worth
    optimizing.

    Diagnostic/dev tool: prints Rich tables to stdout and writes nothing to
    disk. Requires a running local Ollama daemon and an ingested active
    engagement (`srg ingest`). Never asks follow-up questions (always runs
    non-interactively, like `bulk-generate`).
    """
    engagement = _active_engagement_or_exit()

    load_start = time.perf_counter()
    with console.status("Loading engagement data..."):
        collections = _build_collections(engagement)
    collection_load_seconds = time.perf_counter() - load_start

    if collections[config.COLLECTION_KNOWLEDGE_BASE].count() == 0:
        typer.echo(
            "The NIST knowledge base has no ingested content — run `srg ingest` first.",
            err=True,
        )
        raise typer.Exit(code=1)

    report = benchmark.BenchmarkReport(collection_load_seconds=collection_load_seconds)
    # The report table has many columns (label, model, four durations, two token
    # counts, cold-load flag) -- render it wide rather than truncating cell text to
    # fit an assumed terminal width, since a diagnostic report is only useful if its
    # numbers are intact (redirect to a file or scroll horizontally if needed).
    stdout_console = Console(width=200)

    for control_id in control_ids:
        for iteration in range(1, iterations + 1):
            recorder = benchmark.Recorder()
            run_start = time.perf_counter()
            try:
                with console.status(
                    f"Benchmarking {control_id} ({iteration}/{iterations})..."
                ) as status:
                    # Deliberately not throttled: wall_seconds below spans this whole
                    # block, and this command's job is to measure that time accurately,
                    # not make the spinner readable.
                    result = _generate_control_response(
                        control_id,
                        context,
                        collections,
                        engagement,
                        OutputFormat.markdown,
                        max_followups=0,
                        review=review,
                        on_retrieval_timing=recorder.on_retrieval_timing,
                        on_embed_response=recorder.on_embed_response,
                        on_generation_response=recorder.on_generation_response,
                        on_review_response=recorder.on_review_response,
                        set_status=status.update,
                    )
            except _SYSTEMIC_ERRORS as exc:
                console.print(
                    f"[red]Aborted during {control_id} (iteration {iteration}): {exc}[/red]"
                )
                if report.runs:
                    benchmark.render_report(report, stdout_console)
                raise typer.Exit(code=1) from exc

            report.runs.append(
                benchmark.build_run_timing(
                    control_id,
                    iteration,
                    time.perf_counter() - run_start,
                    recorder,
                    forced_completion=result.forced_completion,
                    has_baseline_match=result.has_baseline_match,
                )
            )

    benchmark.render_report(report, stdout_console)


@app.command("create-engagement")
def create_engagement_command(
    name: str = typer.Argument(..., help="Engagement slug, e.g. virginia or acme-health."),
    customer_name: str = typer.Option(
        None,
        "--customer-name",
        help="Customer name shown on responses (default: title-cased engagement slug).",
    ),
) -> None:
    """Create and activate an isolated customer engagement."""
    try:
        engagement = engagements.create_engagement(name, customer_name)
    except (ValueError, FileExistsError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(f"Created and activated engagement: {engagement.customer_name}")
    typer.echo(f"\nAdd customer standards files in:\n  {engagement.customer_standards_dir}")
    typer.echo(f"\nAdd private system context details in:\n  {engagement.private_context_dir}")
    typer.echo("\nNext:\n  srg ingest")


@app.command("use-engagement")
def use_engagement_command(
    name: str = typer.Argument(..., help="Existing engagement slug."),
) -> None:
    """Select the engagement used by ingest and generate."""
    try:
        engagement = engagements.set_active_engagement(name)
    except (ValueError, FileNotFoundError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"Active engagement: {engagement.response_customer_name}")


@app.command("list-engagements")
def list_engagements_command() -> None:
    """List available engagements and identify the active one."""
    active = _active_engagement_or_exit()
    for engagement in engagements.list_engagements():
        marker = "*" if engagement.slug == active.slug else " "
        typer.echo(f"{marker} {engagement.slug}: {engagement.response_customer_name}")


@app.command("show-engagement")
def show_engagement_command() -> None:
    """Show the active engagement and its document locations."""
    engagement = _active_engagement_or_exit()
    typer.echo(f"Active engagement: {engagement.response_customer_name} ({engagement.slug})")
    typer.echo(f"Customer standards: {engagement.customer_standards_dir}")
    typer.echo(f"Private context: {engagement.private_context_dir}")


def _active_engagement_or_exit() -> engagements.Engagement:
    try:
        return engagements.active_engagement()
    except (ValueError, FileNotFoundError, KeyError) as exc:
        typer.echo(f"Cannot load the active engagement: {exc}", err=True)
        typer.echo("Run `srg use-engagement demo` or create a new engagement.", err=True)
        raise typer.Exit(code=1) from exc


def _show_demo_reminder(engagement: engagements.Engagement) -> None:
    if engagement.is_demo:
        typer.echo(
            "\nUsing DEMO engagement. To create your first engagement, see README.md "
            "#create-a-customer-engagement.",
            err=True,
        )


MIN_STATUS_DISPLAY_SECONDS = 0.8


def _throttled_status_setter(status) -> Callable[[str], None]:
    """Wrap a Status's update() so each label stays on screen for at least
    MIN_STATUS_DISPLAY_SECONDS before the next one replaces it. Without this,
    phases that finish in well under a second (collection load, retrieval)
    flash by unreadably fast. Slower phases (the actual model calls) already
    exceed the threshold, so this never adds a perceptible delay to them.
    """
    last_switch = time.perf_counter()

    def set_status(label: str) -> None:
        nonlocal last_switch
        elapsed = time.perf_counter() - last_switch
        if elapsed < MIN_STATUS_DISPLAY_SECONDS:
            time.sleep(MIN_STATUS_DISPLAY_SECONDS - elapsed)
        status.update(label)
        last_switch = time.perf_counter()

    return set_status


@contextmanager
def _spinner(label: str, set_status: Callable[[str], None] | None):
    """Show `label` on an already-active spinner via set_status, or open a new
    one if none is active. Opening a second Rich Live while one is already
    active renders as two overlapping spinners in a real terminal, so callers
    with an active outer status must pass its update method through here
    instead of letting this open its own.
    """
    if set_status is not None:
        set_status(label)
        yield
    else:
        with console.status(label):
            yield


def _wait_for_model(
    messages: list[dict],
    label: str = "Thinking...",
    *,
    generation_model: str | None = None,
    generation_seed: int | None = None,
    on_response: Callable[[Mapping], None] | None = None,
    set_status: Callable[[str], None] | None = None,
) -> str:
    """Call chat_messages with a spinner so a multi-minute wait doesn't look hung."""
    with _spinner(label, set_status):
        if generation_model is None:
            return chat_messages(messages, response_format=RESPONSE_SCHEMA, on_response=on_response)
        return generate_messages(
            messages,
            model=generation_model,
            seed=config.GENERATION_SEED if generation_seed is None else generation_seed,
            response_format=RESPONSE_SCHEMA,
            on_response=on_response,
        )


def _review_and_revise(
    prompt: AssembledPrompt,
    messages: list[dict],
    draft: str,
    *,
    generation_model: str | None = None,
    generation_seed: int | None = None,
    on_generation_response: Callable[[Mapping], None] | None = None,
    on_review_response: Callable[[Mapping], None] | None = None,
    set_status: Callable[[str], None] | None = None,
) -> str:
    """Run two reviewer critiques, each followed by a generator revision."""
    candidate = draft
    model_kwargs = (
        {"generation_model": generation_model, "generation_seed": generation_seed}
        if generation_model is not None
        else {}
    )
    for pass_number in (1, 2):
        with _spinner(f"Reviewing draft ({pass_number}/2)...", set_status):
            # messages[0:2] are the original system/user prompt, already sent to
            # assemble_review_messages explicitly as prompt.system/prompt.user --
            # passing the full list here would duplicate the entire grounding
            # material a second time (re-serialized as JSON), roughly doubling
            # every review call's token cost for zero added information.
            raw_critique = review_messages(
                assemble_review_messages(prompt, candidate, messages[2:]),
                response_format=REVIEW_SCHEMA,
                on_response=on_review_response,
            )
        critique = parse_critique(raw_critique)
        messages.append({"role": "assistant", "content": candidate})
        messages.append(
            {
                "role": "user",
                "content": revision_instruction(critique, final=pass_number == 2),
            }
        )
        candidate = _wait_for_model(
            messages,
            "Finalizing..." if pass_number == 2 else "Revising draft...",
            on_response=on_generation_response,
            set_status=set_status,
            **model_kwargs,
        )
    return candidate


BLANK_RESPONSE_PLACEHOLDER = (
    "[The model returned an empty response. Try again, or set SRG_GEN_MODEL "
    "to a different generation model.]"
)


def _is_blank_response(response: str | None) -> bool:
    return response is None or not response.strip()


def _render_final_reply(raw_reply: str, output_format: OutputFormat) -> str:
    reply = parse_model_reply(raw_reply)
    response = BLANK_RESPONSE_PLACEHOLDER if _is_blank_response(reply.response) else reply.response
    response = _INLINE_VALIDATIONS_RE.sub("", response).rstrip()
    validations = reply.validations or ["[PLACEHOLDER: no screenshot validations were generated.]"]
    if output_format == OutputFormat.markdown:
        validation_text = "\n".join(f"* {validation}" for validation in validations)
    else:
        validation_text = "\n".join(validations)
    return f"{response.rstrip()}\n\n[Validations]\n\n{validation_text}"


@dataclass
class ConversationOutcome:
    text: str
    forced_completion: bool


def _run_conversation_tracked(
    prompt: AssembledPrompt,
    output_format: OutputFormat = OutputFormat.markdown,
    max_followups: int | None = None,
    *,
    review: bool = False,
    generation_model: str | None = None,
    generation_seed: int | None = None,
    on_generation_response: Callable[[Mapping], None] | None = None,
    on_review_response: Callable[[Mapping], None] | None = None,
    set_status: Callable[[str], None] | None = None,
) -> ConversationOutcome:
    """Run the generation call, handling up to max_followups (or
    config.MAX_FOLLOWUP_TURNS if not given) interactive clarifying questions
    before returning the final response.

    If the model still hasn't produced a final answer once the follow-up
    budget is exhausted, one last forced-completion call is made and its
    result is returned unconditionally (best-effort response with
    placeholders for whatever couldn't be addressed). Passing
    max_followups=0 makes this fully noninteractive: the first needs_info
    reply immediately triggers forced completion without ever prompting.
    When review is true, the completed draft receives two review/revision
    passes after all question handling is finished.
    """
    messages = [
        {"role": "system", "content": prompt.system},
        {"role": "user", "content": prompt.user},
    ]
    followups_remaining = config.MAX_FOLLOWUP_TURNS if max_followups is None else max_followups
    blank_retry_available = True
    model_kwargs = (
        {"generation_model": generation_model, "generation_seed": generation_seed}
        if generation_model is not None
        else {}
    )

    while True:
        raw_reply = _wait_for_model(
            messages,
            on_response=on_generation_response,
            set_status=set_status,
            **model_kwargs,
        )
        reply = parse_model_reply(raw_reply)
        if not reply.needs_info:
            if _is_blank_response(reply.response) and blank_retry_available:
                blank_retry_available = False
                messages.append({"role": "assistant", "content": raw_reply})
                messages.append({"role": "user", "content": BLANK_RESPONSE_RETRY_INSTRUCTION})
                continue
            final_reply = (
                _review_and_revise(
                    prompt,
                    messages,
                    raw_reply,
                    on_generation_response=on_generation_response,
                    on_review_response=on_review_response,
                    set_status=set_status,
                    **model_kwargs,
                )
                if review
                else raw_reply
            )
            return ConversationOutcome(_render_final_reply(final_reply, output_format), False)

        messages.append({"role": "assistant", "content": raw_reply})

        if followups_remaining <= 0:
            messages.append({"role": "user", "content": FORCED_COMPLETION_INSTRUCTION})
            raw_reply = _wait_for_model(
                messages,
                "Wrapping up...",
                on_response=on_generation_response,
                set_status=set_status,
                **model_kwargs,
            )
            final_reply = (
                _review_and_revise(
                    prompt,
                    messages,
                    raw_reply,
                    on_generation_response=on_generation_response,
                    on_review_response=on_review_response,
                    set_status=set_status,
                    **model_kwargs,
                )
                if review
                else raw_reply
            )
            return ConversationOutcome(_render_final_reply(final_reply, output_format), True)

        typer.echo(f"\n{reply.question}\n")
        answer = typer.prompt("Your answer")
        messages.append({"role": "user", "content": answer})
        followups_remaining -= 1


def _run_conversation(
    prompt: AssembledPrompt,
    output_format: OutputFormat = OutputFormat.markdown,
) -> str:
    """Back-compat wrapper around _run_conversation_tracked for existing callers."""
    return _run_conversation_tracked(prompt, output_format).text


if __name__ == "__main__":
    app()
