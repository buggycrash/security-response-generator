from security_response_generator import config
from security_response_generator.generation import retrieval
from security_response_generator.generation.retrieval import RetrievalTiming


def _raw_result(chunk_ids: list[str]) -> dict:
    return {
        "ids": [chunk_ids],
        "documents": [[f"text {chunk_id}" for chunk_id in chunk_ids]],
        "metadatas": [[{"source_path": "doc.md"} for _ in chunk_ids]],
    }


def _flat_result(chunk_ids: list[str]) -> dict:
    return {
        "ids": chunk_ids,
        "documents": [f"text {chunk_id}" for chunk_id in chunk_ids],
        "metadatas": [{"source_path": "doc.md"} for _ in chunk_ids],
    }


class _FakeCollection:
    def get(self, where_document=None):
        return _flat_result([])

    def query(self, query_embeddings, n_results, where_document=None):
        return _raw_result([])


def _collections() -> dict:
    return {
        config.COLLECTION_CUSTOMER_STANDARDS: _FakeCollection(),
        config.COLLECTION_KNOWLEDGE_BASE: _FakeCollection(),
        config.COLLECTION_PRIVATE_CONTEXT: _FakeCollection(),
    }


def test_retrieve_for_control_reports_timing_when_on_timing_given(monkeypatch):
    monkeypatch.setattr(retrieval, "embed_query", lambda text, *, on_response=None: [0.0])

    timings: list[RetrievalTiming] = []
    retrieval.retrieve_for_control("AC-2", "", _collections(), on_timing=timings.append)

    assert len(timings) == 1
    timing = timings[0]
    assert timing.embedding_seconds >= 0.0
    assert timing.chroma_seconds >= 0.0
    assert set(timing.chroma_seconds_by_collection) == {
        config.COLLECTION_CUSTOMER_STANDARDS,
        config.COLLECTION_KNOWLEDGE_BASE,
        config.COLLECTION_PRIVATE_CONTEXT,
    }
    assert all(seconds >= 0.0 for seconds in timing.chroma_seconds_by_collection.values())
    assert timing.chroma_seconds == sum(timing.chroma_seconds_by_collection.values())


def test_retrieve_for_control_on_timing_not_required(monkeypatch):
    monkeypatch.setattr(retrieval, "embed_query", lambda text, *, on_response=None: [0.0])

    # Should not raise when on_timing is omitted.
    result = retrieval.retrieve_for_control("AC-2", "", _collections())
    assert result.baseline_chunks == []


def test_retrieve_for_control_forwards_on_embed_response(monkeypatch):
    def fake_embed_query(text, *, on_response=None):
        if on_response is not None:
            on_response({"load_duration": 99})
        return [0.0]

    monkeypatch.setattr(retrieval, "embed_query", fake_embed_query)

    captured = {}
    retrieval.retrieve_for_control(
        "AC-2", "", _collections(), on_embed_response=lambda response: captured.update(response)
    )

    assert captured["load_duration"] == 99
