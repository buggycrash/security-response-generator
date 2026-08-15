from security_response_generator import config
from security_response_generator.generation import retrieval
from security_response_generator.generation.retrieval import (
    RetrievalResult,
    RetrievedChunk,
    merge_results,
    to_chunks,
)


def _chunk(chunk_id: str, text: str = "text", path: str = "doc.md") -> RetrievedChunk:
    return RetrievedChunk(text=text, source_path=path, chunk_id=chunk_id)


class _FakeCollection:
    """Returns one canned result for the unranked exact-substring get() lookup and
    another for the embedding-ranked semantic query(), so tests can tell them apart."""

    def __init__(self, exact_match_result: dict, semantic_result: dict):
        self._exact_match_result = exact_match_result
        self._semantic_result = semantic_result

    def get(self, where_document=None):
        return self._exact_match_result

    def query(self, query_embeddings, n_results, where_document=None):
        return self._semantic_result


def _raw_result(chunk_ids: list[str]) -> dict:
    return {
        "ids": [chunk_ids],
        "documents": [[f"text {chunk_id}" for chunk_id in chunk_ids]],
        "metadatas": [[{"source_path": "doc.md"} for _ in chunk_ids]],
    }


def _flat_result(chunk_ids: list[str], texts: dict[str, str] | None = None) -> dict:
    """A Chroma `collection.get(...)`-shaped result (flat, no query-batching nesting)."""
    texts = texts or {}
    return {
        "ids": chunk_ids,
        "documents": [texts.get(chunk_id, f"text {chunk_id}") for chunk_id in chunk_ids],
        "metadatas": [{"source_path": "doc.md"} for _ in chunk_ids],
    }


def test_merge_results_dedups_and_prioritizes_primary():
    primary = [_chunk("a"), _chunk("b")]
    secondary = [_chunk("b"), _chunk("c"), _chunk("d")]

    merged = merge_results(primary, secondary, top_k=3)

    assert [c.chunk_id for c in merged] == ["a", "b", "c"]


def test_merge_results_respects_top_k_cap():
    primary = [_chunk(str(i)) for i in range(10)]
    merged = merge_results(primary, [], top_k=4)

    assert len(merged) == 4


def test_merge_results_handles_empty_inputs():
    assert merge_results([], [], top_k=5) == []


def test_to_chunks_converts_raw_chroma_query_shape():
    raw_result = {
        "ids": [["doc.md::0", "doc.md::1"]],
        "documents": [["first chunk", "second chunk"]],
        "metadatas": [[{"source_path": "doc.md"}, {"source_path": "doc.md"}]],
    }

    chunks = to_chunks(raw_result)

    assert len(chunks) == 2
    assert chunks[0].chunk_id == "doc.md::0"
    assert chunks[0].text == "first chunk"
    assert chunks[0].source_path == "doc.md"


def test_to_chunks_handles_empty_result():
    raw_result = {"ids": [[]], "documents": [[]], "metadatas": [[]]}
    assert to_chunks(raw_result) == []


def test_query_collection_reports_exact_match_when_metadata_pass_hits():
    collection = _FakeCollection(
        exact_match_result=_flat_result(
            ["a"], texts={"a": "AC-2 ACCOUNT MANAGEMENT\nControl: a. Define account types."}
        ),
        semantic_result=_raw_result(["a", "b"]),
    )

    chunks, exact_match = retrieval._query_collection(collection, "AC-2", [0.0], top_k=5)

    assert exact_match is True
    assert [c.chunk_id for c in chunks] == ["a", "b"]


def test_query_collection_reports_no_exact_match_for_semantic_only_hits():
    # A fabricated control ID (e.g. "IL-27") still pulls back the semantically-nearest
    # chunks -- Chroma's vector query has no similarity threshold -- but the
    # exact-substring pass correctly finds nothing, so exact_match must be False.
    collection = _FakeCollection(
        exact_match_result=_flat_result([]),
        semantic_result=_raw_result(["x", "y"]),
    )

    chunks, exact_match = retrieval._query_collection(collection, "IL-27", [0.0], top_k=5)

    assert exact_match is False
    assert [c.chunk_id for c in chunks] == ["x", "y"]


