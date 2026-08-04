"""Retrieve relevant chunks per source tier for a given control ID + query."""

import re
from dataclasses import dataclass

from security_response_generator import config
from security_response_generator.llm.ollama_client import embed_query

# Matches a NIST control/enhancement ID split into its base control and,
# optionally, an enhancement number -- e.g. "AC-2(1)" -> base="AC-2", enh="1".
_CONTROL_ID_SPLIT_RE = re.compile(r"^([A-Z]{2}-\d+)(?:\((\d+)\))?$")


@dataclass
class RetrievedChunk:
    text: str
    source_path: str
    chunk_id: str


@dataclass
class RetrievalResult:
    customer_chunks: list[RetrievedChunk]
    baseline_chunks: list[RetrievedChunk]
    private_chunks: list[RetrievedChunk]
    baseline_exact_match: bool

    @property
    def has_baseline_match(self) -> bool:
        return self.baseline_exact_match

    @property
    def has_customer_match(self) -> bool:
        return bool(self.customer_chunks)


def to_chunks(query_result: dict) -> list[RetrievedChunk]:
    """Convert a raw Chroma `collection.query(...)` result (single-query shape) to chunks."""
    ids = query_result.get("ids", [[]])[0]
    documents = query_result.get("documents", [[]])[0]
    metadatas = query_result.get("metadatas", [[]])[0]
    return _to_chunks(ids, documents, metadatas)


def _chunks_from_get_result(get_result: dict) -> list[RetrievedChunk]:
    """Convert a raw Chroma `collection.get(...)` result (flat shape, no batching) to chunks."""
    return _to_chunks(
        get_result.get("ids", []),
        get_result.get("documents", []),
        get_result.get("metadatas", []),
    )


def _to_chunks(ids, documents, metadatas) -> list[RetrievedChunk]:
    return [
        RetrievedChunk(
            text=text,
            source_path=(metadata or {}).get("source_path", ""),
            chunk_id=chunk_id,
        )
        for chunk_id, text, metadata in zip(ids, documents, metadatas)
    ]


def _is_heading_match(chunk_text: str, control_id: str) -> bool:
    """Whether chunk_text opens with control_id's own heading line, as opposed to
    merely mentioning it in passing (e.g. in another control's "Related Controls:"
    list). NIST enhancement headings only show the parenthesized number, not the
    parent control ID (e.g. "(1) AUTOMATED ALERTS AND ADVISORIES" for "SI-5(1)").
    """
    split = _CONTROL_ID_SPLIT_RE.match(control_id)
    if not split:
        return False
    base, enhancement = split.group(1), split.group(2)
    anchor = rf"\({enhancement}\)" if enhancement else re.escape(base)
    return re.match(rf"^{anchor}\s+[A-Z]", chunk_text.lstrip()) is not None


def merge_results(
    primary: list[RetrievedChunk], secondary: list[RetrievedChunk], top_k: int
) -> list[RetrievedChunk]:
    """Merge two ranked chunk lists, primary first, deduped by chunk_id, capped at top_k."""
    seen: set[str] = set()
    merged: list[RetrievedChunk] = []
    for chunk in (*primary, *secondary):
        if chunk.chunk_id in seen:
            continue
        seen.add(chunk.chunk_id)
        merged.append(chunk)
        if len(merged) >= top_k:
            break
    return merged


