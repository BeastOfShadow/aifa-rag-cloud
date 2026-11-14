"""Test di ibrido e di riparazione sul campione, tutto automatico (nessun controllo a mano):
1. riparazione dei titoli: le righe che sono un titolo QRD (stessa regola del punteggio) diventano "## titolo";
   misuro i titoli riconosciuti prima e dopo, il testo non cambia;
2. ripiego per documento: Docling, e dove la recall è sotto soglia il migliore degli altri;
3. tabelle: coppie di tabelle consecutive con lo stesso numero di colonne e poco in mezzo (tabelle spezzate tra pagine);
4. pulizia, ogni 100.000 parole: righe ripetute 5+ volte (testate e piè di pagina), segnaposto di immagini, tag HTML
   fuori dalle tabelle.
Uso: .venv/bin/python conv/exp/hybrid_tests.py  -> stampa e scrive conv/results/hybrid_tests.csv"""
import csv
import re
from collections import Counter
from pathlib import Path

import pandas as pd
import pymupdf

CONV = Path(__file__).resolve().parent.parent
__file__ = str(CONV / "score.py")
exec(open(__file__).read().split("gold = list")[0])      # words, md_plain, strip_boilerplate, section_patterns, tables
CONVS = ["docling", "mineru", "olmocr"]
THRESHOLD = 0.95
MARK = r"(#{1,6}\s*(\*\*\s*)?|\*\*\s*)"


def heading_positions(md, tipo):
    """{id sezione: offset della riga del titolo} per i titoli QRD: RCP prima occorrenza dopo la sezione precedente, FI
    l'ultima (l'indice iniziale ripete i titoli); solo righe corte"""
    pats = section_patterns(tipo)
    last, found = -1, {}
    for k in sorted(pats, key=lambda k: [int(x) for x in k.split(".")] if tipo == "RCP" else [int(k[0])]):
        body = (r"(\d+\.?\s*)?\**\s*" + pat_tail(pats[k])) if tipo == "FI" else pats[k]
        c = [m.start() for m in re.finditer(r"^[ \t]*" + MARK + "?" + body + r"[^\n]{0,110}$", md, re.M | re.I) if m.start() > last]
        if c:
            last = c[-1] if tipo == "FI" else c[0]
            found[k] = last
    return found


def heading_lines(md, tipo):
    return sorted(heading_positions(md, tipo).values())


def pat_tail(p):
    return p.split(r"\.?", 1)[-1]


def repair(md, tipo):
    pos, out, off = set(heading_lines(md, tipo)), [], 0
    for line in md.splitlines(keepends=True):
        n = len(line)       # offset sul testo originale, anche se la riga viene riscritta
        if off in pos and not line.lstrip().startswith("#"):
            line = "## " + line.strip().strip("*").strip() + "\n"
        out.append(line)
        off += n
    return "".join(out)


def adjacent_tables(md):
    """coppie di tabelle Markdown/HTML consecutive con lo stesso numero di colonne e meno di 4 righe di testo in mezzo"""
    blocks = [(m.start(), m.end(), len(m.group(0).splitlines()[0].strip("|").split("|"))) for m in
              re.finditer(r"(?:^[ \t]*\|.*\n?)+", md, re.M)]
    blocks += [(m.start(), m.end(), len(re.findall(r"<t[dh]\b", re.search(r"<tr.*?</tr>", m.group(0), re.S | re.I).group(0), re.I)))
               for m in re.finditer(r"<table.*?</table>", md, re.S | re.I) if re.search(r"<tr", m.group(0), re.I)]
    blocks.sort()
    return sum(b[2] == a[2] and len([l for l in md[a[1]:b[0]].splitlines() if l.strip()]) < 4 for a, b in zip(blocks, blocks[1:]))


def clean_stats(md):
    lines = [l.strip() for l in md.splitlines() if len(l.strip()) > 20 and not l.lstrip().startswith("|")]
    rep = sum(n for n in Counter(lines).values() if n >= 5)
    img = len(re.findall(r"<!--\s*image\s*-->|!\[[^\]]*\]\(", md, re.I))
    html = len(re.findall(r"<(?!/?(?:table|thead|tbody|tfoot|tr|td|th|caption|colgroup|col)\b)[a-zA-Z][^<>]*>", re.sub(
        r"<table.*?</table>", "", md, flags=re.S | re.I)))
    return rep, img, html


rows = []
for g in csv.DictReader(open(CONV / "gold.csv")):
    s = f"{g['cod']}_{g['tipo']}"
    pdf = "\n".join(p.get_text() for p in pymupdf.open(CONV / f"data/pdf_gold_clean/{s}.pdf"))
    pdf = re.sub(r"(\w)-\n(\w)", r"\1\2", pdf)
    ref = words(strip_boilerplate(pdf))
    for c in CONVS:
        md = next(CONV.glob(f"out/{c}/**/{s}.md")).read_text(errors="replace")
        fixed = repair(md, g["tipo"])
        got = words(strip_boilerplate(md_plain(md)))
        e, before, _ = sections(pdf, md, g["tipo"])
        _, after, _ = sections(pdf, fixed, g["tipo"])
        rep, img, tags = clean_stats(md)
        rows.append(dict(doc=s, converter=c, recall=sum((ref & got).values()) / sum(ref.values()), expected=e,
                         sec_before=before, sec_after=after, adjacent_tables=adjacent_tables(md),
                         words=sum(got.values()), repeated=rep, images=img, html_tags=tags))
df = pd.DataFrame(rows)
df.to_csv(CONV / "results" / "hybrid_tests.csv", index=False)
g = df.groupby("converter")
t = g.agg(recall=("recall", "mean"), expected=("expected", "sum"), before=("sec_before", "sum"), after=("sec_after", "sum"),
          adjacent=("adjacent_tables", "sum"), words=("words", "sum"), repeated=("repeated", "sum"), images=("images", "sum"),
          html=("html_tags", "sum"))
t["sections_before"] = t.before / t.expected; t["sections_after"] = t.after / t.expected
for c in ("repeated", "images", "html"): t[c + "_per_100k"] = t[c] / t.words * 1e5
print(t[["recall", "sections_before", "sections_after", "adjacent", "repeated_per_100k", "images_per_100k", "html_per_100k"]].round(4).to_string())
r = df.pivot(index="doc", columns="converter", values="recall")
fb = r.docling.where(r.docling >= THRESHOLD, r.max(axis=1))
print(f"\nripiego sotto {THRESHOLD}: recall media docling {r.docling.mean():.4f} -> {fb.mean():.4f}, minimo {r.docling.min():.3f} -> "
      f"{fb.min():.3f}, documenti cambiati: {int((r.docling < THRESHOLD).sum())} {list(r.index[r.docling < THRESHOLD])}")
