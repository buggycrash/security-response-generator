# Security Response Generator

[![CI](https://github.com/buggycrash/security-response-generator/actions/workflows/ci.yml/badge.svg)](https://github.com/buggycrash/security-response-generator/actions/workflows/ci.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

## Why SRG?

SRG is built for a specific, common situation: a small team has no dedicated
security professional, but still needs to write clear prose explaining how
its system satisfies security controls. That work often falls to an existing
team member who understands the system but does not spend every day writing
control responses.

After an hour or two of initial setup, that team member can produce a draft
response in minutes instead of starting from a blank page. SRG maps the prose
to the applicable NIST requirements and customer-specific parameters, using
concrete system details supplied either in reusable private context files or
with the individual request. The result is a faster drafting process with
more consistent requirement coverage, terminology, and writing style across
controls and over time. Every response remains a draft and should be reviewed
for accuracy before it is submitted.

Security Response Generator (`srg`) is a local CLI that drafts NIST SP
800-53 Release 5.2.0 control responses from:

- The included NIST SP 800-53 Release 5.2.0 catalog
- Customer-specific standards
- Private system context
- Notes supplied with each request

Embeddings and response generation run locally through Ollama. Generated
responses are labeled with the active customer engagement so customer
content and output are not easily confused.

SRG connects only to Ollama on the local loopback interface, refuses
cloud-tagged Ollama models, and disables Chroma product telemetry.

For architecture, configuration, model selection, troubleshooting, and
implementation details, see the
[technical README](docs/technical-readme.md).

## Prerequisites

- Customer approval to use local AI tooling for the engagement
- Python 3.11 or newer
- [Ollama](https://ollama.com/download)
- Approximately 7 GB of available GPU or unified memory for the default
  generation and embedding models

## Supported platforms

SRG has been tested only on Ubuntu 22.04. It is not compatible with native
Windows, but it can run within WSL2. macOS may be compatible but has not yet
been tested.

> [!WARNING]
> The model weights are not included in this source repository and are not
> covered by its MIT License. With the default model configuration, setup
> downloads Llama 3.1 and EmbeddingGemma into Ollama's local model storage
> unless `./setup.sh --skip-models` is used, making them separately licensed
> runtime components of the installed project. Llama 3.1 is subject to the
> [Llama 3.1 Community License](https://ollama.com/library/llama3.1%3A8b-text-q3_K_M/blobs/0ba8f0e314b4),
> and EmbeddingGemma is subject to the
> [Gemma Terms of Use](https://ai.google.dev/gemma/terms).

## Install

Clone the repository, enter its directory, and run:

```bash
./setup.sh
```

Setup creates the Python environment, installs `srg`, starts Ollama when
needed, downloads the default models, and installs the command launcher.
No virtual-environment activation or `source` command is required.

If setup reports that `~/.local/bin` is not on your `PATH`, follow the
one-time instruction it prints.

Check installation health at any time:

```bash
./setup.sh --check
```

## Remove the external installation

To remove the launcher and configured Ollama models created by setup:

```bash
./cleanup.sh
```

The script previews its actions and requires an exact typed confirmation. It
removes only a launcher symlink owned by this checkout and warns before
removing Ollama models, which may be shared with other projects. Use
`--keep-models` to retain them.

`setup.sh` does not edit shell profiles, so cleanup prints the exact `PATH`
line to remove if you added it manually. Project files, the virtual
environment, indexes, and engagement data are retained by default. To also
permanently delete local engagement data, use
`./cleanup.sh --wipe-engagements`; this requires a second exact typed
confirmation and preserves the committed fictional demo seed files.

## Try the built-in demo

The initial engagement is `DEMO`. It includes:

- The project-level NIST SP 800-53 Release 5.2.0 catalog
- Fictional private context for `DEMO-ECMS`
- No customer-specific standards

Ingest the source material:

```bash
srg ingest
```

Generate an SI-5 response:

```bash
srg generate SI-5 --context "Use the documented monitoring and advisory process."
```

The response begins with:

```text
Customer: DEMO
```

## Update the NIST catalog

SRG includes a reproducible converter for NIST's machine-readable OSCAL
catalog. To download the currently supported official release and replace the
local ingest-ready Markdown:

```bash
srg update-nist
srg ingest --source knowledge_base
```

`update-nist` validates the catalog, records its version, source URL, and
SHA-256 digest in the generated file, and converts control statements,
organization-defined parameters, guidance, and related-control references
into the headings expected by SRG's chunker. The source catalog also contains
SP 800-53A assessment procedures; those are intentionally excluded to keep
control-response retrieval focused.

For a future official release or a previously downloaded catalog, use
`--source` with an HTTPS URL or local JSON path. Use `--output` to choose a
different Markdown destination. This command is the only part of this
workflow that downloads NIST content; ingest and generation remain local.

## Create a customer engagement

Use a name that combines the governing state and system name:

```bash
srg create-engagement northbridge-SALI
```

For a more formal response label:

```bash
srg create-engagement northbridge-SALI \
  --customer-name "State of Northbridge"
```

The command activates the engagement and prints its document locations:

```text
Add customer standards files in:
  .../engagements/northbridge-sali/customer_standards

Add private system context details in:
  .../engagements/northbridge-sali/private_context
```

Copy the appropriate documents into those folders, then run:

```bash
srg ingest
srg generate SI-5 --context "Additional notes specific to this response."
```

The shared NIST catalog is not duplicated between engagements. Customer
standards, private context, indexes, and generated response folders remain
isolated under `engagements/<engagement-name>/`.

## Switch engagements

List available engagements:

```bash
srg list-engagements
```

Show the active engagement and its document paths:

```bash
srg show-engagement
```

Activate an existing engagement:

```bash
srg use-engagement northbridge-SALI
```

## Save or export a response

Markdown is the default:

```bash
srg generate SI-5 -o response.md
```

For Xacta, Archer, ServiceNow IRM/CAM, or another system requiring plain
ASCII text:

```bash
srg generate SI-5 --format text -o response.txt
```

Plain-text output retains normal capitalization while removing Markdown,
Unicode punctuation, and other non-ASCII characters.

## Ask a question

While not a general chatbot, SRG does allow the user to query the system
for information that might only be apparent after cross checking several
documents, or perhaps requires interpreting vague or conflicting customer
guidance.

`srg chat` answers a freeform question grounded in the active engagement's
indexed material -- the customer/state standard, NIST 800-53 baseline, and
private system context -- without needing a specific control ID:

```bash
srg chat "What is the password complexity requirement?"
srg chat "Does the customer provide a System Integrity policy?"
```

Unlike `srg generate`, `chat` is a single-shot lookup: it prints one answer
and exits. The answer is grounded only in retrieved material; if nothing
indexed covers the question, the model says so rather than guessing. Output
is labeled with the active engagement and carries a draft-answer disclaimer,
the same as `generate`.

## Common next steps

```bash
srg --help
srg ingest --help
srg generate --help
srg chat --help
```

See [docs/technical-readme.md](docs/technical-readme.md) for:

- Model sizing and model selection
- Incremental ingestion and rebuild behavior
- Retrieval and prompt architecture
- Environment variables
- Security and privacy details
- Troubleshooting
- Development and testing

This part-time project is not currently accepting external contributions.
See [CONTRIBUTING.md](CONTRIBUTING.md) for details and the development
workflow. See [SECURITY.md](SECURITY.md) before reporting a security issue.

## License

The original software and documentation are available under the
[MIT License](LICENSE). Third-party publications and separately downloaded
runtime components are governed by their own terms; see
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
