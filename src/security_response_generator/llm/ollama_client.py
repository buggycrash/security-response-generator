"""Thin wrapper around the local Ollama client for embeddings and chat."""

from collections.abc import Callable

import ollama

from security_response_generator.config import (
    EMBED_BATCH_SIZE,
    EMBEDDING_MODEL,
    GENERATION_KEEP_ALIVE,
    GENERATION_MODEL,
    NUM_CTX,
)

LOCAL_OLLAMA_HOST = "http://127.0.0.1:11434"


def _local_client() -> ollama.Client:
    """Return an Ollama client that cannot be redirected by OLLAMA_HOST."""
    return ollama.Client(host=LOCAL_OLLAMA_HOST, trust_env=False)


def _require_local_model(model: str) -> None:
    """Reject Ollama cloud model tags before any prompt or document is sent."""
    tag = model.lower().rsplit(":", maxsplit=1)[-1]
    if tag == "cloud" or tag.endswith("-cloud"):
        raise ValueError(
            f"SRG refuses Ollama cloud model '{model}'; configure a locally installed model."
        )


def embed_texts(
    texts: list[str], on_batch: Callable[[int], None] | None = None
) -> list[list[float]]:
    """Embed texts in EMBED_BATCH_SIZE-sized batches.

    If on_batch is given, it's called with the number of texts just embedded
    after each batch completes, so callers can drive a progress indicator
    without needing to know about batching themselves.
    """
    if not texts:
        return []
    _require_local_model(EMBEDDING_MODEL)
    client = _local_client()
    embeddings: list[list[float]] = []
    for start in range(0, len(texts), EMBED_BATCH_SIZE):
        batch = texts[start : start + EMBED_BATCH_SIZE]
        response = client.embed(model=EMBEDDING_MODEL, input=batch)
        embeddings.extend(response["embeddings"])
        if on_batch is not None:
            on_batch(len(batch))
    return embeddings


def embed_query(text: str) -> list[float]:
    return embed_texts([text])[0]


def chat_messages(messages: list[dict], response_format: dict | None = None) -> str:
    _require_local_model(GENERATION_MODEL)
    response = _local_client().chat(
        model=GENERATION_MODEL,
        messages=messages,
        options={"num_ctx": NUM_CTX},
        format=response_format,
        keep_alive=GENERATION_KEEP_ALIVE,
    )
    return response["message"]["content"]
