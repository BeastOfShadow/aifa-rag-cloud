"""Punteggi del confronto tra converter sul campione di prova (conv/gold.csv), senza un riferimento scritto a mano:
ogni Markdown è confrontato con il testo nativo del suo PDF, pulito dal timbro AIFA con conv/clean.py (il testo
originale del produttore; se manca la versione pulita, il PDF scaricato).

- fedeltà del testo: parole del PDF ritrovate nel Markdown (recall: se è bassa il converter perde testo) e parole del
  Markdown che nel PDF ci sono (precision: se è bassa il converter inventa o storpia). Filigrana e testo legale AIFA
  sono esclusi da entrambi con la stessa regola;
- sezioni: titoli standard (RCP 1–10 e 4.1–6.6, FI 1–6) presenti nel testo del PDF che nel Markdown sono un titolo,
  cioè all'inizio di una riga come intestazione o grassetto (s3 attuale li lascia in mezzo al testo);
- tabelle (Markdown o HTML): quante, quante righe hanno un numero di celle diverso dall'intestazione, quante
  contengono la filigrana (tabella spezzata tra due pagine);
- filigrana AIFA rimasta nel Markdown.
Uso: .venv/bin/python conv/score.py   legge conv/out/<converter>/ e conv/out/times.csv, scrive conv/results/bakeoff_*.csv
"""
import csv
import html
import re
from collections import Counter
from pathlib import Path

import pandas as pd
import pymupdf

ROOT = Path(__file__).resolve().parent.parent
CONV = Path(__file__).resolve().parent
OUT, RES = CONV / "out", CONV / "results"

RCP_IDS = [str(i) for i in range(1, 11)] + [f"{a}.{b}" for a, n in ((4, 9), (5, 3), (6, 6)) for b in range(1, n + 1)]
FI_HEADS = [r"1\.?\s*Cos['’]\s*[eè]", r"2\.?\s*Cosa deve sapere",
            r"3\.?\s*Come (prendere|usare|assumere|utilizzare|somministrare|viene)",
            r"4\.?\s*Possibili effetti indesiderati", r"5\.?\s*Come conservare", r"6\.?\s*Contenuto della confezione"]
WM = "documento reso disponibile"
# filigrana e piè di pagina legale che AIFA aggiunge a ogni pagina, tolti dal PDF e dal Markdown prima del confronto
# (le regole di s4 sono tarate sul Markdown di s3 e sul testo del PDF lasciano pezzi del piè di pagina)
BOILER = re.compile(r"documento reso disponibile da aifa(\s*il\s*\d{1,2}/\d{1,2}/\d{2,4})?"
                    r"|esula dalla competenza dell['’]aifa.{0,700}?titolare (aic\)|dell['’]autorizzazione"
                    r"( all['’]immissione in commercio)?( \(o titolare aic\))?)\.?", re.I)


def strip_boilerplate(text):
    return BOILER.sub(" ", re.sub(r"\s+", " ", text))


def words(text):
    return Counter(w for w in re.findall(r"[^\W_]+", text.lower()) if len(w) > 1 or w.isdigit())


def md_plain(md):
    md = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", md)              # immagini
    md = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", md)            # link: tengo il testo
    # commenti e tag html (tabelle, <br>, <sup>), non i < del testo: "< 200 cellule" fino al > dopo sono pagine intere
    md = html.unescape(re.sub(r"<!--.*?-->|</?[a-zA-Z][^<>]*>", " ", md, flags=re.S))
    return re.sub(r"[|*#_`>~]+|-{3,}", " ", md)


assert md_plain("<td>CD4 < 200 cellule</td><!-- image -->| &lt;1/10 | > 3").split() == ["CD4", "<", "200", "cellule",
                                                                                        "<1/10", "3"]


def section_patterns(tipo):
    if tipo == "RCP":   # numero seguito da un titolo che inizia maiuscolo, non "4.1" dentro una frase
        return {i: re.escape(i) + r"\.?\s+[A-ZÀ-Ù]" for i in RCP_IDS}
    return {h.split("\\")[0]: h for h in FI_HEADS}


def sections(pdf_text, md, tipo):
    expected = found = 0
    for pat in section_patterns(tipo).values():
        if re.search(r"^\s*" + pat, pdf_text, re.M | (re.I if tipo == "FI" else 0)):
            expected += 1
            # titolo nel Markdown: a inizio riga, come intestazione (#), in grassetto o entrambi (## **4.1 …)
            head = r"^\s*(#{1,6}\s*(\*\*\s*)?|\*\*\s*)"
            found += bool(re.search(head + r"(\d+\.?\s*)?\**\s*" + pat.split(r"\.?", 1)[-1]
                                    if tipo == "FI" else head + pat, md, re.M | re.I))
    return expected, found


