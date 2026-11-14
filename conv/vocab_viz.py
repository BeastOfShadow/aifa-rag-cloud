"""Pagina del vocabolario: conv/docs/vocabulary.html + i dati completi in conv/out/vocab_web/ (un file per tipo, sezione e
lunghezza, caricato dalla pagina solo quando serve). Parole, coppie e terne di conv/vocab.py, per tipo (RCP, FI, tutti)
e sezione QRD, con tutto il vocabolario in ordine crescente o decrescente, ricerca e distribuzione.
La pagina apre i dati con percorsi relativi (../out/vocab_web/): si apre come file, ma la cartella deve stare dov'è.
Uso: .venv/bin/python conv/vocab_viz.py [cartella vocab, default conv/out/vocab]"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

CONV = Path(__file__).resolve().parent
SRC = Path(sys.argv[1]) if len(sys.argv) > 1 else CONV / "out" / "vocab"
WEB = CONV / "out" / "vocab_web"
BINS = [1, 2, 3, 5, 10, 20, 50, 100, 200, 500, 1000, 2000, 5000, 10000, 10**9]
WEB.mkdir(parents=True, exist_ok=True)
for f in WEB.glob("*.js"):
    f.unlink()

t = pd.read_csv(SRC / "terms.csv", dtype={"section": str}, keep_default_na=False)
t["tf"] = pd.to_numeric(t.tf, errors="coerce")
glob = pd.read_csv(SRC / "terms_global.csv", keep_default_na=False)       # parole sull'intero documento, da 2 documenti
sec = pd.read_csv(SRC / "sections.csv", dtype={"section": str}, keep_default_na=False)
docs = {tipo: int(g.doc.nunique()) for tipo, g in sec.groupby("tipo")}
ndocs = sec.groupby(["tipo", "section"]).doc.nunique()
words = sec.groupby(["tipo", "section"]).words.sum()


def write(tipo, s, n, g):
    """un file per vista: tutti i termini in ordine decrescente di documenti (poi alfabetico)"""
    g = g.sort_values(["df", "term"], ascending=[False, True])
    rows = [[a, int(b), None if pd.isna(c) else int(c)] for a, b, c in zip(g.term, g.df, g.tf)]
    body = {"distinct": len(rows), "hist": np.histogram(g.df, bins=BINS)[0].tolist(), "rows": rows}
    (WEB / f"{tipo}_{s}_{n}.js").write_text(
        f'(window.VD=window.VD||{{}})["{tipo}|{s}|{n}"]={json.dumps(body, ensure_ascii=False, separators=(",", ":"))};', encoding="utf-8")


index = {"docs": {"ALL": sum(docs.values()), **docs}, "bins": BINS[:-1], "sections": {"ALL": {}},
         "totwords": {"ALL": int(words.sum())}, "tipi": ["ALL", *sorted(docs)]}
for tipo, gt in t.groupby("tipo"):
    ids = sorted((s for s in gt.section.unique() if s not in ("", "pre")), key=lambda s: [int(x) for x in s.split(".")])
    index["sections"][tipo] = {s: [int(ndocs[tipo, s]), int(words[tipo, s])] for s in ["pre", *ids] if (tipo, s) in ndocs}
    for s, gs in gt.groupby("section"):
        if s == "":
            continue
        for n, g in gs.groupby("n"):
            write(tipo, s, n, g)
    for n, g in gt.groupby("n"):         # tutte le sezioni: coppie e terne sommate sulle sezioni; parole sull'intero documento
        if n > 1:
            g = g.groupby("term", as_index=False).agg(df=("df", "sum"), tf=("tf", "sum"))
            g["tf"] = np.nan
            write(tipo, "all", n, g)
    write(tipo, "all", 1, glob[glob.tipo == tipo][["term", "df", "tf"]])
for n, g in t[t.n > 1].groupby("n"):
    g = g.groupby("term", as_index=False).agg(df=("df", "sum")); g["tf"] = np.nan
    write("ALL", "all", n, g)
write("ALL", "all", 1, glob.groupby("term", as_index=False)[["df", "tf"]].sum())
html = (CONV / "docs" / "vocabulary.template.html").read_text(encoding="utf-8").replace(
    "/*DATA*/null", json.dumps(index, ensure_ascii=False, separators=(",", ":")))
(CONV / "docs" / "vocabulary.html").write_text(html, encoding="utf-8")
size = sum(f.stat().st_size for f in WEB.glob("*.js")) / 1e6
print(f"conv/docs/vocabulary.html {len(html) / 1e3:.0f} kB + {len(list(WEB.glob('*.js')))} file in conv/out/vocab_web/ ({size:.0f} MB)")
