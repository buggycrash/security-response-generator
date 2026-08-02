"""Track per-file content hashes to support incremental ingestion."""

import hashlib
import json
from pathlib import Path


def compute_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_manifest(manifest_path: Path) -> dict[str, str]:
    if not manifest_path.exists():
        return {}
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def save_manifest(manifest_path: Path, manifest: dict[str, str]) -> None:
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")


def diff_manifest(
    previous: dict[str, str], current_files: dict[str, str]
) -> tuple[list[str], list[str], list[str]]:
    """Compare a previous hash-per-path snapshot to the current one.

    Returns (changed_or_new, unchanged, deleted) relative paths.
    """
    changed_or_new = [
        path for path, digest in current_files.items() if previous.get(path) != digest
    ]
    unchanged = [path for path, digest in current_files.items() if previous.get(path) == digest]
    deleted = [path for path in previous if path not in current_files]
    return changed_or_new, unchanged, deleted
