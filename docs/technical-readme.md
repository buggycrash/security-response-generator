# Security Response Generator

A local-first CLI that drafts security control responses (e.g.
NIST 800-53 controls like "SI-5") for a compliance assessor to review, using
retrieval-augmented generation (RAG) grounded in three tiers of source
material. Output can be Markdown or plain ASCII text, depending on what the
target system of record accepts:

1. **NIST 800-53 rev5 baseline** — the most typical control catalog.  If your customer uses something different like PCI-DSS, HIPAA, or ISO/IEC 27001:2022 you'll need to refactor a number of things in this tool.
2. **Customer/state-specific standards** — e.g. a state's published
   per-control guidance with state-specific parameter values. When present
   for a control, this is treated as **authoritative** over generic NIST
   language.  Its content must match the control catalog IDs.  
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
  closed-source or overseas API
- Refuses to answer (rather than hallucinate) if a control ID has no match
  in the NIST baseline
    - This is a dedicated **tool**, NOT a general chatbot
- Interactive follow-up questions when a material part of the control isn't
  covered by the supplied context, up to a configurable round limit, with a
  best-effort placeholder-annotated response if the model still isn't done
- Incremental ingestion — only re-embeds files that changed
- Reproducible download and conversion of official NIST SP 800-53 OSCAL
  catalogs into chunker-compatible Markdown
- Isolated customer engagements with a shared NIST 800-53 baseline, so
  switching customers never requires deleting or replacing another
  customer's files
- Every generated response is labeled with its customer engagement (or
  `DEMO`) in code rather than relying on the model to identify it
- Markdown or plain ASCII text output (`--format`), printed to stdout and
  optionally written to a file — plain text is enforced in code, not just by
  prompt instruction, for evidence/GRC systems that reject any formatting or
  non-ASCII characters

## Caveats

- The ingest and retrieval processors remain tailored to NIST SP 800-53
  control IDs and heading structure. Use `srg update-nist` for official OSCAL
  updates; dropping in an arbitrary catalog or generic JSON-to-Markdown
  conversion may not preserve reliable control boundaries.

## Technology Stack

