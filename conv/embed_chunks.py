"""Ricalcola i vettori di un indice di s4 con un altro modello partendo dall'esportazione compatta (export_vectors.py):
stessi chunk, stessi id e stesso testo con contesto di s4 (denominazione — tipo — sezione: testo). Calcola tutto su
tutte le GPU (in bf16, chunk ordinati per lunghezza), poi scrive npz compatto e indice Chroma.
Uso: python conv/embed_chunks.py <chunks.jsonl.gz> <modello> <out.npz> [<dir chroma>]
"""
import gzip
import json
import sys

import numpy as np
import torch
from sentence_transformers import SentenceTransformer

FULL = {"FI": "Foglio Illustrativo", "RCP": "Riassunto Caratteristiche Prodotto"}

if __name__ == "__main__":   # i processi del pool reimportano il modulo: niente lavoro fuori da qui
    src, name, out = sys.argv[1:4]
    rows = [json.loads(l) for l in gzip.open(src, "rt", encoding="utf-8")]
    texts = [f"{r['metadata'].get('denominazione', '')} — {FULL[r['metadata']['tipo']]} — {r['metadata']['section']}: "
             f"{r['document']}" for r in rows]
    print(f"{len(rows)} chunk, modello {name}", flush=True)
    model = SentenceTransformer(name, model_kwargs={"torch_dtype": torch.bfloat16})
    devs = [f"cuda:{i}" for i in range(torch.cuda.device_count())]
    order = np.argsort([len(t) for t in texts])          # lotti di lunghezza simile: meno padding
    pool = model.start_multi_process_pool(devs)
    emb = model.encode_document([texts[i] for i in order], pool=pool, batch_size=128, chunk_size=4000,
                                normalize_embeddings=True, show_progress_bar=True)
    model.stop_multi_process_pool(pool)
    vec = np.empty_like(emb)
    vec[order] = emb
    np.savez(out, ids=np.array([r["id"] for r in rows]), vec=vec.astype(np.float16))
    print(f"-> {out} {vec.shape}", flush=True)
    if len(sys.argv) > 4:
        import chromadb
        coll = chromadb.PersistentClient(path=sys.argv[4]).create_collection("stampati", metadata={"hnsw:space": "cosine"})
        for i in range(0, len(rows), 5000):
            b = rows[i:i + 5000]
            coll.add(ids=[r["id"] for r in b], documents=[r["document"] for r in b],
                     metadatas=[r["metadata"] for r in b], embeddings=vec[i:i + 5000].astype(np.float32))
        print(f"Chroma pronto: {sys.argv[4]} count={coll.count()}", flush=True)
