#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd -P "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
# shellcheck source=scripts/common.sh
source "$PROJECT_ROOT/scripts/common.sh"

INSTALL_DIR="${SRG_INSTALL_DIR:-$HOME/.local/bin}"
GEN_MODEL="${SRG_GEN_MODEL:-$SRG_DEFAULT_GEN_MODEL}"
EMBED_MODEL="${SRG_EMBED_MODEL:-$SRG_DEFAULT_EMBED_MODEL}"
KEEP_MODELS=0
WIPE_ENGAGEMENTS=0

usage() {
  cat <<EOF
Usage: ./cleanup.sh [options]

Remove the external resources created by setup.sh.

Options:
  --keep-models         Leave the configured Ollama models installed
  --wipe-engagements    Also permanently delete local engagement data
  --model MODEL         Remove this generation model instead of $SRG_DEFAULT_GEN_MODEL
  --install-dir DIR     Look for the srg launcher here (default: ~/.local/bin)
  -h, --help            Show this help

Environment:
  SRG_GEN_MODEL         Generation model (default: $SRG_DEFAULT_GEN_MODEL)
  SRG_EMBED_MODEL       Embedding model (default: $SRG_DEFAULT_EMBED_MODEL)
  SRG_INSTALL_DIR       Launcher directory (default: ~/.local/bin)

The project virtual environment and shared NIST index are left in place.
Engagement data is deleted only with --wipe-engagements and a second,
exact typed confirmation.
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --keep-models) KEEP_MODELS=1 ;;
    --wipe-engagements) WIPE_ENGAGEMENTS=1 ;;
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

if [ "$PROJECT_ROOT" = "/" ] || [ ! -f "$PROJECT_ROOT/setup.sh" ]; then
  srg_error "Cannot safely identify the Security Response Generator project root."
  exit 1
fi

LAUNCHER="$INSTALL_DIR/srg"

printf 'Security Response Generator cleanup\n\n'
if [ -L "$LAUNCHER" ] && [ "$(readlink "$LAUNCHER")" = "$PROJECT_ROOT/srg" ]; then
  printf 'Launcher to remove:\n  %s -> %s/srg\n' "$LAUNCHER" "$PROJECT_ROOT"
elif [ -e "$LAUNCHER" ] || [ -L "$LAUNCHER" ]; then
  printf 'Launcher will NOT be removed (it is not owned by this checkout):\n  %s\n' "$LAUNCHER"
else
  printf 'Launcher is already absent:\n  %s\n' "$LAUNCHER"
fi

if [ "$KEEP_MODELS" -eq 1 ]; then
  printf '\nOllama models will be kept.\n'
else
  cat <<EOF

Configured Ollama models to remove:
  $GEN_MODEL
  $EMBED_MODEL

WARNING: Ollama models are shared across local applications. setup.sh did not
record whether these models were already installed, so removing them may affect
other projects. Re-run with --keep-models to leave them installed.
EOF
fi

if [ "$WIPE_ENGAGEMENTS" -eq 1 ]; then
  cat <<EOF

WARNING: Local engagement data will be permanently deleted, including customer
standards, private system context, generated responses, and engagement indexes.
The committed fictional demo seed files will be preserved.
EOF
else
  printf '\nLocal engagement data will be kept.\n'
fi

printf '\nType REMOVE EXTERNAL SRG SETUP to continue: '
if ! IFS= read -r confirmation || [ "$confirmation" != "REMOVE EXTERNAL SRG SETUP" ]; then
  printf '\nCleanup cancelled. Nothing was changed.\n'
  exit 1
fi

if [ "$WIPE_ENGAGEMENTS" -eq 1 ]; then
  printf 'Type WIPE ENGAGEMENT DATA to confirm permanent deletion: '
  if ! IFS= read -r wipe_confirmation ||
    [ "$wipe_confirmation" != "WIPE ENGAGEMENT DATA" ]; then
    printf '\nCleanup cancelled. Nothing was changed.\n'
    exit 1
  fi