- **Language**: Python
- **Generation model**: [Llama 3.1 8B](https://ollama.com/library/llama3.1)
  via [Ollama](https://ollama.com) by default — a dense, text-only model
  that reliably stays grounded on the specific control ID being asked
  about, while still fitting comfortably in 12GB of VRAM alongside the
  embedding model. Swappable via `SRG_GEN_MODEL` -- see
  [Choosing a generation model](#choosing-a-generation-model) for smaller
  and larger alternatives and the tradeoffs found in practice.
- **Embedding model**: EmbeddingGemma via Ollama
- **Vector store**: ChromaDB (embedded/local, no server)
- **CLI**: [Typer](https://typer.tiangolo.com)

## Prerequisites

- Permission from your customer to use this tool.  Different customers have very different AI permissions models.  
- Python 3.11+
- [Ollama](https://ollama.com/download) installed, with the daemon running
- Ubuntu 22.04 is the only tested operating system. Native Windows is not
  supported; WSL2 is supported. macOS may be compatible but has not yet been
  tested.
- A modest amount of VRAM or unified memory for `llama3.1:8b` (~4.9GB
  download, ~7GB of VRAM once loaded alongside `embeddinggemma`) -- fits
  comfortably on a 12GB card. See
  [Choosing a generation model](#choosing-a-generation-model) for smaller
  options if your hardware is more constrained, or larger ones if you have
  VRAM to spare.

## Installation

1. **Run the setup script (recommended)**:
   
   Clone the repo to your local system, then
   ```bash
   cd security-response-generator
   ./setup.sh
   ```
   This creates a `.venv`, installs the package, starts Ollama when needed,
   pulls both models (`llama3.1:8b`, `embeddinggemma`), and installs a small
   launcher at `~/.local/bin/srg`. The launcher invokes the project virtual
   environment directly, so it does not need to be activated.

   A built-in `demo` engagement is active initially. It uses the included
   NIST SP 800-53 Release 5.2.0 catalog, fictional private system context, and no
   customer-specific standards.

   If `~/.local/bin` is not already on your `PATH`, setup prints the exact
   one-time shell-profile change to add it. You can check or troubleshoot
   the installation at any time without changing anything:
   ```bash
   ./setup.sh --check
   ```

   Other setup options include `--dev`, `--skip-models`, `--model MODEL`,
   and `--install-dir DIR`; run `./setup.sh --help` for details.

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

The default, `llama3.1:8b`, was picked after directly comparing it
side-by-side against smaller and larger alternatives on this exact tool,
not just on paper. The thing that actually matters for this workload isn't
raw benchmark scores -- it's whether the model reliably stays locked onto
the *specific control ID* it was asked about, using only the material
retrieved for that control. In repeated testing, `llama3.1:8b` did; smaller
models sometimes didn't (see below). It's also a plain dense, text-only
model (no vision/audio encoders to load), so its ~7GB resident footprint
(Q4_K_M) coexists comfortably with `embeddinggemma` on a 12GB card without
the evict-and-reload cycling that tighter-fitting models can trigger on
every `srg generate` call (see the "Responses are much slower than
expected" entry in [Troubleshooting](#troubleshooting) for what that
looks like).

If your hardware is more constrained, **[Phi-4-mini](https://ollama.com/library/phi4-mini)**
(Microsoft) is smaller (~3.8B parameters, ~2.5GB download) and loads even
faster. Be aware of the tradeoff found in testing, though: it noticeably
drifted off the requested control ID more often than `llama3.1:8b` --
answering under the wrong control heading entirely, or letting unrelated
material (e.g. from `private_context`) bleed into the response -- even once
retrieval was confirmed to be feeding it clean, on-topic material for the
right control. That's a model-capability gap, not a retrieval bug. Prefer
`llama3.1:8b` if your hardware can fit it; treat `phi4-mini` as a
speed/VRAM tradeoff you're consciously accepting, not a drop-in equivalent.

If you have significantly more VRAM available (roughly 16GB+),
**[Gemma 4 E4B](https://ollama.com/library/gemma4)** (Google) is also an
option -- larger and potentially more capable, but multimodal (it bundles
vision/audio encoders this tool never uses), which adds load-time overhead
and made it prone to VRAM-eviction cycling on a 12GB card in testing, since
its footprint sits right at the edge of what's available alongside
`embeddinggemma`.

Switch models with the `SRG_GEN_MODEL` environment variable -- no code
changes needed, since `srg` talks to Ollama's generic chat API regardless
of which model is behind it:

```bash
ollama pull phi4-mini
SRG_GEN_MODEL=phi4-mini srg generate SI-5 --context "..."
```

To make a switch permanent for your own sessions, export `SRG_GEN_MODEL` in
your shell profile. The
interactive follow-up-question feature (see
[Interactive follow-up questions](#interactive-follow-up-questions)) is
enforced via Ollama's structured-output/JSON-schema support, not by asking
the model to nicely follow a formatting convention -- so it stays reliable
regardless of which generation model you pick, including smaller/leaner
ones that are otherwise less rigorous about following instructions.

The embedding model (`embeddinggemma`) is a separate, much smaller model
used only for retrieval, and typically doesn't need to change when you swap
the generation model.

### Using a customer-approved cloud gateway (e.g. AWS Bedrock)

Everything above assumes local models because most engagements haven't
pre-approved sending customer or system data to any external service (see
[Prerequisites](#prerequisites) and
[Security & Privacy](#security--privacy)). That default flips for a specific,
common situation: a customer that already provides AWS Bedrock as their own
sanctioned interface to a set of approved models. In that case, routing
generation through the customer's Bedrock endpoint isn't sending data to an
arbitrary third party -- it stays inside a boundary the customer has already
vetted, under whatever data-handling terms they negotiated with AWS. If
that's your situation, it can be a reasonable choice, and often a better
one: Bedrock exposes larger, frontier-class models than what's practical to
run locally on constrained hardware, which can mean meaningfully more
nuanced, better-grounded responses than `llama3.1:8b` or the other local
options above.

A few things worth confirming before doing this on any given engagement:

- Get it in writing the same way you would any other AI usage on the
  engagement -- Bedrock accounts and configurations vary (region, logging/
  retention via CloudTrail, cross-region inference, per-model-provider data
  terms), and "the customer approved Bedrock" doesn't automatically cover
  every model or setting available through it.
- Retrieval and embedding (`embeddinggemma`) would stay local exactly as
  today -- this only changes where the assembled prompt for the
  *generation* step gets sent, the same distinction as choosing between
  local models above.

This isn't implemented today -- `llm/ollama_client.py`'s `chat_messages()`
is currently the only function that talks to a generation model, and it
assumes Ollama's chat API. Adding Bedrock support would mean a parallel
client (e.g. via `boto3`'s `bedrock-runtime` Converse API) behind a
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
   **This may take some time, several minutes on the initial run.**  
   Re-run this any time files in those folders change — unchanged files are
   skipped automatically. Use `--source knowledge_base|customer_standards|private_context`
   to ingest just one tier. `--rebuild` rebuilds only the active
   engagement's customer/private index. The shared NIST baseline requires
   the deliberately explicit `--rebuild-baseline` option.
   Large files show a progress bar on stderr as embedding batches complete, so a long ingest doesn't look stalled.

4. **Generate a response**:
   ```bash
   srg generate "SI-5" --context "our environment uses a SaaS SIEM for continuous monitoring"
   ```
   **This may take some time, ESPECIALLY on the initial request.**  
   Prints Markdown to stdout by default. Add `-o response.md` to also write
   it to a file (or to a directory, in which case a customer-labeled filename
   like `virginia_SI-5_20260715.md` is generated). Every response begins
   with `Customer: Virginia` (or `Customer: DEMO`).
   A spinner shows on stderr while waiting for the model (generation can take a couple of minutes depending on your hardware) so a long wait doesn't look hung — it doesn't pollute stdout, so piping/redirecting output still works cleanly.

   For evidence/GRC systems that only accept raw text with no formatting (maybe Archer or Xacta),
   add `--format text`:
   ```bash
   srg generate "SI-5" --format text --context "..." -o response.txt
   ```
   This produces plain ASCII output — no Markdown syntax, no smart quotes,
   em-dashes, bullets, or other non-ASCII characters.  
   A directory target with `--format text` gets a `.txt` filename instead
   of `.md`.

### Interactive follow-up questions

If the model determines that a distinct, material part of the control
isn't covered by the retrieved material, your `--context` notes, or anything
already discussed, it can ask you a clarifying question instead of guessing:

```
$ srg generate "SI-5" --context "we use Acme Sentinel for monitoring"

What is the required review/dissemination timeframe for security alerts in
your environment?

Your answer: reviewed within 24 hours, disseminated within 48 hours
```

Answer at the prompt and it continues the same conversation — no need to
re-run the command. This can happen up to `SRG_MAX_FOLLOWUP_TURNS` times
(default **2**). If it still isn't done after that, one final call produces
a best-effort response anyway: it opens with a brief note that some
information wasn't available, and inserts `[PLACEHOLDER: ...]` markers in
place of anything it couldn't address confidently, so you can fill those in
by hand before submitting to the assessor.  

The tool is biased to generate *something* rather than looping/questioning indefinitely.

## Customer engagements

Customer documents and indexes are isolated under `engagements/<name>/`.
The NIST baseline remains shared and is not duplicated for each customer.

```bash
srg create-engagement virginia  # creates and activates it
srg show-engagement             # shows active folders
srg list-engagements
srg use-engagement demo
srg use-engagement virginia
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
./setup.sh --dev     # install the package and development dependencies
.venv/bin/pytest               # run tests
.venv/bin/ruff check .          # lint
.venv/bin/ruff format --check . # verify formatting
```

To enable the repository's pre-commit checks for this clone, run:

```bash
git config core.hooksPath .git-hooks
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
   `prompts/instructions.md` (editable — controls tone, structure, and the
   authoritative-standards rule) and a format-specific instruction (Markdown
   vs. plain ASCII text, chosen via `--format`), then sent to the generation
   model (`llama3.1:8b` by default — see
   [Choosing a generation model](#choosing-a-generation-model)) via Ollama.
   The model is asked for a JSON-schema-constrained reply (`needs_info`,
   `question`, `response`) rather than free-form text, so the follow-up
   mechanism below works reliably regardless of which model is configured.
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
├── knowledge_base/                  # committed: NIST 800-53 rev5, public refs
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
├── src/security_response_generator/
│   ├── cli.py                       # update, ingest, engagement, and generation commands
│   ├── config.py                    # models, paths, chunking, top-k (env-overridable)
│   ├── ingest/                      # loaders, chunking, manifest, Chroma store
│   ├── generation/                  # retrieval, prompt assembly, ASCII normalizer
│   └── llm/ollama_client.py         # Ollama embed/chat wrapper
├── chroma_db/                       # gitignored: Chroma persistence, created at runtime
└── tests/
```

## Troubleshooting

- **`ollama: command not found`**: install from https://ollama.com/download.
- **Ollama daemon not running**: the `srg` launcher normally starts it
  automatically. If startup fails, review `/tmp/srg-ollama-serve.log` (or
  `$TMPDIR/srg-ollama-serve.log` when `TMPDIR` is set).
- **Installation seems incomplete**: run `./setup.sh --check` for individual
  Python, launcher, Ollama, and model health checks.
- **`srg generate` refuses every control ID**: run `srg ingest` first — the
  NIST baseline collection is empty until `knowledge_base/` is ingested.
- **Model pull is slow/fails**: `llama3.1:8b` is a ~4.9GB download (see
  [Choosing a generation model](#choosing-a-generation-model) for smaller
  or larger alternatives); check disk space and network connectivity.
- **Responses are much slower than expected**: run `ollama ps` to check
  whether the model is fully on GPU or partially spilled to system RAM/CPU
  (Ollama does this automatically and silently if VRAM is tight, and it's a
  common source of unexplained slowness). Lowering context length or closing
  other GPU-heavy applications usually resolves it.
- **`srg ingest` fails with `ResponseError: ... EOF` on a large document**
  (e.g. the full NIST 800-53 catalog): this is the
  embedding model's runner subprocess getting OOM-killed — check Ollama's
  own log (`journalctl -u ollama`, or the terminal running `ollama serve`
  if you started it manually) for a `signal: killed` line to confirm.
  `srg ingest` already batches embedding requests (`SRG_EMBED_BATCH_SIZE`,
  default 32) to avoid this; if it still happens on a memory-constrained
  machine (e.g. WSL2 with a low `.wslconfig` memory cap), try lowering the
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
11. Ingest a control whose requirements clearly need something not in the
    shared baseline or active engagement folders
    (omit one detail on purpose) and run `srg generate` for it — confirm
    the tool asks a clarifying question, answer it at the prompt, and
    confirm the final response reflects your answer
12. Repeat step 11 but decline to give useful answers (or set
    `SRG_MAX_FOLLOWUP_TURNS=0`) — confirm the tool still produces a response
    within the round limit, opening with a note that information was
    missing and containing `[PLACEHOLDER: ...]` markers rather than
    guessing
13. `git status` — confirm the test customer's engagement files do not appear

## License

The original software and documentation are available under the
[MIT License](../LICENSE). Third-party publications and separately
downloaded runtime components are governed by their own terms; see
[THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md).
