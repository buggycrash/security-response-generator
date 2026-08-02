"""Chroma collection management: one collection per source tier."""

from collections.abc import Callable
from pathlib import Path

import chromadb
from chromadb.config import Settings

from security_response_generator.config import CHROMA_DIR
from security_response_generator.ingest.chunking import Chunk
from security_response_generator.llm.ollama_client import embed_texts


def get_client(path: Path | None = None) -> chromadb.ClientAPI:
    database_path = path or CHROMA_DIR
    database_path.mkdir(parents=True, exist_ok=True)
    return chromadb.PersistentClient(
        path=str(database_path),
        settings=Settings(anonymized_telemetry=False),
    )


def get_collection(client: chromadb.ClientAPI, name: str):
    return client.get_or_create_collection(name=name)


def chunk_id(source_path: str, chunk_index: int) -> str:
    return f"{source_path}::{chunk_index}"


def delete_source(collection, source_path: str) -> None:
    collection.delete(where={"source_path": source_path})


def upsert_chunks(
    collection,
    source_path: str,
    source_collection: str,
    chunks: list[Chunk],
    on_batch: Callable[[int], None] | None = None,
) -> None:
    if not chunks:
        return
    ids = [chunk_id(source_path, chunk.chunk_index) for chunk in chunks]
    documents = [chunk.text for chunk in chunks]
    metadatas = [
        {
            "source_path": source_path,
            "source_collection": source_collection,
            "control_ids": ",".join(chunk.control_ids),
        }
        for chunk in chunks
    ]
    embeddings = embed_texts(documents, on_batch=on_batch)
    collection.upsert(ids=ids, documents=documents, metadatas=metadatas, embeddings=embeddings)
