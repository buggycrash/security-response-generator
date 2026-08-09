"""Central configuration, overridable via environment variables."""

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]

GENERATION_MODEL = os.environ.get("SRG_GEN_MODEL", "gemma4:e4b-it-qat")
EMBEDDING_MODEL = os.environ.get("SRG_EMBED_MODEL", "embeddinggemma")
GENERATION_KEEP_ALIVE = os.environ.get("SRG_GEN_KEEP_ALIVE", "20m")

# Defaults to the same duration as GENERATION_KEEP_ALIVE, since
# embeddinggemma is invoked on every generate/chat call just like the
# generation model and has its own non-trivial load time. Override
# independently with SRG_EMBED_KEEP_ALIVE if the two should diverge.
EMBED_KEEP_ALIVE = os.environ.get("SRG_EMBED_KEEP_ALIVE", GENERATION_KEEP_ALIVE)

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
CHAT_INSTRUCTIONS_PATH = PROJECT_ROOT / "prompts" / "chat_instructions.md"

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

# Bulk CSV generation processes one control per row, each with its own Chroma
# retrieval and Ollama call; a generous-but-bounded cap keeps a mistaken/huge CSV
# from silently kicking off an hours-long unattended run. 25 covers an entire
# control family except SC (System and Communications Protection, ~50 controls
# and enhancements).
MAX_BULK_CONTROLS = int(os.environ.get("SRG_MAX_BULK_CONTROLS", "25"))

# Ollama defaults to a small context window (~2048 tokens) unless told
# otherwise, which silently truncates prompts assembled from multiple
# retrieved chunks (worst case: 14 chunks x ~750 tokens + instructions +
# conversation history can exceed 10k tokens) -- Ollama keeps only a few
# tokens from the start plus the most recent tokens, which can drop the
# retrieved grounding material entirely. 16384 gives comfortable headroom
# for the default top-k settings above plus response generation.
NUM_CTX = int(os.environ.get("SRG_NUM_CTX", "16384"))

# Left unset by default so the generation model's own Modelfile default
# applies -- some models show high output variance under low temperature,
# so this stays strictly opt-in.
_gen_temperature = os.environ.get("SRG_GEN_TEMPERATURE")
GENERATION_TEMPERATURE = float(_gen_temperature) if _gen_temperature is not None else None

# Defaults to a fixed seed so generation is reproducible out of the box;
# override with SRG_GEN_SEED for varied output across runs.
GENERATION_SEED = int(os.environ.get("SRG_GEN_SEED", "42"))

# Off by default; sending raw model replies to stderr is meant for one-off
# debugging when a model produces unexpected output (blank/garbled replies,
# schema-satisfying-but-empty fields, etc.), not routine use.
DEBUG_RAW_REPLY = bool(os.environ.get("SRG_DEBUG_RAW_REPLY"))

CONTROL_ID_PATTERN = r"[A-Z]{2}-\d+(?:\(\d+\))?"

SUPPORTED_EXTENSIONS = {".md", ".txt", ".pdf"}