def test_is_heading_match_true_for_base_control_heading():
    text = "AC-2 ACCOUNT MANAGEMENT\nControl: a. Define and document account types."
    assert retrieval._is_heading_match(text, "AC-2") is True


def test_is_heading_match_false_for_passing_reference():
    text = "Related Controls: AC-2, AC-3, RA-5."
    assert retrieval._is_heading_match(text, "AC-2") is False


def test_is_heading_match_true_for_enhancement_shorthand_heading():
    # NIST enhancement headings show only the parenthesized number, not the parent ID.
    text = "(1) AUTOMATED ALERTS AND ADVISORIES\nBroadcast security alert information."
    assert retrieval._is_heading_match(text, "SI-5(1)") is True


def test_is_heading_match_false_for_wrong_enhancement_number():
    text = "(2) SOME OTHER ENHANCEMENT\nDiscussion text."
    assert retrieval._is_heading_match(text, "SI-5(1)") is False


def test_exact_match_chunks_prefers_heading_over_passing_mention():
    # Regression test: a control's own heading chunk must win over a chunk that only
    # references it inside another control's "Related Controls:" list, even though
    # both contain the literal substring.
    collection = _FakeCollection(
        exact_match_result=_flat_result(
            ["mention", "heading"],
            texts={
                "mention": "Related Controls: PT-3, PT-5, RA-5.",
                "heading": "PT-3 PERSONALLY IDENTIFIABLE INFORMATION PROCESSING PURPOSES\n"
                "Control: a. Identify and document the purpose(s).",
            },
        ),
        semantic_result=_raw_result([]),
    )

    chunks = retrieval._exact_match_chunks(collection, "PT-3", top_k=5)

    assert [c.chunk_id for c in chunks] == ["heading"]


def test_exact_match_chunks_falls_back_to_passing_mentions_if_no_heading_found():
    collection = _FakeCollection(
        exact_match_result=_flat_result(
            ["mention"], texts={"mention": "Related Controls: PT-3, PT-5, RA-5."}
        ),
        semantic_result=_raw_result([]),
    )

    chunks = retrieval._exact_match_chunks(collection, "PT-3", top_k=5)

    assert [c.chunk_id for c in chunks] == ["mention"]


def test_retrieval_result_refusal_flag_reflects_exact_match_not_chunk_presence():
    no_baseline = RetrievalResult(
        customer_chunks=[],
        baseline_chunks=[],
        private_chunks=[],
        baseline_exact_match=False,
        customer_exact_match=False,
    )
    assert no_baseline.has_baseline_match is False

    with_baseline = RetrievalResult(
        customer_chunks=[],
        baseline_chunks=[_chunk("a")],
        private_chunks=[],
        baseline_exact_match=True,
        customer_exact_match=False,
    )
    assert with_baseline.has_baseline_match is True


def test_retrieval_result_refusal_flag_ignores_semantic_only_baseline_chunks():
    # A fabricated control ID (e.g. "IL-27") can still pull back semantically-nearest
    # chunks with no similarity threshold -- has_baseline_match must not be fooled by
    # baseline_chunks being non-empty when no exact match was actually found.
    semantic_only = RetrievalResult(
        customer_chunks=[],
        baseline_chunks=[_chunk("a")],
        private_chunks=[],
        baseline_exact_match=False,
        customer_exact_match=False,
    )
    assert semantic_only.has_baseline_match is False


def test_retrieval_result_customer_caveat_flag():
    no_customer = RetrievalResult(
        customer_chunks=[],
        baseline_chunks=[_chunk("a")],
        private_chunks=[],
        baseline_exact_match=True,
        customer_exact_match=False,
    )
    assert no_customer.has_customer_match is False

    with_customer = RetrievalResult(
        customer_chunks=[_chunk("c")],
        baseline_chunks=[_chunk("a")],
        private_chunks=[],
        baseline_exact_match=True,
        customer_exact_match=True,
    )
    assert with_customer.has_customer_match is True


