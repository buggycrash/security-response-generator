from security_response_generator.ingest import store
from security_response_generator.ingest.chunking import Chunk


def test_get_client_disables_chroma_telemetry(monkeypatch, tmp_path):
    captured = {}

    def fake_persistent_client(*, path, settings):
        captured["path"] = path
        captured["settings"] = settings
        return object()

    monkeypatch.setattr(store.chromadb, "PersistentClient", fake_persistent_client)

    store.get_client(tmp_path / "chroma")

    assert captured["path"] == str(tmp_path / "chroma")
    assert captured["settings"].anonymized_telemetry is False


class _FakeCollection:
    def __init__(self):
        self.upserted = None

    def upsert(self, ids, documents, metadatas, embeddings):
        self.upserted = {
            "ids": ids,
            "documents": documents,
            "metadatas": metadatas,
            "embeddings": embeddings,
        }


def test_upsert_chunks_forwards_on_batch_to_embed_texts(monkeypatch):
    captured = {}

    def fake_embed_texts(documents, on_batch=None):
        captured["documents"] = documents
        captured["on_batch"] = on_batch
        if on_batch is not None:
            on_batch(len(documents))
        return [[0.0] for _ in documents]

    monkeypatch.setattr(store, "embed_texts", fake_embed_texts)

    collection = _FakeCollection()
    chunks = [Chunk(text="a", chunk_index=0), Chunk(text="b", chunk_index=1)]
    batch_sizes = []

    store.upsert_chunks(collection, "doc.md", "knowledge_base", chunks, on_batch=batch_sizes.append)

    assert captured["documents"] == ["a", "b"]
    assert batch_sizes == [2]
    assert collection.upserted["ids"] == ["doc.md::0", "doc.md::1"]


def test_upsert_chunks_on_batch_not_required(monkeypatch):
    monkeypatch.setattr(
        store, "embed_texts", lambda documents, on_batch=None: [[0.0] for _ in documents]
    )

    collection = _FakeCollection()
    chunks = [Chunk(text="a", chunk_index=0)]

    store.upsert_chunks(collection, "doc.md", "knowledge_base", chunks)

    assert collection.upserted["documents"] == ["a"]


def test_upsert_chunks_empty_chunks_is_a_no_op(monkeypatch):
    def fake_embed_texts(*args, **kwargs):
        raise AssertionError("should not be called for empty chunks")

    monkeypatch.setattr(store, "embed_texts", fake_embed_texts)

    collection = _FakeCollection()
    store.upsert_chunks(collection, "doc.md", "knowledge_base", [])

    assert collection.upserted is None
