"""Command-line interface for security-response-generator."""

from datetime import date
from pathlib import Path

import typer
from rich.console import Console
from rich.progress import BarColumn, MofNCompleteColumn, Progress, TextColumn, TimeElapsedColumn

from security_response_generator import config, engagements
from security_response_generator.generation.formatting import normalize_to_ascii
from security_response_generator.generation.prompt import (
    FORCED_COMPLETION_INSTRUCTION,
    RESPONSE_SCHEMA,
    AssembledPrompt,
    OutputFormat,
    assemble_prompt,
    parse_model_reply,
)
from security_response_generator.generation.retrieval import retrieve_for_control
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

app = typer.Typer(help="Local RAG CLI for drafting security control responses.")
console = Console(stderr=True)


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
    baseline_client = get_client(config.CHROMA_DIR)
    engagement_client = get_client(engagement.chroma_dir)
    collections = {
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

    result = retrieve_for_control(control_id, context, collections)

    if not result.has_baseline_match:
        typer.echo(
            f"No matching NIST baseline content found for control ID '{control_id}' — "
            "check the ID or run `srg ingest`.",
            err=True,
        )
        raise typer.Exit(code=1)

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

    response_text = _run_conversation(prompt)

    if output_format == OutputFormat.text:
        response_text = normalize_to_ascii(response_text)
        customer_label = normalize_to_ascii(engagement.response_customer_name)
        response_text = f"Customer: {customer_label}\n\n{response_text}"
    else:
        response_text = f"# Customer: {engagement.response_customer_name}\n\n{response_text}"

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
            "\nUsing DEMO engagement.\n\n"
            "To create your first engagement:\n"
            "  srg create-engagement <governing-state>-<system-name>\n\n"
            "For example:\n"
            "  srg create-engagement northbridge-SALI\n\n"
            "If you already created an engagement, list the available engagements:\n"
            "  srg list-engagements\n\n"
            "Then activate the one you want to use:\n"
            "  srg use-engagement <engagement-name>",
            err=True,
        )


def _wait_for_model(messages: list[dict], label: str = "Thinking...") -> str:
    """Call chat_messages with a spinner so a multi-minute wait doesn't look hung."""
    with console.status(label):
        return chat_messages(messages, response_format=RESPONSE_SCHEMA)


def _run_conversation(prompt: AssembledPrompt) -> str:
    """Run the generation call, handling up to config.MAX_FOLLOWUP_TURNS
    interactive clarifying questions before returning the final response.

    If the model still hasn't produced a final answer once the follow-up
    budget is exhausted, one last forced-completion call is made and its
    result is returned unconditionally (best-effort response with
    placeholders for whatever couldn't be addressed).
    """
    messages = [
        {"role": "system", "content": prompt.system},
        {"role": "user", "content": prompt.user},
    ]
    followups_remaining = config.MAX_FOLLOWUP_TURNS

    while True:
        raw_reply = _wait_for_model(messages)
        reply = parse_model_reply(raw_reply)
        if not reply.needs_info:
            return reply.response or raw_reply

        messages.append({"role": "assistant", "content": raw_reply})

        if followups_remaining <= 0:
            messages.append({"role": "user", "content": FORCED_COMPLETION_INSTRUCTION})
            raw_reply = _wait_for_model(messages, "Wrapping up...")
            return parse_model_reply(raw_reply).response or raw_reply

        typer.echo(f"\n{reply.question}\n")
        answer = typer.prompt("Your answer")
        messages.append({"role": "user", "content": answer})
        followups_remaining -= 1


if __name__ == "__main__":
    app()