def _query_collection(
    collection, control_id: str, query_embedding, top_k: int
) -> tuple[list[RetrievedChunk], bool]:
    """Query one collection, returning the merged chunks plus whether the control ID's
    own text was actually found.

    The exact-match pass deliberately does *not* rank by embedding similarity: under a
    noisy or adversarial query, the control's own heading chunk can be outranked by
    superficially "closer" unrelated chunks (verified in practice -- a near-empty page
    fragment ranked above a control's own definition for a query with irrelevant
    context appended). An unranked substring lookup, preferring chunks that open with
    the control's own heading over chunks that merely reference it in passing, is used
    instead so the real match is never silently dropped for ranking reasons.

    The semantic pass, in contrast, has no similarity threshold -- Chroma always
    returns its `top_k` nearest vectors, so it's non-empty for virtually any query
    regardless of relevance. Callers that need to know whether the control ID
    genuinely exists (not just that *some* semantically-nearby chunk exists) should
    use the second return value, not the presence of merged chunks.
    """
    exact_chunks = _exact_match_chunks(collection, control_id, top_k)
    semantic_pass = _safe_query(collection, query_embedding, top_k)
    merged = merge_results(exact_chunks, semantic_pass, top_k)
    return merged, bool(exact_chunks)


def _exact_match_chunks(collection, control_id: str, top_k: int) -> list[RetrievedChunk]:
    try:
        result = collection.get(where_document={"$contains": control_id})
    except Exception:
        return []
    candidates = _chunks_from_get_result(result)
    heading_matches = [c for c in candidates if _is_heading_match(c.text, control_id)]
    return (heading_matches or candidates)[:top_k]


def _safe_query(
    collection, query_embedding, top_k: int, where_document=None
) -> list[RetrievedChunk]:
    kwargs = {"query_embeddings": [query_embedding], "n_results": top_k}
    if where_document:
        kwargs["where_document"] = where_document
    try:
        result = collection.query(**kwargs)
    except Exception:
        return []
    return to_chunks(result)


def semantic_search(collection, query_embedding, top_k: int) -> list[RetrievedChunk]:
    """Semantic-only nearest-neighbor lookup, for callers with no control ID to
    anchor an exact-match pass (e.g. freeform chat questions)."""
    return _safe_query(collection, query_embedding, top_k)


@dataclass
class ChatRetrievalResult:
    customer_chunks: list[RetrievedChunk]
    baseline_chunks: list[RetrievedChunk]
    private_chunks: list[RetrievedChunk]

    @property
    def has_any_match(self) -> bool:
        return bool(self.customer_chunks or self.baseline_chunks or self.private_chunks)


def retrieve_for_chat(question: str, collections: dict) -> ChatRetrievalResult:
    query_embedding = embed_query(question)
    return ChatRetrievalResult(
        customer_chunks=semantic_search(
            collections[config.COLLECTION_CUSTOMER_STANDARDS],
            query_embedding,
            config.TOP_K_CUSTOMER_STANDARDS,
        ),
        baseline_chunks=semantic_search(
            collections[config.COLLECTION_KNOWLEDGE_BASE],
            query_embedding,
            config.TOP_K_KNOWLEDGE_BASE,
        ),
        private_chunks=semantic_search(
            collections[config.COLLECTION_PRIVATE_CONTEXT],
            query_embedding,
            config.TOP_K_PRIVATE_CONTEXT,
        ),
    )


def retrieve_for_control(control_id: str, context_notes: str, collections: dict) -> RetrievalResult:
    query_text = f"{control_id} {context_notes}".strip()
    query_embedding = embed_query(query_text)

    customer_chunks, _ = _query_collection(
        collections[config.COLLECTION_CUSTOMER_STANDARDS],
        control_id,
        query_embedding,
        config.TOP_K_CUSTOMER_STANDARDS,
    )
    baseline_chunks, baseline_exact_match = _query_collection(
        collections[config.COLLECTION_KNOWLEDGE_BASE],
        control_id,
        query_embedding,
        config.TOP_K_KNOWLEDGE_BASE,
    )
    private_chunks, _ = _query_collection(
        collections[config.COLLECTION_PRIVATE_CONTEXT],
        control_id,
        query_embedding,
        config.TOP_K_PRIVATE_CONTEXT,
    )

    return RetrievalResult(
        customer_chunks=customer_chunks,
        baseline_chunks=baseline_chunks,
        private_chunks=private_chunks,
        baseline_exact_match=baseline_exact_match,
    )
