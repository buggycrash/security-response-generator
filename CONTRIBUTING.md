# Contributing

Security Response Generator is a part-time personal project and is not
currently accepting external contributions. Unsolicited issues and pull
requests may be closed without review. You are welcome to fork and modify the
project under the terms of its [MIT License](LICENSE).

The development notes below are retained for the maintainer and for anyone
working from a fork. This policy may change as the project matures.

## Protect engagement data

Never include customer standards, private system context, generated customer
responses, credentials, model data, local vector indexes, or identifying log
output in an issue, commit, or pull request. Use the fictional `demo`
engagement and sanitized fixtures when reproducing a problem.

Before sending a suspected vulnerability, review the project's limited
support commitments in [SECURITY.md](SECURITY.md).

## Set up a development environment

Python 3.11 or newer is required. Ollama is not needed for the automated test
suite.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"
git config core.hooksPath .git-hooks
```

The final command enables this repository's pre-commit hook for your clone.
If Ollama is already installed and you also want the normal `srg` launcher,
`./setup.sh --dev --skip-models` is a convenient alternative.

## Verify a change

Keep changes focused and add or update tests for behavior changes. Run:

```bash
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/pytest
```

Use `.venv/bin/ruff format .` to apply formatting. The pre-commit hook and CI
run the same checks. Tests must not require network access, downloaded model
weights, or a running Ollama daemon.

Changes to retrieval or generation behavior may also need the manual checks
in the [technical README](docs/technical-readme.md#manual-verification).
