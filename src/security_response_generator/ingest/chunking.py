"""Split loaded document text into overlapping, structure-aware chunks."""

import re
from dataclasses import dataclass, field

from security_response_generator.config import (
    CHUNK_OVERLAP_CHARS,
    CHUNK_SIZE_CHARS,
    CONTROL_ID_PATTERN,
)

_CONTROL_ID_RE = re.compile(CONTROL_ID_PATTERN)
_PARAGRAPH_SPLIT_RE = re.compile(r"\n\s*\n")

# NIST control/enhancement heading lines, e.g. "SI-5 SECURITY ALERTS, ADVISORIES,
# AND DIRECTIVES" or "(1) AUTOMATED ALERTS AND ADVISORIES". PDF text extraction
# often doesn't leave a blank line between adjacent controls, so paragraph
# splitting alone can silently pack two unrelated controls into one chunk (and
# truncate the second one mid-sentence at the chunk_size cutoff). Splitting on
# these headings first, before paragraph packing, keeps each control/enhancement
# in its own section.
_SECTION_HEADING_RE = re.compile(
    r"(?=^(?:[A-Z]{2}-\d+\s+[A-Z][A-Z0-9,'/&()\- ]*[A-Z0-9)]"
    r"|\(\d+\)\s+[A-Z][A-Z0-9,'/&()|\- ]*[A-Z0-9)])\s*$)",
    re.MULTILINE,
)


@dataclass
class Chunk:
    text: str
    chunk_index: int
    control_ids: list[str] = field(default_factory=list)


def chunk_text(
    text: str, chunk_size: int = CHUNK_SIZE_CHARS, overlap: int = CHUNK_OVERLAP_CHARS
) -> list[Chunk]:
    """Chunk text on control/enhancement heading and paragraph boundaries where
    possible, falling back to a sliding window only for paragraphs that
    individually exceed chunk_size.
    """
    raw_chunks: list[str] = []
    for section in _SECTION_HEADING_RE.split(text):
        raw_chunks.extend(_pack_paragraphs(section, chunk_size, overlap))

    return [
        Chunk(
            text=chunk_value,
            chunk_index=index,
            control_ids=sorted(set(_CONTROL_ID_RE.findall(chunk_value))),
        )
        for index, chunk_value in enumerate(raw_chunks)
    ]


def _pack_paragraphs(text: str, chunk_size: int, overlap: int) -> list[str]:
    paragraphs = [p.strip() for p in _PARAGRAPH_SPLIT_RE.split(text) if p.strip()]
    if not paragraphs:
        return []

    raw_chunks: list[str] = []
    current = ""

    for paragraph in paragraphs:
        candidate = f"{current}\n\n{paragraph}" if current else paragraph

        if len(candidate) <= chunk_size:
            current = candidate
            continue

        if current:
            raw_chunks.append(current)
            current = _tail(current, overlap)

        if len(paragraph) <= chunk_size:
            current = f"{current}\n\n{paragraph}" if current else paragraph
        else:
            for window in _sliding_window(paragraph, chunk_size, overlap):
                raw_chunks.append(window)
            current = ""

    if current:
        raw_chunks.append(current)

    return raw_chunks


def _tail(text: str, overlap: int) -> str:
    if overlap <= 0 or len(text) <= overlap:
        return text
    return text[-overlap:]


def _sliding_window(text: str, size: int, overlap: int):
    step = max(size - overlap, 1)
    for start in range(0, len(text), step):
        window = text[start : start + size]
        if window:
            yield window
        if start + size >= len(text):
            break
