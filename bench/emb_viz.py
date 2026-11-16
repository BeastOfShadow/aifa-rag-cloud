"""Mappa interattiva dello spazio degli embedding -> docs/embedding-space.html (una pagina, nessun server).

Legge i vettori per documento esportati da emb_export.py (results/emb-gpu-*/<run>/vectors.npz), li proietta
in 3D con PCA (vista "galassia", come Cosmind) e in 2D con t-SNE (vista "gruppi"), collega ogni documento
cliccato ai suoi 10 vicini più simili nello spazio originale, e accanto mette i numeri
del confronto tra embedder: tempo di s4, spazio dell'indice, qualità di s5 e quanti vicini di ogni documento
sono farmaci della stessa classe ATC.
Uso: .venv/bin/python bench/emb_viz.py
Con EMB_VECTORS=<cartella con doc_<modello>.npz> (vettori di conv/, sul corpus nuovo) e EMB_OUT=<pagina> legge quei
vettori invece di results/ e scrive la pagina indicata (tempi e qualità di s5 non ci sono).
"""
import json
import os
import sqlite3
from pathlib import Path
import numpy as np
import pandas as pd
from sklearn.decomposition import PCA
from sklearn.manifold import TSNE

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "results"
OUT = Path(os.environ.get("EMB_OUT", ROOT / "docs" / "embedding-space.html"))
SRC = os.environ.get("EMB_VECTORS")
MODELS = {"bge-m3": "bge-m3", "embeddinggemma-2": "EmbeddingGemma 2"}
CORPORA = {"n500": "Sample", "full": "Full corpus", "conv": "Full corpus (Docling)"}
ATC = {"A": "Alimentary tract and metabolism", "B": "Blood and blood forming organs",
       "C": "Cardiovascular system", "D": "Dermatologicals",
       "G": "Genito-urinary system and sex hormones", "H": "Systemic hormonal preparations",
       "J": "Antiinfectives for systemic use", "L": "Antineoplastic and immunomodulating agents",
       "M": "Musculo-skeletal system", "N": "Nervous system", "P": "Antiparasitic products",
       "R": "Respiratory system", "S": "Sensory organs", "V": "Various"}
# per i link di ogni documento: organizzazione AIFA (URL del PDF, da s1) e Markdown presenti in locale (data/md, da s3)
ORG = {(f, t): u.split("/organizzazione/")[1].split("/")[0] for f, t, u in
       sqlite3.connect(ROOT / "data" / "farmaci.sqlite").execute("SELECT cod_farmaco, tipo, url FROM docs")}
MD = {p.name for p in (ROOT / "data" / "md").glob("*.md")}


def same_class_neighbors(vec, atc, farmaco, k=10):
    """Quota dei k vicini più simili (coseno) con la stessa classe ATC di primo livello,
    escludendo l'altro documento dello stesso farmaco. A blocchi, per il corpus completo."""
    v = vec.astype(np.float32)
    g, f = np.array([a[:1] for a in atc]), np.array(farmaco)
    out = []
    for i in range(0, len(v), 2000):
        s = v[i:i + 2000] @ v.T
        s[f[i:i + 2000, None] == f[None, :]] = -2
        idx = np.argpartition(-s, k, axis=1)[:, :k]
        out.append((g[idx] == g[i:i + 2000, None]).mean(axis=1))
    return float(np.concatenate(out)[g != ""].mean())


def neighbors(vec, k=10):
    """I k documenti più simili (coseno) a ognuno, escluso sé stesso: indici e similarità, per la vista vicini."""
    v = vec.astype(np.float32)
    idx, sim = [], []
    for i in range(0, len(v), 2000):
        s = v[i:i + 2000] @ v.T
        s[np.arange(len(s)), np.arange(i, i + len(s))] = -2
        top = np.argpartition(-s, k, axis=1)[:, :k]
        order = np.argsort(-np.take_along_axis(s, top, axis=1), axis=1)
        top = np.take_along_axis(top, order, axis=1)
        idx.append(top)
        sim.append(np.take_along_axis(s, top, axis=1))
    return np.concatenate(idx), np.concatenate(sim)


