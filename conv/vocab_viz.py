"""Pagina del vocabolario: conv/docs/vocabulary.html, una pagina sola (dati dentro, nessun server, nessuna libreria).
Parole, coppie e terne di parole di conv/vocab.py, per tipo (RCP, FI) e sezione QRD: le più usate come barre, in ordine
crescente o decrescente, filtro delle parole vuote, ricerca, e quante parole hanno poche o tante occorrenze.
Uso: .venv/bin/python conv/vocab_viz.py [cartella vocab, default conv/out/vocab]"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

CONV = Path(__file__).resolve().parent
SRC = Path(sys.argv[1]) if len(sys.argv) > 1 else CONV / "out" / "vocab"
TOP = 300                                         # termini tenuti per tipo, sezione e lunghezza
BINS = [5, 10, 20, 50, 100, 200, 500, 1000, 2000, 5000, 10000, 10**9]

t = pd.read_csv(SRC / "terms.csv", dtype={"section": str}, keep_default_na=False)
t["tf"] = pd.to_numeric(t.tf, errors="coerce")
sec = pd.read_csv(SRC / "sections.csv", dtype={"section": str}, keep_default_na=False)
docs = {tipo: int(g.doc.nunique()) for tipo, g in sec.groupby("tipo")}
words = sec.groupby(["tipo", "section"]).words.sum()
ndocs = sec.groupby(["tipo", "section"]).doc.nunique()


def top(g, n):
    g = g.sort_values(["df", "term"], ascending=[False, True]).head(TOP)
    return [[a, int(b), None if pd.isna(c) else int(c)] for a, b, c in zip(g.term, g.df, g.tf)]


def hist(g):
    return np.histogram(g.df, bins=BINS)[0].tolist()


out = {"docs": docs, "bins": BINS[:-1], "data": {}, "sections": {}}
for tipo, gt in t.groupby("tipo"):
    out["data"][tipo] = {}
    ids = sorted((s for s in gt.section.unique() if s not in ("", "pre")),
                 key=lambda s: [int(x) for x in s.split(".")])
    out["sections"][tipo] = {s: [int(ndocs[tipo, s]), int(words[tipo, s])] for s in ["pre", *ids] if (tipo, s) in ndocs}
    for s in ["all", *[s for s in out["sections"][tipo]]]:
        g = gt if s == "all" else gt[gt.section == s]
        if s == "all":     # somma sulle sezioni: parole = occorrenze vere; coppie e terne = presenze in sezioni
            g = g.groupby(["n", "term"], as_index=False).agg(df=("df", "sum"), tf=("tf", "sum"))
            g.loc[g.n > 1, "tf"] = np.nan
        out["data"][tipo][s] = {str(n): {"top": top(gn, n), "hist": hist(gn), "distinct": int(len(gn))}
                                for n, gn in g.groupby("n")}
html = (CONV / "docs" / "vocabulary.template.html").read_text(encoding="utf-8").replace(
    "/*DATA*/null", json.dumps(out, ensure_ascii=False, separators=(",", ":")))
(CONV / "docs" / "vocabulary.html").write_text(html, encoding="utf-8")
print(f"{len(html) / 1e6:.1f} MB -> conv/docs/vocabulary.html")
