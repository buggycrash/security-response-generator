#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd -P "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
# shellcheck source=scripts/common.sh
source "$PROJECT_ROOT/scripts/common.sh"

INSTALL_DIR="${SRG_INSTALL_DIR:-$HOME/.local/bin}"
GEN_MODEL="${SRG_GEN_MODEL:-$SRG_DEFAULT_GEN_MODEL}"
EMBED_MODEL="${SRG_EMBED_MODEL:-$SRG_DEFAULT_EMBED_MODEL}"
INSTALL_DEV=0
DEV_ONLY=0
SKIP_MODELS=0
CHECK_ONLY=0

usage() {
  cat <<EOF
Usage: ./setup.sh [options]

Install Security Response Generator and make the 'srg' command available.

Options:
  --check              Check installation health without changing anything
  --dev                Install development and test dependencies
  --dev-only           Set up tests and Git hooks; skip launcher and Ollama
  --skip-models        Do not download Ollama models
  --model MODEL        Use and download a different generation model
  --install-dir DIR    Install the srg launcher here (default: ~/.local/bin)
  -h, --help           Show this help

Environment:
  SRG_GEN_MODEL        Generation model (default: $SRG_DEFAULT_GEN_MODEL)
  SRG_EMBED_MODEL      Embedding model (default: $SRG_DEFAULT_EMBED_MODEL)
  SRG_INSTALL_DIR      Launcher directory (default: ~/.local/bin)
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --check) CHECK_ONLY=1 ;;
    --dev) INSTALL_DEV=1 ;;
    --dev-only)
      INSTALL_DEV=1
      DEV_ONLY=1
      ;;
    --skip-models) SKIP_MODELS=1 ;;
    --model)
      [ "$#" -ge 2 ] || { srg_error "--model requires a value"; exit 2; }
      GEN_MODEL="$2"
      shift
      ;;
    --install-dir)
      [ "$#" -ge 2 ] || { srg_error "--install-dir requires a value"; exit 2; }
      INSTALL_DIR="$2"
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      srg_error "Unknown option: $1"
      usage >&2
      exit 2
      ;;
  esac
  shift
done

if [ "$DEV_ONLY" -eq 0 ] &&
  { srg_model_is_cloud "$GEN_MODEL" || srg_model_is_cloud "$EMBED_MODEL"; }; then
  srg_error "Cloud-tagged Ollama models are not supported. Configure local models only."
  exit 2
fi

pass() {
  printf 'PASS  %s\n' "$1"
}

fail() {
  printf 'FAIL  %s\n' "$1"
  CHECK_FAILURES=$((CHECK_FAILURES + 1))
}

check_installation() {
  local python_display
  CHECK_FAILURES=0

  if [ -x "$PROJECT_ROOT/.venv/bin/python" ] &&
    srg_python_version_ok "$PROJECT_ROOT/.venv/bin/python"; then
    python_display="$("$PROJECT_ROOT/.venv/bin/python" --version 2>&1)"
    pass "$python_display virtual environment"
  else
    fail "Python 3.11+ virtual environment"
  fi

  if [ -x "$PROJECT_ROOT/.venv/bin/srg" ]; then
    pass "SRG package installed"
  else
    fail "SRG package installed"
  fi

  if [ -f "$PROJECT_ROOT/engagements/demo/engagement.json" ] &&
    [ -f "$PROJECT_ROOT/engagements/demo/private_context/demo-system.md" ]; then
    pass "Built-in demo engagement"
  else
    fail "Built-in demo engagement"
  fi

  if [ -L "$INSTALL_DIR/srg" ] &&
    [ "$(readlink "$INSTALL_DIR/srg")" = "$PROJECT_ROOT/srg" ]; then
    pass "Launcher installed at $INSTALL_DIR/srg"
  elif [ -e "$INSTALL_DIR/srg" ]; then
    fail "$INSTALL_DIR/srg exists but is not this project's launcher"
  else
    fail "Launcher installed at $INSTALL_DIR/srg"
  fi

  if command -v ollama >/dev/null 2>&1; then
    pass "Ollama command installed"
    if srg_ollama_ready; then
      pass "Ollama daemon reachable"
      if srg_model_installed "$GEN_MODEL"; then
        pass "Generation model $GEN_MODEL"
      else
        fail "Generation model $GEN_MODEL"
      fi
      if srg_model_installed "$EMBED_MODEL"; then
        pass "Embedding model $EMBED_MODEL"
      else
        fail "Embedding model $EMBED_MODEL"
      fi
    else
      fail "Ollama daemon reachable"
    fi
  else
    fail "Ollama command installed"
  fi

  if [ "$CHECK_FAILURES" -ne 0 ]; then
    printf '\n%d check(s) failed. Run %s/setup.sh to repair the installation.\n' \
      "$CHECK_FAILURES" "$PROJECT_ROOT"
    return 1
  fi
  printf '\nAll checks passed.\n'
}

if [ "$CHECK_ONLY" -eq 1 ]; then
  check_installation
  exit $?
fi

printf 'Setting up Security Response Generator\n\n'

TOTAL_STEPS=6
if [ "$DEV_ONLY" -eq 1 ]; then
  TOTAL_STEPS=4
fi

printf '[1/%s] Checking Python...\n' "$TOTAL_STEPS"
PYTHON_BIN=""
if command -v python3 >/dev/null 2>&1 && srg_python_version_ok "$(command -v python3)"; then
  PYTHON_BIN="$(command -v python3)"
elif command -v pyenv >/dev/null 2>&1 &&
  pyenv which python >/dev/null 2>&1 &&
  srg_python_version_ok "$(pyenv which python)"; then
  PYTHON_BIN="$(pyenv which python)"