def run_stats(config):
    """Tempo di s4, MB dell'indice e qualità di s5 dalle cartelle dei run di questa configurazione."""
    dirs = list((RESULTS / config).glob("*/"))
    s4 = [pd.read_csv(p).query("phase == 's4'")["seconds"] for p in RESULTS.glob(f"{config}/*/phases.csv")]
    if not s4 and "-full-bge-m3" in config:   # l'indice completo con bge-m3 è quello di split-gpu
        s4 = [pd.read_csv(p).query("phase == 's4'")["seconds"] for p in RESULTS.glob("split-gpu/*/phases.csv")]
    mb = [int(p.read_text()) for d in dirs for p in d.glob("chroma_mb.txt")]
    s5 = [pd.read_csv(p).query("rep == 1") for p in RESULTS.glob(f"{config}/*/s5.csv")]
    q = s5[0] if s5 else None
    return {"s4": int(pd.concat(s4).median()) if s4 else None, "chroma": mb[0] if mb else None,
            "found": f"{q['dense_rank'].notna().sum()}/{len(q)}" if q is not None else None,
            "top": f"{q['right_top'].sum()}/{6 * len(q)}" if q is not None else None}


corpora = {}
for path in sorted(Path(SRC).glob("doc_*.npz") if SRC else RESULTS.glob("emb-gpu-*/*/vectors.npz")):
    config = path.stem if SRC else path.parts[-3]   # con SRC nessuna cartella di run: run_stats non trova nulla
    corpus = "conv" if SRC else "full" if config.startswith("emb-gpu-full-") else "n500"
    model = config.removeprefix("doc_") if SRC else config.removeprefix("emb-gpu-full-").removeprefix("emb-gpu-")
    z = np.load(path)
    meta = {"name": z["nome"].tolist(), "tipo": z["tipo"].tolist(), "atc": z["atc"].tolist(), "cod": z["farmaco"].tolist(),
            "org": [ORG[(f, t)] for f, t in zip(z["farmaco"], z["tipo"])],
            "md": [int(f"{f}_{t}.md" in MD) for f, t in zip(z["farmaco"], z["tipo"])]}
    c = corpora.setdefault(corpus, {"label": CORPORA[corpus], "meta": meta, "farmaco": z["farmaco"].tolist(),
                                    "models": {}})
    assert c["farmaco"] == z["farmaco"].tolist() and c["meta"]["tipo"] == meta["tipo"], \
        f"{path}: documenti diversi dagli altri modelli dello stesso corpus"
    vec = z["vec"].astype(np.float32)
    pca = PCA(3, random_state=0).fit(vec)
    xyz = pca.transform(vec)
    uv = TSNE(2, init="pca", random_state=0).fit_transform(PCA(50, random_state=0).fit_transform(vec))
    print(f"{corpus} {model}: {len(vec)} documenti, PCA 3D {pca.explained_variance_ratio_.sum():.1%}")
    nb, ns = neighbors(vec)
    c["models"][model] = {
        "nb": nb.ravel().tolist(), "ns": np.round(ns.ravel().astype(float), 3).tolist(),
        "label": MODELS.get(model, model), "dim": vec.shape[1],
        "var3d": round(float(pca.explained_variance_ratio_.sum()), 3),
        "knn": round(same_class_neighbors(vec, meta["atc"], c["farmaco"]), 3), **run_stats(config),
        **{k: np.round(a.astype(float), d).tolist() for k, a, d in
           [("x", xyz[:, 0], 4), ("y", xyz[:, 1], 4), ("z", xyz[:, 2], 4), ("u", uv[:, 0], 2), ("v", uv[:, 1], 2)]}}

for c in corpora.values():
    del c["farmaco"]
data = json.dumps({"corpora": corpora, "atc": ATC}, ensure_ascii=False, separators=(",", ":"))
page = (Path(__file__).with_name("emb_viz_template.html").read_text(encoding="utf-8")
        .replace("__DATA__", data.replace("</", "<\\/")))
OUT.write_text(page, encoding="utf-8")
print(f"-> {OUT} ({OUT.stat().st_size / 1e6:.1f} MB)")
