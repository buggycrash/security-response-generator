"""Load raw text out of source documents, attaching provenance metadata."""

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from pypdf import PdfReader

from security_response_generator.config import SUPPORTED_EXTENSIONS

_EXCLUDED_FILENAMES = {"README.md", ".gitkeep"}

# NIST 800-53's Appendix C is a multi-page control-crosswalk table (control
# number/name/enhancement name/implemented-by/assurance columns), not
# narrative control text. Every control ID appears as a row in it, which
# pollutes both exact-substring and semantic retrieval with zero grounding
# value, so these pages are dropped before chunking rather than ingested.
_CONTROL_TABLE_MARKERS = ("APPENDIX C", "CONTROL ENHANCEMENT NAME", "ASSURANCE")


@dataclass
class LoadedDocument:
    source_path: str
    text: str


def iter_source_files(source_dir: Path) -> Iterator[Path]:
    if not source_dir.exists():
        return
    for path in sorted(source_dir.rglob("*")):
        if not path.is_file():
            continue
        if path.name in _EXCLUDED_FILENAMES:
            continue
        if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            continue
        yield path


def load_document(path: Path, root: Path) -> LoadedDocument:
    relative_path = str(path.relative_to(root))
    suffix = path.suffix.lower()
    if suffix in {".md", ".txt"}:
        text = path.read_text(encoding="utf-8")
    elif suffix == ".pdf":
        text = _load_pdf(path)
    else:
        raise ValueError(f"Unsupported file type: {path}")
    return LoadedDocument(source_path=relative_path, text=text)


def _load_pdf(path: Path) -> str:
    reader = PdfReader(str(path))
    pages = []
    for page_number, page in enumerate(reader.pages, start=1):
        page_text = page.extract_text() or ""
        if _is_control_crosswalk_table(page_text):
            continue
        pages.append(f"\n\n[page {page_number}]\n{page_text}")
    return "".join(pages)


def _is_control_crosswalk_table(page_text: str) -> bool:
    return all(marker in page_text for marker in _CONTROL_TABLE_MARKERS)
