"""Thin wrapper around the local Ollama client for embeddings and chat."""

import sys
from collections.abc import Callable, Mapping

import ollama

from security_response_generator.config import (
    DEBUG_RAW_REPLY,
    EMBED_BATCH_SIZE,
    EMBED_KEEP_ALIVE,
    EMBEDDING_MODEL,
    GENERATION_KEEP_ALIVE,
    GENERATION_MODEL,
    GENERATION_SEED,
    GENERATION_TEMPERATURE,
    NUM_CTX,
    REVIEW_KEEP_ALIVE,
    REVIEW_MODEL,
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
    texts: list[str],
    on_batch: Callable[[int], None] | None = None,
    *,
    on_response: Callable[[Mapping], None] | None = None,
) -> list[list[float]]:
    """Embed texts in EMBED_BATCH_SIZE-sized batches.

    If on_batch is given, it's called with the number of texts just embedded
    after each batch completes, so callers can drive a progress indicator
    without needing to know about batching themselves.

    If on_response is given, it's called with each batch's raw Ollama
    response (which carries server-side timing fields like load_duration
    alongside the embeddings) -- used by diagnostic tooling, not by normal
    callers.
    """
    if not texts:
        return []
    _require_local_model(EMBEDDING_MODEL)
    client = _local_client()
    embeddings: list[list[float]] = []
    for start in range(0, len(texts), EMBED_BATCH_SIZE):
        batch = texts[start : start + EMBED_BATCH_SIZE]
        response = client.embed(model=EMBEDDING_MODEL, input=batch, keep_alive=EMBED_KEEP_ALIVE)
        if on_response is not None:
            on_response(response)
        embeddings.extend(response["embeddings"])
        if on_batch is not None:
            on_batch(len(batch))
    return embeddings


def embed_query(text: str, *, on_response: Callable[[Mapping], None] | None = None) -> list[float]:
    return embed_texts([text], on_response=on_response)[0]


def chat_messages(
    messages: list[dict],
    response_format: dict | None = None,
    *,
    on_response: Callable[[Mapping], None] | None = None,
) -> str:
    _require_local_model(GENERATION_MODEL)
    options: dict = {"num_ctx": NUM_CTX, "seed": GENERATION_SEED}
    if GENERATION_TEMPERATURE is not None:
        options["temperature"] = GENERATION_TEMPERATURE
    response = _local_client().chat(
        model=GENERATION_MODEL,
        messages=messages,
        options=options,
        format=response_format,
        keep_alive=GENERATION_KEEP_ALIVE,
    )
    if on_response is not None:
        on_response(response)
    content = response["message"]["content"]
    if DEBUG_RAW_REPLY:
        print(f"[SRG debug] raw model reply:\n{content}\n", file=sys.stderr)
    return content


def review_messages(
    messages: list[dict],
    response_format: dict | None = None,
    *,
    on_response: Callable[[Mapping], None] | None = None,
) -> str:
    """Send a review request to the separately configured local reviewer model."""
    _require_local_model(REVIEW_MODEL)
    options: dict = {"num_ctx": NUM_CTX, "seed": GENERATION_SEED}
    if GENERATION_TEMPERATURE is not None:
        options["temperature"] = GENERATION_TEMPERATURE
    response = _local_client().chat(
        model=REVIEW_MODEL,
        messages=messages,
        options=options,
        format=response_format,
        keep_alive=REVIEW_KEEP_ALIVE,
    )
    if on_response is not None:
        on_response(response)
    content = response["message"]["content"]
    if DEBUG_RAW_REPLY:
        print(f"[SRG debug] raw reviewer reply:\n{content}\n", file=sys.stderr)
    return content