def test_retrieval_result_customer_caveat_flag_ignores_semantic_only_customer_chunks():
    # Semantic-only customer chunks (e.g. SC-13 content returned for a query about
    # SC-8(1), the only semantically-nearby content in a collection with no SC-8(1)
    # entry) must not be treated as a genuine customer/state standard match --
    # has_customer_match must not be fooled by customer_chunks being non-empty when
    # no exact match was actually found.
    semantic_only = RetrievalResult(
        customer_chunks=[_chunk("a")],
        baseline_chunks=[],
        private_chunks=[],
        baseline_exact_match=True,
        customer_exact_match=False,
    )
    assert semantic_only.has_customer_match is False


def test_semantic_search_returns_chunks_from_query():
    collection = _FakeCollection(
        exact_match_result=_flat_result([]), semantic_result=_raw_result(["a", "b"])
    )

    chunks = retrieval.semantic_search(collection, [0.0], top_k=5)

    assert [c.chunk_id for c in chunks] == ["a", "b"]


def test_semantic_search_returns_empty_on_query_failure():
    class _RaisingCollection:
        def query(self, **kwargs):
            raise RuntimeError("collection unavailable")

    assert retrieval.semantic_search(_RaisingCollection(), [0.0], top_k=5) == []


def test_retrieve_for_chat_queries_all_three_collections_with_shared_embedding(monkeypatch):
    monkeypatch.setattr(retrieval, "embed_query", lambda text: [0.0])
    collections = {
        config.COLLECTION_CUSTOMER_STANDARDS: _FakeCollection(
            _flat_result([]), _raw_result(["c1"])
        ),
        config.COLLECTION_KNOWLEDGE_BASE: _FakeCollection(_flat_result([]), _raw_result(["b1"])),
        config.COLLECTION_PRIVATE_CONTEXT: _FakeCollection(_flat_result([]), _raw_result(["p1"])),
    }

    result = retrieval.retrieve_for_chat("what is the password policy?", collections)

    assert [c.chunk_id for c in result.customer_chunks] == ["c1"]
    assert [c.chunk_id for c in result.baseline_chunks] == ["b1"]
    assert [c.chunk_id for c in result.private_chunks] == ["p1"]


def test_retrieve_for_chat_embeds_the_terminology_expanded_query(monkeypatch):
    captured: dict = {}

    def _fake_embed_query(text):
        captured["text"] = text
        return [0.0]

    monkeypatch.setattr(retrieval, "embed_query", _fake_embed_query)
    collections = {
        config.COLLECTION_CUSTOMER_STANDARDS: _FakeCollection(_flat_result([]), _raw_result([])),
        config.COLLECTION_KNOWLEDGE_BASE: _FakeCollection(_flat_result([]), _raw_result([])),
        config.COLLECTION_PRIVATE_CONTEXT: _FakeCollection(_flat_result([]), _raw_result([])),
    }

    retrieval.retrieve_for_chat("what is the password length requirement?", collections)

    assert captured["text"] == "what is the password length requirement? authenticator"


def test_retrieve_for_control_embeds_the_terminology_expanded_query(monkeypatch):
    captured: dict = {}

    def _fake_embed_query(text, *, on_response=None):
        captured["text"] = text
        return [0.0]

    monkeypatch.setattr(retrieval, "embed_query", _fake_embed_query)
    collections = {
        config.COLLECTION_CUSTOMER_STANDARDS: _FakeCollection(_flat_result([]), _raw_result([])),
        config.COLLECTION_KNOWLEDGE_BASE: _FakeCollection(_flat_result([]), _raw_result([])),
        config.COLLECTION_PRIVATE_CONTEXT: _FakeCollection(_flat_result([]), _raw_result([])),
    }

    retrieval.retrieve_for_control("IA-5", "password policy notes", collections)

    assert captured["text"] == "IA-5 password policy notes authenticator"


def test_chat_retrieval_result_has_any_match():
    empty = retrieval.ChatRetrievalResult(customer_chunks=[], baseline_chunks=[], private_chunks=[])
    assert empty.has_any_match is False

    with_one_match = retrieval.ChatRetrievalResult(
        customer_chunks=[], baseline_chunks=[_chunk("a")], private_chunks=[]
    )
    assert with_one_match.has_any_match is True