def tables(md):
    """[(celle per riga, testo)] per ogni tabella Markdown (righe consecutive con |) o HTML (<table>)."""
    out, block = [], []
    for line in md.splitlines() + [""]:
        if line.lstrip().startswith("|"):
            block.append(line)
        elif block:
            rows = [r for r in block if not re.fullmatch(r"\s*\|?[\s:|-]+\|?\s*", r)]   # senza la riga ---
            out.append(([len(r.strip().strip("|").split("|")) for r in rows], "\n".join(block)))
            block = []
    for t in re.findall(r"<table.*?</table>", md, re.S | re.I):
        cells, carry = [], []   # carry: [righe ancora coperte, colonne] delle celle con rowspan delle righe sopra
        for r in re.findall(r"<tr.*?</tr>", t, re.S | re.I):
            tags = re.findall(r"<t[dh]\b[^>]*>", r, re.I)
            span = [[int((re.search(rf"{a}=\"?(\d+)", c, re.I) or [0, 1])[1]) for a in ("rowspan", "colspan")]
                    for c in tags]
            cells.append(sum(c for _, c in carry) + sum(c for _, c in span))
            carry = [[n - 1, c] for n, c in carry + span if n > 1]
        out.append((cells, t))
    return out


assert tables("<table><tr><td rowspan=2>a</td><td colspan=\"2\">b</td></tr><tr><td>c</td><td>d</td></tr></table>")[0][0] \
    == [3, 3]


gold = list(csv.DictReader(open(CONV / "gold.csv")))
pdf = {}
for g in gold:
    name = f"{g['cod']}_{g['tipo']}.pdf"
    clean = CONV / "data" / "pdf_gold_clean" / name
    d = pymupdf.open(clean if clean.exists() else CONV / "data" / "pdf" / name)
    text = "\n".join(p.get_text() for p in d)
    text = re.sub(r"(\w)-\n(\w)", r"\1\2", text)                 # a capo con trattino
    pdf[g["cod"], g["tipo"]] = (len(d), text, words(strip_boilerplate(text)))

rows = []
for conv in sorted(p.name for p in OUT.iterdir() if p.is_dir()):
    for g in gold:
        stem = f"{g['cod']}_{g['tipo']}"
        found = list((OUT / conv).rglob(f"{stem}.md"))
        pages, text, ref = pdf[g["cod"], g["tipo"]]
        r = {"converter": conv, "doc": stem, "group": g["group"], "pages": pages, "converted": bool(found)}
        if found:
            md = found[0].read_text(encoding="utf-8", errors="replace")
            got = words(strip_boilerplate(md_plain(md)))
            common = sum((ref & got).values())
            exp, sec = sections(text, md, g["tipo"])
            tb = tables(md)
            r.update(recall=common / max(sum(ref.values()), 1), precision=common / max(sum(got.values()), 1),
                     sections_expected=exp, sections_found=sec, tables=len(tb),
                     table_rows=sum(len(c) for c, _ in tb),
                     ragged_rows=sum(sum(n != c[0] for n in c[1:]) for c, _ in tb if c),
                     tables_with_watermark=sum(WM in t.lower() for _, t in tb), watermark=md.lower().count(WM))
        rows.append(r)

df = pd.DataFrame(rows)
RES.mkdir(exist_ok=True)
df.to_csv(RES / "bakeoff_scores.csv", index=False)
ok = df[df["converted"]]
summary = ok.groupby("converter").agg(
    docs=("doc", "count"), pages=("pages", "sum"), recall=("recall", "mean"), precision=("precision", "mean"),
    recall_min=("recall", "min"), sections_found=("sections_found", "sum"), sections_expected=("sections_expected", "sum"),
    tables=("tables", "sum"), table_rows=("table_rows", "sum"), ragged_rows=("ragged_rows", "sum"),
    tables_with_watermark=("tables_with_watermark", "sum"), watermark=("watermark", "sum"))
summary["sections"] = summary["sections_found"] / summary["sections_expected"]
summary["ragged"] = summary["ragged_rows"] / summary["table_rows"].where(summary["table_rows"] > 0)
times = OUT / "times.csv"
if times.exists():   # tempo dell'ultima esecuzione di ogni converter, sul campione intero
    t = pd.read_csv(times).drop_duplicates("converter", keep="last").set_index("converter")
    summary = summary.join(t[["version", "seconds", "status"]])
    summary["s_per_page"] = summary["seconds"] / summary["pages"]
summary.to_csv(RES / "bakeoff_summary.csv")
cols = [c for c in ["docs", "recall", "precision", "recall_min", "sections", "tables", "ragged", "tables_with_watermark",
                    "watermark", "seconds", "s_per_page", "status"] if c in summary]
print(summary[cols].round(3).to_string())
