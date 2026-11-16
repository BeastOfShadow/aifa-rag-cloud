"""Un vettore per documento da un indice Chroma di s4: media dei vettori dei frammenti di ogni FI/RCP,
normalizzata. Serve a emb_viz.py per disegnare lo spazio degli embedding.

Uso: .venv/bin/python bench/emb_export.py <run>/data <out.npz>
"""
import sys
import chromadb
import numpy as np

coll = chromadb.PersistentClient(path=f"{sys.argv[1]}/chroma").get_collection("stampati")
sums, meta = {}, {}
for off in range(0, coll.count(), 5000):
    b = coll.get(include=["embeddings", "metadatas"], limit=5000, offset=off)
    for e, m in zip(b["embeddings"], b["metadatas"]):
        k = (str(m["cod_farmaco"]), m["tipo"])
        sums[k] = sums.get(k, 0) + np.asarray(e, dtype=np.float32)
        meta[k] = (m.get("denominazione", ""), m.get("codice_atc", ""))

keys = sorted(sums)
vec = np.stack([sums[k] for k in keys])
vec /= np.linalg.norm(vec, axis=1, keepdims=True)
np.savez_compressed(sys.argv[2], vec=vec.astype(np.float16),
                    farmaco=[k[0] for k in keys], tipo=[k[1] for k in keys],
                    nome=[meta[k][0] for k in keys], atc=[meta[k][1] for k in keys])
print(f"{len(keys)} documenti, {vec.shape[1]} dimensioni -> {sys.argv[2]}")
