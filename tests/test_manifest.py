import json

from security_response_generator.ingest.manifest import (
    compute_hash,
    diff_manifest,
    load_manifest,
    save_manifest,
)


def test_diff_manifest_detects_new_changed_and_deleted():
    previous = {"a.md": "hash-a", "b.md": "hash-b", "c.md": "hash-c"}
    current = {"a.md": "hash-a", "b.md": "hash-b-changed", "d.md": "hash-d"}

    changed_or_new, unchanged, deleted = diff_manifest(previous, current)

    assert sorted(changed_or_new) == ["b.md", "d.md"]
    assert unchanged == ["a.md"]
    assert deleted == ["c.md"]


def test_diff_manifest_empty_previous_treats_everything_as_new():
    current = {"a.md": "hash-a"}
    changed_or_new, unchanged, deleted = diff_manifest({}, current)

    assert changed_or_new == ["a.md"]
    assert unchanged == []
    assert deleted == []


def test_load_manifest_missing_file_returns_empty_dict(tmp_path):
    assert load_manifest(tmp_path / "manifest.json") == {}


def test_save_and_load_manifest_round_trip(tmp_path):
    manifest_path = tmp_path / "chroma_db" / "manifest.json"
    save_manifest(manifest_path, {"knowledge_base/a.md": "hash-a"})

    assert json.loads(manifest_path.read_text()) == {"knowledge_base/a.md": "hash-a"}
    assert load_manifest(manifest_path) == {"knowledge_base/a.md": "hash-a"}


def test_compute_hash_is_stable_and_content_sensitive(tmp_path):
    file_path = tmp_path / "doc.txt"
    file_path.write_text("hello")
    first_hash = compute_hash(file_path)

    assert compute_hash(file_path) == first_hash

    file_path.write_text("hello world")
    assert compute_hash(file_path) != first_hash
