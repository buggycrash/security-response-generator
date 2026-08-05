# Security Response Generator

A local-first CLI that drafts security control responses (for example,
NIST SP 800-53 controls such as `SI-5`) for a compliance assessor to review.
It uses retrieval-augmented generation (RAG) grounded in three tiers of
source material. Output can be Markdown or plain ASCII text, depending on
what the target system of record accepts:

1. **NIST SP 800-53 Rev. 5 baseline** — the control catalog around which SRG's
   ingestion and retrieval logic is designed. Supporting a different catalog,
   such as PCI DSS, HIPAA, or ISO/IEC 27001, would require code changes.
2. **Customer/state-specific standards** — for example, a state's published
   per-control guidance with state-specific parameter values. When present
   for a control, this is treated as **authoritative** over generic NIST
   language. Its content must use matching control catalog IDs.
3. **Private system context** — non-public specifics about the system being
   assessed, supplied through the active engagement's `private_context/`
   folder plus freeform context notes per query.

Everything runs locally: embeddings and generation both go through
[Ollama](https://ollama.com), and retrieval uses an embedded
[ChromaDB](https://www.trychroma.com) instance (a folder on disk, no server
process). SRG pins Ollama connections to the local loopback interface,
rejects cloud-tagged Ollama models before sending content, and disables
Chroma product telemetry.

## Features

- Three-tier retrieval that respects customer/state standards as
  authoritative when they exist, and explicitly flags when they don't.
- Runs on U.S.-developed, open-weight models (Llama 3.1 8B by default; see
  [Choosing a generation model](#choosing-a-generation-model)) rather than a
  closed-source or overseas API.
- Refuses to answer (rather than hallucinate) if a control ID has no match
  in the NIST baseline. SRG is a dedicated control-response tool, not a
  general chatbot.
- Interactive follow-up questions when a material part of the control isn't
  covered by the supplied context, up to a configurable round limit, with a
  best-effort placeholder-annotated response if the model still isn't done.
- Incremental ingestion — only re-embeds files that changed.
- Reproducible download and conversion of official NIST SP 800-53 OSCAL
  catalogs into chunker-compatible Markdown.
- Isolated customer engagements with a shared NIST SP 800-53 baseline, so
  switching customers never requires deleting or replacing another
  customer's files.
- Every generated response is labeled with its customer engagement (or
  `DEMO`) in code rather than relying on the model to identify it.
- Markdown or plain ASCII text output (`--format`), printed to stdout and
  optionally written to a file — plain text is enforced in code, not just by
  prompt instruction, for evidence/GRC systems that reject any formatting or
  non-ASCII characters.
- A separate validation section with suggested screenshots that an assessor
  could request to substantiate material claims in the draft.

## Caveats

- The ingest and retrieval processors remain tailored to NIST SP 800-53
  control IDs and heading structure. Use `srg update-nist` for official OSCAL
  updates; dropping in an arbitrary catalog or generic JSON-to-Markdown
  conversion may not preserve reliable control boundaries.

## Technology Stack

- **Language**: Python
- **Generation model**: [Llama 3.1 8B](https://ollama.com/library/llama3.1)
  via [Ollama](https://ollama.com) by default — a practical, dense,
  text-only model that fits comfortably in 12 GB of VRAM alongside the
  embedding model. It is swappable through `SRG_GEN_MODEL`; see
  [Choosing a generation model](#choosing-a-generation-model) for tested
  models and results.
- **Embedding model**: EmbeddingGemma via Ollama
- **Vector store**: ChromaDB (embedded/local, no server)
- **CLI**: [Typer](https://typer.tiangolo.com)

## Prerequisites

- Permission from your customer to use this tool. Different customers have
  very different AI usage policies.
- Python 3.11+
- [Ollama](https://ollama.com/download) installed, with the daemon running
- Ubuntu 22.04 is the only tested operating system. Native Windows is not
  supported; WSL2 is supported. macOS may be compatible but has not yet been
  tested.
- A modest amount of VRAM or unified memory for `llama3.1:8b` (approximately
  4.9 GB to download and 7 GB of VRAM once loaded alongside
  `embeddinggemma`) — it fits comfortably on a 12 GB card. See
  [Choosing a generation model](#choosing-a-generation-model) for a more
  capable option if you have additional VRAM. SRG does not currently have a
  recommended generation model smaller than `llama3.1:8b`.

## Installation

Clone the repository, then run the recommended setup script:

```bash
cd security-response-generator
./setup.sh
```

This creates a `.venv`, installs the package, starts Ollama when needed,
pulls both models (`llama3.1:8b` and `embeddinggemma`), and installs a small
launcher at `~/.local/bin/srg`. The launcher invokes the project virtual
environment directly, so it does not need to be activated.

A built-in `demo` engagement is active initially. It uses the included
NIST SP 800-53 Release 5.2.0 catalog, fictional private system context, and
no customer-specific standards.

If `~/.local/bin` is not already on your `PATH`, setup prints the exact
one-time shell-profile change to add it. Check or troubleshoot the
installation at any time without changing anything:

```bash
./setup.sh --check
```

Other setup options include `--dev`, `--dev-only`, `--skip-models`,
`--model MODEL`, and `--install-dir DIR`; run `./setup.sh --help` for
details. `--dev-only` prepares the test environment and Git hook without
touching the command launcher, Ollama, or models.

## Cleanup

Run `./cleanup.sh` to remove the launcher owned by the current checkout and
the configured generation and embedding models. Because Ollama models are
shared and setup does not record whether a model predated SRG, review the
preview carefully or pass `--keep-models`. Cleanup does not uninstall Ollama,
stop its daemon, remove the project virtual environment, or delete the shared
NIST index.

Setup never edits shell profiles. Cleanup therefore prints the exact `PATH`
line to remove manually rather than modifying a profile it does not own.

Engagement data is retained unless `--wipe-engagements` is supplied. That
option requires both the explicit flag and a second exact typed confirmation;
it deletes customer engagements, local active-engagement state, and generated
demo data while preserving the committed fictional demo seed files.

## Choosing a generation model

> [!WARNING]
> Model weights are not included in this source repository and are not
> covered by its MIT License. Running `./setup.sh` without `--skip-models`
> downloads them into Ollama's local model storage, where they become
> separately licensed runtime components of the installed project. The
> default generation model is governed by the
> [Llama 3.1 Community License](https://ollama.com/library/llama3.1%3A8b-text-q3_K_M/blobs/0ba8f0e314b4);
> the default embedding model is governed by the
> [Gemma Terms of Use](https://ai.google.dev/gemma/terms).

The default, `llama3.1:8b`, was selected after direct comparisons on SRG's
retrieval and generation workload. It provides a practical balance between
response quality, speed, and hardware requirements, but it remains an 8B
model: on complex controls it can omit or misunderstand relevant context
even when retrieval supplied the correct material. Every generated response
is therefore a draft requiring human review.

See [Examples of SRG model use](Examples-of-SRG-use.md) for side-by-side
outputs from the default model and Gemma 4 E4B using identical prompts. The
examples illustrate both outcomes: some prompts are handled similarly by
both models, while more capable models can follow nuanced analyst context
more reliably.

`llama3.1:8b` is a dense, text-only model with no unused vision or audio
encoders to load. Its approximately 7 GB resident footprint (Q4_K_M)
coexists comfortably with `embeddinggemma` on a 12 GB card without the
evict-and-reload cycling that tighter-fitting models can trigger on every
`srg generate` call. See "Responses are much slower than expected" in
[Troubleshooting](#troubleshooting) for symptoms and mitigations.

**[Phi-4-mini](https://ollama.com/library/phi4-mini)** (Microsoft) was tested
because its approximately 3.8B parameters and 2.5 GB download make it
attractive for constrained hardware. It is not sufficient for SRG's
generation workload and should not be used for control responses. Across
repeated identical prompts, it produced inconsistent output, omitted explicit
analyst context, drifted from the requested control, and generated validation
suggestions unrelated to its claims. Those are model-capability failures, not
retrieval failures. `llama3.1:8b` is the minimum recommended local generation
model; if your hardware cannot run it, SRG does not currently offer a suitable
smaller fallback. The linked
[model-output examples](Examples-of-SRG-use.md#phi4-mini-is-weak-and-inconsistent)
show the observed Phi-4-mini failures.

If you have significantly more VRAM available (roughly 16 GB or more),
**[Gemma 4 E4B](https://ollama.com/library/gemma4)** (Google) is also an
option. It is larger and can follow nuanced context more reliably, but it is
multimodal and bundles vision/audio encoders this tool never uses. That adds
load-time overhead and made it prone to VRAM-eviction cycling on a 12 GB card
in testing because its footprint sits at the edge of what's available
alongside `embeddinggemma`.

Switch models with the `SRG_GEN_MODEL` environment variable — no code
changes needed, since `srg` talks to Ollama's generic chat API regardless
of which model is behind it:

```bash
ollama pull gemma4:e4b
SRG_GEN_MODEL=gemma4:e4b srg generate SI-5 --context "..."
```

To make a switch permanent for your own sessions, export `SRG_GEN_MODEL` in
your shell profile.

After each generation request, SRG asks Ollama to keep the generation model
loaded for 20 minutes to make subsequent runs faster. Override that duration
with `SRG_GEN_KEEP_ALIVE` using an Ollama duration such as `30m` or `1h`:

```bash
SRG_GEN_KEEP_ALIVE=30m srg generate SI-5
```

The interactive follow-up-question feature (see
[Interactive follow-up questions](#interactive-follow-up-questions)) is
enforced via Ollama's structured-output/JSON-schema support rather than a
free-form response protocol. This keeps reply parsing consistent across
models, although the accuracy and completeness of the generated content
still depend on the selected model.

The embedding model (`embeddinggemma`) is a separate, much smaller model
used only for retrieval, and typically doesn't need to change when you swap
the generation model.

### Using a customer-approved cloud gateway (for example, AWS Bedrock)

Everything above assumes local models because most engagements haven't
pre-approved sending customer or system data to any external service (see
[Prerequisites](#prerequisites) and
[Security & Privacy](#security--privacy)). That default flips for a specific,
common situation: a customer that already provides AWS Bedrock as their own
sanctioned interface to a set of approved models. In that case, routing
generation through the customer's Bedrock endpoint isn't sending data to an
arbitrary third party — it stays inside a boundary the customer has already
vetted, under whatever data-handling terms they negotiated with AWS. If
that's your situation, it can be a reasonable choice, and often a better
one: Bedrock exposes larger, frontier-class models than what's practical to
run locally on constrained hardware, which can mean meaningfully more
nuanced, better-grounded responses than the supported local options above.

A few things worth confirming before doing this on any given engagement:

- Get it in writing the same way you would any other AI usage on the
  engagement — Bedrock accounts and configurations vary (region, logging/
  retention via CloudTrail, cross-region inference, per-model-provider data
  terms), and "the customer approved Bedrock" doesn't automatically cover
  every model or setting available through it.
- Retrieval and embedding (`embeddinggemma`) would stay local exactly as
  today — this only changes where the assembled prompt for the
  *generation* step gets sent, the same distinction as choosing between
  local models above.

This isn't implemented today — `llm/ollama_client.py`'s `chat_messages()`
is currently the only function that talks to a generation model, and it
assumes Ollama's chat API. Adding Bedrock support would mean a parallel
client (for example, via `boto3`'s `bedrock-runtime` Converse API) behind a
provider switch, plus reimplementing the JSON-schema structured-output
contract used for the
[interactive follow-up mechanism](#interactive-follow-up-questions) against
Bedrock's equivalent. It's noted here as a legitimate option worth knowing
about, not a decision this tool makes for you.

## Usage

The setup script installs an `srg` launcher in `~/.local/bin`; no virtual
environment activation or per-session startup command is required. If that
directory is not already on your `PATH`, setup shows the one-time change
needed to add it. The launcher starts the local Ollama daemon automatically
when necessary and then runs the command from this project's virtual
environment. This means `srg` works from any directory after setup.

1. **Add source material**:
   - The supported NIST SP 800-53 Release 5.2.0 catalog is already included in
     `knowledge_base/`. This project intentionally does not support switching
     to ISO, PCI DSS, or another control catalog.
   - On a fresh installation, the active `demo` engagement already contains
     fictional private context and no customer standards.
   - For real work, create an engagement first:
     ```bash
     srg create-engagement <governing-state>-<system-name>

     # For example:
     srg create-engagement northbridge-SALI
     ```
     The command prints the engagement-specific folders where customer
     standards and private system context belong.
     Use `--customer-name "State of Northbridge"` when the response label
     should be more formal than the title-cased folder name.

2. **Update the NIST catalog when needed**:
   ```bash
   srg update-nist
   ```
   By default, this downloads the official NIST OSCAL JSON catalog from the
   fixed `oscal-content` release tag `v1.4.0`, which contains SP 800-53
   Release 5.2.0. The source is intentionally pinned rather than resolved as
   "latest" so the same SRG version does not silently begin ingesting
   different regulatory content and so the generated baseline remains
   reproducible. The command validates the catalog metadata and atomically
   regenerates `knowledge_base/NIST.SP.800-53-oscal.md`. It records the source
   URL, catalog version, last-modified value, and source SHA-256 digest in
   the output. The converter emits headings compatible with exact control
   retrieval, resolves OSCAL organization-defined parameter references into
   readable placeholders, and includes SP 800-53 statements and guidance.
   SP 800-53A assessment objectives and methods bundled in the source OSCAL
   catalog are intentionally excluded.

   Updating to a future official release is therefore an explicit action:
   pass its HTTPS URL or a downloaded local copy with `--source`.
   `--output` selects a different Markdown destination. The command does not
   require Ollama and does not ingest or embed the result automatically.

3. **Ingest**:

   ```bash
   srg ingest
   ```

   The initial run may take several minutes. Re-run this command whenever
   files in the source folders change — unchanged files are
   skipped automatically. Use `--source knowledge_base|customer_standards|private_context`
   to ingest just one tier. `--rebuild` rebuilds only the active
   engagement's customer/private index. The shared NIST baseline requires
   the deliberately explicit `--rebuild-baseline` option.
   Large files show a progress bar on stderr as embedding batches complete,
   so a long ingest does not look stalled.

4. **Generate a response**:

   ```bash
   srg generate SI-5 --context "our environment uses a SaaS SIEM for continuous monitoring"
   ```

   Control enhancements work the same way. Quote an enhancement ID so the
   shell passes its parentheses to SRG instead of interpreting them as shell
   syntax:

   ```bash
   srg generate "SC-8(1)" --context "TLS 1.3 protects information in transit."
   ```

   Retrieval, analyst context, follow-up questions, validation suggestions,
   formatting, and file output behave the same way for controls and control
   enhancements.

   The first request may take longer while Ollama loads the model. SRG prints
   Markdown to stdout by default. Add `-o response.md` to also write
   it to a file (or to a directory, in which case a customer-labeled filename
   like `northbridge_SI-5_20260715.md` is generated). Every response begins
   with `Customer: Northbridge` (or `Customer: DEMO`) and ends with a
   `[Validations]` section containing suggested screenshot evidence.
   A spinner appears on stderr while waiting for the model, leaving stdout
   clean for piping and redirection.

   For evidence/GRC systems that only accept raw text with no formatting,
   such as some Archer or Xacta configurations, add `--format text`:

   ```bash
   srg generate SI-5 --format text --context "..." -o response.txt
   ```

   This produces plain ASCII output — no Markdown syntax, no smart quotes,
   em dashes, bullets, or other non-ASCII characters.
   A directory target with `--format text` gets a `.txt` filename instead
   of `.md`.

### Interactive follow-up questions

If the model determines that a distinct, material part of the control
isn't covered by the retrieved material, your `--context` notes, or anything
already discussed, it can ask you a clarifying question instead of guessing:

```
$ srg generate SI-5 --context "we use Acme Sentinel for monitoring"

What is the required review/dissemination timeframe for security alerts in
your environment?

Your answer: reviewed within 24 hours, disseminated within 48 hours
```

Answer at the prompt to continue the same conversation; there is no need to
rerun the command. This can happen up to `SRG_MAX_FOLLOWUP_TURNS` times
(default **2**). If it still isn't done after that, one final call produces
a best-effort response anyway: it opens with a brief note that some
information wasn't available, and inserts `[PLACEHOLDER: ...]` markers in
place of anything it couldn't address confidently, so you can fill those in
by hand before submitting to the assessor.

The tool is designed to produce a bounded, best-effort draft rather than
questioning indefinitely.

## Improving output quality

Output quality depends first on the facts available to the model and then on
the model's ability to interpret them. A larger model cannot reliably fill in
missing system information, so improve grounding before treating model size as
a substitute for context. In practical order:

1. **Add useful, reusable system information to `private_context/`.** Include
   stable details that may support many controls: system architecture,
   authentication and authorization mechanisms, administrative roles,
   logging and monitoring practices, account and change-management processes,
   review frequencies, and named tools. Keep the material specific enough to
   support concrete claims and free of unsupported assumptions. After adding
   or changing files, update the active engagement's index:

   ```bash
   srg ingest --source private_context
   ```

2. **Supply control-specific facts with `--context`.** Use this for details
   that are especially relevant to the response being drafted, including
   implementation choices, exact parameter values, applicability conditions,
   exceptions, and facts that may not belong in a reusable system document.
   Be explicit rather than relying on the model to infer the consequence. For
   example:

   ```bash
   srg generate "AC-2" --context "Shared and group accounts are prohibited and are not deployed."
   ```

   `--context` applies immediately to that generation request and does not
   require re-ingestion. If the same fact should inform many controls, put it
   in `private_context/` instead of repeating it on every command.

3. **Use a more capable generation model when hardware permits.** Better
   grounding still requires a model capable of following nuanced context and
   synthesizing a complex control. Gemma 4 E4B was more reliable than the
   default model on some identical prompts, while Phi-4-mini was insufficient
   for this workload. See
   [Choosing a generation model](#choosing-a-generation-model) and the
   [side-by-side model outputs](Examples-of-SRG-use.md) for the observed
   differences.

These measures are complementary. Start with accurate source material, add
the facts unique to the current control, and then use the strongest supported
model your hardware can run. More capable generation improves interpretation;
it does not remove the need for grounded inputs or human review of the draft.

## Customer engagements

Customer documents and indexes are isolated under `engagements/<state name>-<system name>/`.
The NIST baseline remains shared and is not duplicated for each customer.

```bash
srg create-engagement northbridge-SALI  # creates and activates it
srg show-engagement                     # shows active folders
srg list-engagements
srg use-engagement demo
srg use-engagement northbridge-SALI
```

Creating an engagement makes empty `customer_standards/`, `private_context/`,
`chroma_db/`, and `responses/` folders. Copy the applicable starter files
from `example_files/` into the paths printed by `create-engagement`, add
private context, and run `srg ingest`. Switching engagements changes which
customer/private index is queried; it does not delete or modify either
customer's files.

If DEMO is active after you have already created an engagement, run
`srg list-engagements` and then `srg use-engagement <engagement-name>` to
select it.

## Development

```bash
./setup.sh --dev-only            # install dev dependencies and enable Git hooks
.venv/bin/pytest               # run tests
.venv/bin/ruff check .          # lint
.venv/bin/ruff format --check . # verify formatting
```

See [CONTRIBUTING.md](../CONTRIBUTING.md) for the complete contribution
workflow. GitHub Actions runs the same lint, formatting, and test checks on
pull requests and pushes to `main`.

Most of the pipeline (chunking, manifest diffing, prompt assembly, retrieval
merge logic, CLI argument parsing) is unit-tested without needing a live
Ollama instance. Actual embedding/retrieval quality and generation output
require a live Ollama daemon with both models pulled and are verified
manually — see the "Manual verification" section below.

## How It Works

1. **Prepare the NIST baseline**: `srg update-nist` downloads or reads an
   OSCAL JSON catalog and converts only its SP 800-53 control material into
   deterministic, ingest-ready Markdown. This optional update operation is
   separate from ingest and is the only networked NIST step.
2. **Ingest**: documents are loaded (`.pdf` via `pypdf`, `.md`/`.txt`
   directly), split into ~800-token chunks with overlap (splitting on
   headers/paragraphs where possible), tagged with any control IDs found
   in the text, embedded via EmbeddingGemma, and stored in one of three
   Chroma collections. The `knowledge_base` collection is shared, while
   `customer_standards` and `private_context` live in the active
   engagement's isolated database. Separate manifests make re-ingestion
   incremental.
3. **Retrieve**: for a given control ID and freeform notes, each collection
   is queried twice — once filtered to chunks whose text contains the
   control ID, once by semantic similarity — and the results are merged,
   with `customer_standards` and `private_context` given protected
   top-k slots so they aren't drowned out by the much larger NIST corpus.
4. **Refuse or caveat**: if the NIST baseline has no match for the control
   ID, the tool refuses and exits non-zero rather than guessing. If the
   baseline matches but no customer/state standard does, generation
   proceeds but the model is instructed to say so explicitly.
5. **Generate**: retrieved chunks (labeled by tier), the control ID, and
   the analyst's notes are assembled into a prompt alongside
   `prompts/instructions.md` (editable — controls tone, implementation
   structure, validation guidance, and the authoritative-standards rule) and
   a format-specific rendering instruction (Markdown vs. plain ASCII text,
   chosen via `--format`).
   Analyst-provided `--context` facts are placed in the system message so
   they receive the same priority as the editable instructions. The assembled
   messages are then sent through Ollama to the generation model
   (`llama3.1:8b` by default; see
   [Choosing a generation model](#choosing-a-generation-model)).
   `instructions.md` is read fresh for every `srg generate` invocation, so
   edits apply immediately without re-ingesting documents or restarting SRG.
   The format-specific instruction only controls character-level rendering;
   it preserves sections and other response structure requested by the
   editable system instructions.
   The model returns a JSON-schema-constrained reply (`needs_info`,
   `question`, `response`, and `validations`) rather than free-form text, so
   the follow-up mechanism can reliably distinguish a question from a final
   response. SRG renders structured validation suggestions after the
   implementation prose and removes any duplicate validation section the
   model may have included in the prose. If the model returns no suggestions,
   SRG emits a visible placeholder instead of silently omitting the section.
6. **Follow up if needed**: if the reply has `needs_info: true`, its
   `question` is shown to you interactively and your typed answer is
   appended to the conversation before calling the model again — up to
   `SRG_MAX_FOLLOWUP_TURNS` times (default 2). If the budget runs out, one
   final call forces a best-effort response with `[PLACEHOLDER: ...]`
   markers for anything still unaddressed.
7. **Normalize (text format only)**: if `--format text` was requested, the
   raw model output is run through `generation/formatting.py`, which
   converts smart quotes/em-dashes/bullets to ASCII equivalents, strips any
   leftover Markdown syntax, and drops any remaining non-ASCII characters —
   a code-level guarantee independent of how well the model followed the
   prompt instruction.
8. **Output**: the response is printed to stdout and optionally written to
   a file.

## File Structure

```
security-response-generator/
├── pyproject.toml
├── setup.sh
├── srg                              # PATH-installed runtime launcher
├── scripts/common.sh                # shared setup/runtime health checks
├── prompts/instructions.md          # editable system prompt
├── knowledge_base/                  # committed: NIST SP 800-53 Rev. 5 catalog
├── chroma_db/                       # shared NIST embeddings
├── engagements/
│   ├── demo/                        # committed fictional demo context
│   └── <customer>/                  # gitignored customer data
│       ├── customer_standards/
│       ├── private_context/
│       ├── chroma_db/
│       └── responses/
├── example_files/                   # committed: per-jurisdiction starter material
│   └── Federal/ VA/ PA/ CA/ MD/ HI/ # copy into an engagement as applicable
├── docs/                            # technical guide, examples, and images
├── src/security_response_generator/
│   ├── cli.py                       # update, ingest, engagement, and generation commands
│   ├── config.py                    # models, paths, chunking, top-k (env-overridable)
│   ├── ingest/                      # loaders, chunking, manifest, Chroma store
│   ├── generation/                  # retrieval, prompt assembly, ASCII normalizer
│   └── llm/ollama_client.py         # Ollama embed/chat wrapper
└── tests/
```

## Troubleshooting

- **`ollama: command not found`**: install it from the
  [Ollama download page](https://ollama.com/download).
- **Ollama daemon not running**: the `srg` launcher normally starts it
  automatically. If startup fails, review `/tmp/srg-ollama-serve.log` (or
  `$TMPDIR/srg-ollama-serve.log` when `TMPDIR` is set).
- **Installation seems incomplete**: run `./setup.sh --check` for individual
  Python, launcher, Ollama, and model health checks.
- **`srg generate` refuses every control ID**: run `srg ingest` first — the
  NIST baseline collection is empty until `knowledge_base/` is ingested.
- **Model pull is slow/fails**: `llama3.1:8b` is an approximately 4.9 GB
  download (see
  [Choosing a generation model](#choosing-a-generation-model) for model
  requirements and the larger Gemma option); check disk space and network
  connectivity. Phi-4-mini is smaller but is not sufficiently reliable for
  this workload.
- **Responses are much slower than expected**: run `ollama ps` to check
  whether the model is fully on GPU or partially spilled to system RAM/CPU
  (Ollama does this automatically and silently if VRAM is tight, and it's a
  common source of unexplained slowness). Lowering context length or closing
  other GPU-heavy applications usually resolves it.
- **`srg ingest` fails with `ResponseError: ... EOF` on a large document**
  (for example, the full NIST SP 800-53 catalog): this is the
  embedding model's runner subprocess getting OOM-killed — check Ollama's
  own log (`journalctl -u ollama`, or the terminal running `ollama serve`
  if you started it manually) for a `signal: killed` line to confirm.
  `srg ingest` already batches embedding requests (`SRG_EMBED_BATCH_SIZE`,
  default 32) to avoid this; if it still happens on a memory-constrained
  machine (for example, WSL2 with a low `.wslconfig` memory cap), try lowering the
  batch size further:
  ```bash
  SRG_EMBED_BATCH_SIZE=8 srg ingest
  ```

## Security & Privacy

- Customer engagement folders are gitignored, including their standards,
  private context, indexes, and generated responses. Only the explicitly
  fictional `engagements/demo/` seed files are committed.
- The Python client is pinned to Ollama at `127.0.0.1:11434`; an
  `OLLAMA_HOST` environment override cannot redirect SRG to a remote server.
- SRG rejects Ollama model tags ending in `cloud` or `-cloud` before sending
  document or prompt content.
- Every Ollama CLI call made by the setup script or launcher is pinned to
  loopback. Any Ollama daemon started by SRG has cloud features disabled
  with `OLLAMA_NO_CLOUD=1`.
- Chroma is created with `anonymized_telemetry=False`, disabling its product
  telemetry.
- `srg update-nist` is an explicit exception to otherwise local document
  processing: it connects to the configured HTTPS OSCAL source and writes
  only the converted catalog to the selected output path. It does not send
  customer standards, private context, prompts, or generated responses.
- The cloud-gateway discussion above describes a possible future feature.
  The current implementation has no cloud generation provider.

## Manual verification

Since embedding/generation quality can't be captured by unit tests, verify
end-to-end behavior manually after setup:

1. `./setup.sh`
2. `srg use-engagement demo`, then `srg ingest`
3. `srg generate SI-5 --context "..."` — confirm the response starts with
   `Customer: DEMO` and notes that no customer standard was found
4. `srg create-engagement test-customer`
5. Add a sample SI-5 customer standard and fictional private-context file to
   the paths printed by the command
6. `srg ingest` — confirm only that engagement's customer/private data is indexed
7. Re-run `srg ingest` with no changes — confirm 0 re-embedded
8. `srg generate SI-5 --context "..."` — confirm the response starts with
   `Customer: Test Customer` and reflects its standard as authoritative
9. `srg generate ZZ-99` — confirm refusal with non-zero exit
10. `srg generate SI-5 --format text` — confirm the output has no Markdown
    syntax, smart quotes, em-dashes, or bullets, and that every character is
    plain ASCII
11. `srg generate "SC-8(1)" --context "TLS 1.3 protects information in
    transit."` — confirm control enhancements are retrieved and generated
    like base controls
12. Confirm both Markdown and plain-text output contain exactly one
    `[Validations]` section after the implementation narrative
13. Ingest a control whose requirements clearly need something not in the
    shared baseline or active engagement folders
    (omit one detail on purpose) and run `srg generate` for it — confirm
    the tool asks a clarifying question, answer it at the prompt, and
    confirm the final response reflects your answer
14. Repeat step 13 but decline to give useful answers (or set
    `SRG_MAX_FOLLOWUP_TURNS=0`) — confirm the tool still produces a response
    within the round limit, opening with a note that information was
    missing and containing `[PLACEHOLDER: ...]` markers rather than
    guessing
15. `git status` — confirm the test customer's engagement files do not appear

## License

The original software and documentation are available under the
[MIT License](../LICENSE). Third-party publications and separately
downloaded runtime components are governed by their own terms; see
[THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md).