fi

if [ -z "$PYTHON_BIN" ]; then
  srg_error "Python 3.11 or newer is required."
  srg_error "Install it with your OS package manager or pyenv, then rerun ./setup.sh."
  exit 1
fi
printf '      %s\n' "$("$PYTHON_BIN" --version 2>&1)"

printf '[2/%s] Preparing the virtual environment...\n' "$TOTAL_STEPS"
if [ -e "$PROJECT_ROOT/.venv" ] &&
  { [ ! -x "$PROJECT_ROOT/.venv/bin/python" ] ||
    ! srg_python_version_ok "$PROJECT_ROOT/.venv/bin/python"; }; then
  BACKUP_PATH="$PROJECT_ROOT/.venv.invalid.$(date +%Y%m%d%H%M%S)"
  mv "$PROJECT_ROOT/.venv" "$BACKUP_PATH"
  printf '      Moved the invalid environment to %s\n' "$BACKUP_PATH"
fi
if [ ! -x "$PROJECT_ROOT/.venv/bin/python" ]; then
  "$PYTHON_BIN" -m venv "$PROJECT_ROOT/.venv"
  printf '      Created .venv\n'
else
  printf '      Existing .venv is healthy\n'
fi

printf '[3/%s] Installing SRG...\n' "$TOTAL_STEPS"
INSTALL_TARGET="-e"
if [ "$INSTALL_DEV" -eq 1 ]; then
  "$PROJECT_ROOT/.venv/bin/python" -m pip install "$INSTALL_TARGET" "${PROJECT_ROOT}[dev]"
else
  "$PROJECT_ROOT/.venv/bin/python" -m pip install "$INSTALL_TARGET" "$PROJECT_ROOT"
fi

if [ "$DEV_ONLY" -eq 1 ]; then
  printf '[4/4] Enabling developer checks...\n'
  if ! git -C "$PROJECT_ROOT" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    srg_error "Developer setup requires a Git checkout."
    exit 1
  fi
  git -C "$PROJECT_ROOT" config core.hooksPath .git-hooks
  "$PROJECT_ROOT/.venv/bin/python" -c 'import pytest, ruff'
  printf '      Pre-commit hook enabled\n'
  cat <<'EOF'

Developer setup complete.

The project launcher, Ollama daemon, and installed models were not changed.
Run .git-hooks/pre-commit to execute the checks now.
EOF
  exit 0
fi

printf '[4/6] Installing the command launcher...\n'
mkdir -p "$INSTALL_DIR"
if [ -e "$INSTALL_DIR/srg" ] || [ -L "$INSTALL_DIR/srg" ]; then
  if [ -L "$INSTALL_DIR/srg" ] && [ "$(readlink "$INSTALL_DIR/srg")" = "$PROJECT_ROOT/srg" ]; then
    printf '      Existing launcher is current\n'
  else
    srg_error "$INSTALL_DIR/srg already exists and is not managed by this project."
    srg_error "Choose another location with --install-dir DIR or move that file."
    exit 1
  fi
else
  ln -s "$PROJECT_ROOT/srg" "$INSTALL_DIR/srg"
  printf '      Installed %s/srg\n' "$INSTALL_DIR"
fi

printf '[5/6] Checking Ollama and models...\n'
if ! command -v ollama >/dev/null 2>&1; then
  srg_error "Ollama is not installed."
  srg_error "Install it from https://ollama.com/download, then rerun ./setup.sh."
  exit 1
fi
srg_start_ollama

if [ "$SKIP_MODELS" -eq 1 ]; then
  printf '      Model downloads skipped\n'
else
  cat <<'EOF'
      WARNING: Model weights are not included in SRG or covered by its MIT License.
      Setup downloads separately licensed runtime components into Ollama.
      Default model terms:
        Gemma 4 E4B (QAT): https://ai.google.dev/gemma/terms
        EmbeddingGemma: https://ai.google.dev/gemma/terms
EOF
  printf '      Configured generation model: %s\n' "$GEN_MODEL"
  printf '      Configured embedding model: %s\n' "$EMBED_MODEL"
  for model in "$GEN_MODEL" "$EMBED_MODEL"; do
    if srg_model_installed "$model"; then
      printf '      %s already installed\n' "$model"
    else
      printf '      Pulling %s...\n' "$model"
      ollama pull "$model"
    fi
  done
fi

mkdir -p \
  "$PROJECT_ROOT/knowledge_base" \
  "$PROJECT_ROOT/chroma_db" \
  "$PROJECT_ROOT/engagements/demo/customer_standards" \
  "$PROJECT_ROOT/engagements/demo/private_context" \
  "$PROJECT_ROOT/engagements/demo/chroma_db" \
  "$PROJECT_ROOT/engagements/demo/responses"

printf '[6/6] Verifying the installation...\n'
"$PROJECT_ROOT/.venv/bin/srg" --help >/dev/null
"$PROJECT_ROOT/.venv/bin/srg" show-engagement >/dev/null
printf '      SRG command passed its startup check\n'

cat <<EOF

Setup complete.
EOF

case ":$PATH:" in
  *":$INSTALL_DIR:"*)
    cat <<EOF

Next:
  srg ingest
  srg generate SI-5 --context "..."
EOF
    ;;
  *)
    cat <<EOF

Add the launcher directory to your PATH once:
  export PATH="$INSTALL_DIR:\$PATH"

To make that permanent, add the same line to your shell profile. Then run:
  srg ingest
EOF
    ;;
esac

printf '\nRun ./setup.sh --check at any time to diagnose the installation.\n'
