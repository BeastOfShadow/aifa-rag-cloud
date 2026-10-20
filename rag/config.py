"""Config centrale del progetto RAG farmaci.

Tutto locale: dati, PDF, md, embeddings, vector DB.
Solo la GENERAZIONE chiama un LLM via API, dietro l'interfaccia LLMProvider (swappable).
"""
from __future__ import annotations
import os
from pathlib import Path

# ---- percorsi ----
ROOT = Path(__file__).resolve().parent.parent          # farmaci/
DATASETS = ROOT / "datasets"
DATA = Path(os.environ.get("DATA_DIR", ROOT / "data"))  # override per pilot/bench: non tocca data/
PDF_DIR = DATA / "pdf"
MD_DIR = DATA / "md"
DB_PATH = DATA / "farmaci.sqlite"
CHROMA_DIR = DATA / "chroma"
CHROMA_COLLECTION = "stampati"

for d in (DATA, PDF_DIR, MD_DIR):
    d.mkdir(parents=True, exist_ok=True)

# ---- pilot ----
PILOT_N = int(os.environ.get("PILOT_N", "100"))   # quanti farmaci (0 = tutti, full run)

# ---- download ----
# PDF = solo sorgente per il md. Default: cancella PDF dopo conversione (tieni solo md).
# KEEP_PDF=1 per conservarli.
KEEP_PDF = os.environ.get("KEEP_PDF", "0") == "1"
HTTP_TIMEOUT = 60

# ---- OCR (solo per PDF scansionati, rari) ----
OCR_MIN_CHARS_PER_PAGE = 100   # sotto = probabile scansione -> OCR
OCR_LANG = "ita"
HTTP_CONCURRENCY = 6         # gentile con AIFA
HTTP_RETRIES = 3
USER_AGENT = "farmaci-rag-research/0.1 (local; contact 20051767@studenti.uniupo.it)"

# ---- embeddings (LOCALE) ----
EMBED_MODEL = "BAAI/bge-m3"  # multilingua, long context, buono su italiano/medico
EMBED_DIM = 1024
EMBED_BATCH = int(os.environ.get("EMBED_BATCH", "64"))  # H100: EMBED_BATCH=256


def pick_device() -> str:
    """GPU se c'e': mps (Apple Silicon) > cuda > cpu. Override con env DEVICE."""
    forced = os.environ.get("DEVICE")
    if forced:
        return forced
    import torch
    if torch.backends.mps.is_available():
        return "mps"
    if torch.cuda.is_available():
        return "cuda"
    return "cpu"

# ---- chunking ----
CHUNK_MAX_CHARS = 1800       # per sezione; sezioni piu' lunghe vengono spezzate
CHUNK_OVERLAP = 150

# ---- retrieval 2-stadi (max precisione) ----
RERANK_MODEL = "BAAI/bge-reranker-v2-m3"  # cross-encoder, coppia di bge-m3
RETRIEVE_N = 30              # candidati dense (recall largo)
TOP_K = 6                   # tenuti dopo rerank (precisione)

# ---- generazione LLM (API swappable) ----
# scegli il provider via env: LLM_PROVIDER=claude|ollama
LLM_PROVIDER = os.environ.get("LLM_PROVIDER", "claude")
LLM_MODEL = os.environ.get("LLM_MODEL", "claude-opus-4-8")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5:7b")
OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