fi

printf '\nCleaning up...\n'
CLEANUP_FAILURES=0

if [ -L "$LAUNCHER" ] && [ "$(readlink "$LAUNCHER")" = "$PROJECT_ROOT/srg" ]; then
  unlink "$LAUNCHER"
  printf 'Removed launcher %s\n' "$LAUNCHER"
elif [ -e "$LAUNCHER" ] || [ -L "$LAUNCHER" ]; then
  printf 'Skipped launcher not owned by this checkout: %s\n' "$LAUNCHER"
else
  printf 'Launcher already absent: %s\n' "$LAUNCHER"
fi

if [ "$KEEP_MODELS" -eq 0 ]; then
  if ! command -v ollama >/dev/null 2>&1; then
    srg_error "Ollama is not installed; configured models were not removed."
    CLEANUP_FAILURES=$((CLEANUP_FAILURES + 1))
  elif ! srg_ollama_ready; then
    srg_error "The local Ollama daemon is not reachable; configured models were not removed."
    srg_error "Start Ollama and rerun cleanup, or use --keep-models."
    CLEANUP_FAILURES=$((CLEANUP_FAILURES + 1))
  else
    previous_model=""
    for model in "$GEN_MODEL" "$EMBED_MODEL"; do
      if [ "$model" = "$previous_model" ]; then
        continue
      fi
      previous_model="$model"
      if srg_model_is_cloud "$model" || [[ "$model" = -* ]]; then
        srg_error "Refusing to remove invalid local model name: $model"
        CLEANUP_FAILURES=$((CLEANUP_FAILURES + 1))
      elif srg_model_installed "$model"; then
        if ollama rm "$model"; then
          printf 'Removed Ollama model %s\n' "$model"
        else
          srg_error "Failed to remove Ollama model $model"
          CLEANUP_FAILURES=$((CLEANUP_FAILURES + 1))
        fi
      else
        printf 'Ollama model already absent: %s\n' "$model"
      fi
    done
  fi
fi

if [ "$WIPE_ENGAGEMENTS" -eq 1 ]; then
  for engagement_path in "$PROJECT_ROOT"/engagements/*; do
    [ -e "$engagement_path" ] || [ -L "$engagement_path" ] || continue
    if [ "$(basename "$engagement_path")" != "demo" ]; then
      rm -rf -- "$engagement_path"
    fi
  done

  rm -rf -- \
    "$PROJECT_ROOT/.srg" \
    "$PROJECT_ROOT/customer_standards" \
    "$PROJECT_ROOT/private_context" \
    "$PROJECT_ROOT/engagements/demo/chroma_db" \
    "$PROJECT_ROOT/engagements/demo/responses"

  if [ -d "$PROJECT_ROOT/engagements/demo/customer_standards" ]; then
    find "$PROJECT_ROOT/engagements/demo/customer_standards" -mindepth 1 -maxdepth 1 \
      ! -name .gitkeep -exec rm -rf -- {} +
  fi
  if [ -d "$PROJECT_ROOT/engagements/demo/private_context" ]; then
    find "$PROJECT_ROOT/engagements/demo/private_context" -mindepth 1 -maxdepth 1 \
      ! -name demo-system.md -exec rm -rf -- {} +
  fi
  printf 'Deleted local engagement data; preserved the committed demo seed files.\n'
fi

cat <<EOF

PATH note:
setup.sh never edits shell profiles. If you manually added its suggested line,
remove this line from your shell profile and start a new shell:
  export PATH="$INSTALL_DIR:\$PATH"

Do not remove $INSTALL_DIR from PATH if other commands use that directory.
The cleanup script does not uninstall Ollama or stop its shared daemon.
EOF

if [ "$CLEANUP_FAILURES" -ne 0 ]; then
  srg_error "Cleanup completed with $CLEANUP_FAILURES unresolved item(s)."
  exit 1
fi

printf '\nCleanup complete.\n'
