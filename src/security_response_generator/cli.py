"""Command-line interface for security-response-generator."""

import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import ollama
import typer
from rich.console import Console
from rich.progress import BarColumn, MofNCompleteColumn, Progress, TextColumn, TimeElapsedColumn

from security_response_generator import config, engagements
from security_response_generator.generation import bulk_csv
from security_response_generator.generation.formatting import normalize_to_ascii
from security_response_generator.generation.prompt import (
    FORCED_COMPLETION_INSTRUCTION,
    RESPONSE_SCHEMA,
    AssembledPrompt,
    OutputFormat,
    assemble_chat_prompt,
    assemble_prompt,
    parse_model_reply,
)
from security_response_generator.generation.retrieval import retrieve_for_chat, retrieve_for_control
from security_response_generator.ingest import loaders, nist_oscal
from security_response_generator.ingest import manifest as manifest_module
from security_response_generator.ingest.chunking import chunk_text
from security_response_generator.ingest.store import (
    delete_source,
    get_client,
    get_collection,
    upsert_chunks,
)
from security_response_generator.llm.ollama_client import chat_messages

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
) -> ControlGenerationResult:
    """Run retrieval + generation for one control. Reused by `generate` (which
    treats a missing baseline match as fatal) and `bulk-generate` (which
    instead writes a note into that control's own output file).
    """
    result = retrieve_for_control(control_id, context, collections)

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
        customer_chunks=result.customer_chunks,
        baseline_chunks=result.baseline_chunks,
        private_chunks=result.private_chunks,
        output_format=output_format,
    )

    outcome = _run_conversation_tracked(prompt, output_format, max_followups)
    labeled = _label_response(outcome.text, engagement, output_format)
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
) -> None:
    """Generate a security control response grounded in the local knowledge base."""
    engagement = _active_engagement_or_exit()
    collections = _build_collections(engagement)

    result = _generate_control_response(
        control_id, context, collections, engagement, output_format, max_followups=None
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
    collections = _build_collections(engagement)

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
    with console.status("Thinking..."):
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
    an inline note instead. A problem specific to one control (unknown control ID,
    a suppressed question) is written into that control's own file; a problem
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


def _wait_for_model(messages: list[dict], label: str = "Thinking...") -> str:
    """Call chat_messages with a spinner so a multi-minute wait doesn't look hung."""
    with console.status(label):
        return chat_messages(messages, response_format=RESPONSE_SCHEMA)


def _render_final_reply(raw_reply: str, output_format: OutputFormat) -> str:
    reply = parse_model_reply(raw_reply)
    response = reply.response or raw_reply
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
    """
    messages = [
        {"role": "system", "content": prompt.system},
        {"role": "user", "content": prompt.user},
    ]
    followups_remaining = config.MAX_FOLLOWUP_TURNS if max_followups is None else max_followups

    while True:
        raw_reply = _wait_for_model(messages)
        reply = parse_model_reply(raw_reply)
        if not reply.needs_info:
            return ConversationOutcome(_render_final_reply(raw_reply, output_format), False)

        messages.append({"role": "assistant", "content": raw_reply})

        if followups_remaining <= 0:
            messages.append({"role": "user", "content": FORCED_COMPLETION_INSTRUCTION})
            raw_reply = _wait_for_model(messages, "Wrapping up...")
            return ConversationOutcome(_render_final_reply(raw_reply, output_format), True)

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
