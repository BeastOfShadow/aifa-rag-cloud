"""Esporta un indice Chroma di s4 in forma compatta: per ogni chunk id, vettore (float16), testo e metadati. Da qui
l'indice si ricostruisce senza GPU (coll.add con gli stessi vettori). Uso: python conv/export_vectors.py <dir chroma> <out.npz>"""
import gzip
import json
import sys

import chromadb
import numpy as np

coll = chromadb.PersistentClient(path=sys.argv[1]).get_collection("stampati")
ids, vecs, docs, metas = [], [], [], []
for off in range(0, coll.count(), 5000):
    b = coll.get(include=["embeddings", "documents", "metadatas"], limit=5000, offset=off)
    ids += b["ids"]; vecs.append(np.asarray(b["embeddings"], dtype=np.float16)); docs += b["documents"]; metas += b["metadatas"]
np.savez(sys.argv[2], ids=np.array(ids), vec=np.concatenate(vecs))
with gzip.open(sys.argv[2].replace(".npz", ".jsonl.gz"), "wt", encoding="utf-8") as f:
    for i, d, m in zip(ids, docs, metas):
        f.write(json.dumps({"id": i, "document": d, "metadata": m}, ensure_ascii=False) + "\n")
print(f"{len(ids)} chunk, {vecs[0].shape[1]} dimensioni -> {sys.argv[2]}")
