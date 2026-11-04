"""Campione di prova per confrontare i converter: 40 documenti scelti per gruppi, dai Markdown di s3 attuali.

Ogni gruppo copre un caso che la conversione attuale sbaglia o che un converter può sbagliare: documenti
standard (template QRD), formati vecchi, tabelle lunghe spezzate tra le pagine, documenti molto lunghi,
radiofarmaci (RCP con le sezioni 11 e 12). Un farmaco compare una volta sola, scelta riproducibile (seme fisso).
Uso: .venv/bin/python conv/gold.py   ->   conv/gold.csv, e i 40 PDF in conv/data/pdf se s2 non li ha ancora
"""
import csv
import random
import re
import sqlite3
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MD = ROOT / "data" / "md"
PDF = Path(__file__).with_name("data") / "pdf"
OUT = Path(__file__).with_name("gold.csv")
sys.path.insert(0, str(ROOT / "rag"))
from config import USER_AGENT   # noqa: E402

RCP_SUB = ["4.1", "4.2", "4.3", "4.4", "4.5", "4.6", "4.7", "4.8", "4.9",
           "5.1", "5.2", "5.3", "6.1", "6.2", "6.3", "6.4", "6.5", "6.6"]
FI_QRD = [r"1\.?\s*\**\s*Cos['’]\s*[eè]", r"2\.?\s*\**\s*Cosa deve sapere",
          r"3\.?\s*\**\s*Come (prendere|usare|assumere|utilizzare|somministrare|viene)",
          r"4\.?\s*\**\s*Possibili effetti indesiderati", r"5\.?\s*\**\s*Come conservare",
          r"6\.?\s*\**\s*Contenuto della confezione"]


def features(path):
    t = path.read_text(encoding="utf-8", errors="replace")
    tipo = "FI" if path.stem.endswith("_FI") else "RCP"
    rows = [l for l in t.splitlines() if l.startswith("|")]
    if tipo == "RCP":
        std = sum(bool(re.search(r"(?<![\d.])" + re.escape(s) + r"\.?\s", t)) for s in RCP_SUB) / len(RCP_SUB)
    else:
        std = sum(bool(re.search(p, t, re.I)) for p in FI_QRD) / len(FI_QRD)
    return {"cod": path.stem.split("_")[0], "tipo": tipo, "chars": len(t), "table_rows": len(rows),
            # filigrana o piè di pagina AIFA dentro una riga di tabella: tabella spezzata tra due pagine
            "broken_table": sum("Documento reso disponibile" in l for l in rows),
            "std": round(std, 2),
            "radio": tipo == "RCP" and bool(re.search(r"\b11\.?\s*\**\s*DOSIMETRIA", t, re.I))}


docs = [features(p) for p in sorted(MD.glob("*.md"))]
long_rcp = sorted(d["chars"] for d in docs if d["tipo"] == "RCP")[int(0.95 * sum(d["tipo"] == "RCP" for d in docs))]
long_fi = sorted(d["chars"] for d in docs if d["tipo"] == "FI")[int(0.95 * sum(d["tipo"] == "FI" for d in docs))]
GROUPS = [   # nome, quanti, condizione
    ("RCP standard", 6, lambda d: d["tipo"] == "RCP" and d["std"] == 1 and d["broken_table"] == 0 and d["chars"] < long_rcp),
    ("FI standard", 6, lambda d: d["tipo"] == "FI" and d["std"] == 1 and d["chars"] < long_fi),
    ("RCP formato vecchio", 4, lambda d: d["tipo"] == "RCP" and d["std"] < 0.45),
    ("FI formato vecchio", 4, lambda d: d["tipo"] == "FI" and d["std"] <= 0.34),
    ("RCP tabella spezzata", 6, lambda d: d["tipo"] == "RCP" and d["broken_table"] >= 2),
    ("FI con tabelle", 3, lambda d: d["tipo"] == "FI" and d["table_rows"] >= 10),
    ("RCP molto lungo", 3, lambda d: d["tipo"] == "RCP" and d["chars"] >= long_rcp),
    ("FI molto lungo", 2, lambda d: d["tipo"] == "FI" and d["chars"] >= long_fi),
    ("RCP radiofarmaco", 2, lambda d: d["radio"]),
    ("RCP qualsiasi", 2, lambda d: d["tipo"] == "RCP"),
    ("FI qualsiasi", 2, lambda d: d["tipo"] == "FI"),
]
rng = random.Random(0)
used, picked = set(), []
for name, k, cond in GROUPS:
    pool = [d for d in docs if cond(d) and d["cod"] not in used]
    for d in rng.sample(pool, min(k, len(pool))):
        used.add(d["cod"])
        picked.append({"group": name, "pool": len(pool), **d})

with open(OUT, "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=picked[0].keys())
    w.writeheader()
    w.writerows(picked)
for name, *_ in GROUPS:
    g = [p for p in picked if p["group"] == name]
    print(f"{name:22s} {len(g):2d} su {g[0]['pool'] if g else 0:5d} candidati")
print(f"{len(picked)} documenti -> {OUT}")

# i PDF del campione subito, senza aspettare s2 sul corpus intero: stesso URL e stesso nome file di s2
urls = {(f, t): u for f, t, u in sqlite3.connect(ROOT / "data" / "farmaci.sqlite").execute(
    "SELECT cod_farmaco, tipo, url FROM docs")}
PDF.mkdir(parents=True, exist_ok=True)
for p in picked:
    path = PDF / f"{p['cod']}_{p['tipo']}.pdf"
    if not path.exists():
        req = urllib.request.Request(urls[(p["cod"], p["tipo"])], headers={"User-Agent": USER_AGENT})
        body = urllib.request.urlopen(req, timeout=60).read()
        assert body[:4] == b"%PDF", f"{path.name}: non è un PDF"
        path.write_bytes(body)
print(f"PDF del campione in {PDF}: {sum((PDF / f'{p['cod']}_{p['tipo']}.pdf').exists() for p in picked)}/{len(picked)}")
