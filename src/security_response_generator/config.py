"""Central configuration, overridable via environment variables."""

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]

GENERATION_MODEL = os.environ.get("SRG_GEN_MODEL", "llama3.1:8b")
EMBEDDING_MODEL = os.environ.get("SRG_EMBED_MODEL", "embeddinggemma")

# A large source document (e.g. the full NIST 800-53 catalog) can
# chunk into hundreds of pieces. Sending them all to Ollama in a single
# embedding request can OOM-kill the model's runner subprocess, so
# embed_texts() batches calls at this size instead of sending everything
# at once.
EMBED_BATCH_SIZE = int(os.environ.get("SRG_EMBED_BATCH_SIZE", "32"))

CHROMA_DIR = Path(os.environ.get("SRG_CHROMA_DIR", str(PROJECT_ROOT / "chroma_db")))
MANIFEST_PATH = CHROMA_DIR / "manifest.json"

KNOWLEDGE_BASE_DIR = PROJECT_ROOT / "knowledge_base"
NIST_CATALOG_PATH = KNOWLEDGE_BASE_DIR / "NIST.SP.800-53-oscal.md"
ENGAGEMENTS_DIR = PROJECT_ROOT / "engagements"
ACTIVE_ENGAGEMENT_PATH = PROJECT_ROOT / ".srg" / "active-engagement"

INSTRUCTIONS_PATH = PROJECT_ROOT / "prompts" / "instructions.md"

COLLECTION_KNOWLEDGE_BASE = "knowledge_base"
COLLECTION_CUSTOMER_STANDARDS = "customer_standards"
COLLECTION_PRIVATE_CONTEXT = "private_context"

SOURCE_NAMES = (
    COLLECTION_KNOWLEDGE_BASE,
    COLLECTION_CUSTOMER_STANDARDS,
    COLLECTION_PRIVATE_CONTEXT,
)

CHUNK_SIZE_CHARS = int(os.environ.get("SRG_CHUNK_SIZE_CHARS", "3000"))
CHUNK_OVERLAP_CHARS = int(os.environ.get("SRG_CHUNK_OVERLAP_CHARS", "500"))

TOP_K_KNOWLEDGE_BASE = int(os.environ.get("SRG_KB_TOPK", "6"))
TOP_K_CUSTOMER_STANDARDS = int(os.environ.get("SRG_CUSTOMER_TOPK", "4"))
TOP_K_PRIVATE_CONTEXT = int(os.environ.get("SRG_PRIVATE_TOPK", "4"))

MAX_FOLLOWUP_TURNS = int(os.environ.get("SRG_MAX_FOLLOWUP_TURNS", "2"))

# Ollama defaults to a small context window (~2048 tokens) unless told
# otherwise, which silently truncates prompts assembled from multiple
# retrieved chunks (worst case: 14 chunks x ~750 tokens + instructions +
# conversation history can exceed 10k tokens) -- Ollama keeps only a few
# tokens from the start plus the most recent tokens, which can drop the
# retrieved grounding material entirely. 16384 gives comfortable headroom
# for the default top-k settings above plus response generation.
NUM_CTX = int(os.environ.get("SRG_NUM_CTX", "16384"))

CONTROL_ID_PATTERN = r"[A-Z]{2}-\d+(?:\(\d+\))?"

SUPPORTED_EXTENSIONS = {".md", ".txt", ".pdf"}
