"""s5 - Query: retrieval 2-stadi + generazione LLM (provider swappable).

Retrieval (max precisione):
  - filtro strutturato opzionale (metadati Chroma: codice_atc, ragione_sociale, tipo, cod_farmaco)
  - stadio 1: dense bge-m3 -> top RETRIEVE_N candidati (recall largo)
  - stadio 2: cross-encoder bge-reranker-v2-m3 -> riordina, tiene TOP_K (precisione)
Generazione:
  - provider astratto: claude (API) | ollama (locale). Scelto da config.LLM_PROVIDER.

I DATI restano locali. Solo il testo dei chunk recuperati + la domanda vanno all'LLM di generazione.
"""
from __future__ import annotations
import os
import sqlite3
import chromadb
from sentence_transformers import SentenceTransformer, CrossEncoder
from config import (DB_PATH, CHROMA_DIR, CHROMA_COLLECTION, EMBED_MODEL,
                    RERANK_MODEL, RETRIEVE_N, TOP_K,
                    LLM_PROVIDER, LLM_MODEL, OLLAMA_MODEL, OLLAMA_HOST, pick_device)

_model = None
_reranker = None
_coll = None


def _lazy():
    global _model, _reranker, _coll
    if _model is None:
        dev = pick_device()
        _model = SentenceTransformer(EMBED_MODEL, device=dev)
        _reranker = CrossEncoder(RERANK_MODEL, device=dev)
        client = chromadb.PersistentClient(path=str(CHROMA_DIR))
        _coll = client.get_collection(CHROMA_COLLECTION)
    return _model, _reranker, _coll


def retrieve(question: str, k: int = TOP_K, n: int = RETRIEVE_N,
             where: dict | None = None):
    """2-stadi: dense top-n -> rerank -> top-k. where es: {'tipo': 'RCP'}."""
    model, reranker, coll = _lazy()
    qemb = model.encode([question], normalize_embeddings=True)[0].tolist()
    res = coll.query(query_embeddings=[qemb], n_results=n, where=where)
    cands = [{"text": d, "meta": m, "dense": round(1 - dist, 3)}
             for d, m, dist in zip(res["documents"][0], res["metadatas"][0],
                                    res["distances"][0])]
    if not cands:
        return []
    # stadio 2: cross-encoder riordina
    scores = reranker.predict([(question, c["text"]) for c in cands])
    for c, s in zip(cands, scores):
        c["score"] = round(float(s), 3)
    cands.sort(key=lambda c: c["score"], reverse=True)
    return cands[:k]


# ---------- provider LLM (swappable) ----------
def _gen_claude(prompt: str) -> str:
    import anthropic
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    msg = client.messages.create(
        model=LLM_MODEL, max_tokens=1024,
        messages=[{"role": "user", "content": prompt}])
    return msg.content[0].text


def _gen_ollama(prompt: str) -> str:
    import httpx
    r = httpx.post(f"{OLLAMA_HOST}/api/generate",
                   json={"model": OLLAMA_MODEL, "prompt": prompt, "stream": False},
                   timeout=180)
    return r.json()["response"]


def generate(prompt: str) -> str:
    return {"claude": _gen_claude, "ollama": _gen_ollama}[LLM_PROVIDER](prompt)


# ---------- SQL diretto (per domande strutturate, no RAG) ----------
def sql(query: str):
    con = sqlite3.connect(DB_PATH)
    try:
        return con.execute(query).fetchall()
    finally:
        con.close()


PROMPT = """Sei un assistente che risponde su foglietti illustrativi (FI) e riassunti
caratteristiche prodotto (RCP) di farmaci italiani. Rispondi SOLO con le informazioni nei
brani sotto. Se l'informazione non c'e', dillo. Cita sempre il farmaco (denominazione + tipo).

DOMANDA: {q}

BRANI:
{ctx}

RISPOSTA:"""


def answer(question: str, k: int = TOP_K, where: dict | None = None) -> str:
    hits = retrieve(question, k=k, where=where)
    ctx = "\n\n".join(
        f"[{h['meta'].get('denominazione','?')} | {h['meta']['tipo']} | "
        f"{h['meta'].get('section','')[:60]} | rerank={h['score']}]\n{h['text']}"
        for h in hits)
    return generate(PROMPT.format(q=question, ctx=ctx))


if __name__ == "__main__":
    import sys
    q = sys.argv[1] if len(sys.argv) > 1 else "Per cosa si usa la senna? Controindicazioni?"
    print("Q:", q, "\n")
    for h in retrieve(q):
        print(f"- rerank={h['score']:>7} dense={h['dense']:>6} | "
              f"{h['meta'].get('denominazione','?')} | {h['meta']['tipo']} | "
              f"{h['meta'].get('section','')[:45]}")
