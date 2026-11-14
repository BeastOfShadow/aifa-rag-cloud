"""Assembla il corpus finale: per ogni documento la versione scelta (Docling, o MinerU/olmOCR dove il ripiego è migliore),
poi tre passaggi per regola, senza modelli:
1. unione delle tabelle spezzate tra due pagine: due tabelle consecutive con lo stesso numero di colonne e in mezzo solo
   righe vuote o commenti (marcatori di pagina, immagini); l'intestazione ripetuta della seconda si toglie, altrimenti
   la sua prima riga diventa una riga di dati;
2. tolti i commenti HTML (marcatori di pagina, segnaposto di immagini);
3. riparazione dei titoli QRD (righe che sono un titolo diventano "## titolo").
Scrive <uscita>/<doc>.md e <uscita>/provenance.csv (sorgente, tabelle unite, titoli aggiunti).
Uso: .venv/bin/python conv/assemble.py <docling> <mineru> <olmocr> <scelte.csv> <uscita>"""
import csv
import re
import sys
from multiprocessing import Pool
from pathlib import Path

CONV = Path(__file__).resolve().parent
__file__ = str(CONV / "exp" / "hybrid_tests.py")
exec(open(__file__).read().split("rows = []")[0])       # repair() e quello che serve
DOCLING, MINERU, OLMOCR, CHOICES, OUT = (Path(a) for a in sys.argv[1:6])
GARBLED = {"044480_FI", "044480_RCP"}               # testo nativo illeggibile: solo OCR da immagine (conv/exp/garbled.py)
GAP = r"(?:[ \t]*\n|[ \t]*<!--.*?-->[ \t]*\n?)"     # una riga vuota o un commento


def ncols_md(line):
    return len(line.strip().strip("|").split("|"))


def merge_md_tables(md):
    """unisce le tabelle Markdown consecutive con lo stesso numero di colonne e niente di testo in mezzo"""
    n = 0
    pat = re.compile(r"((?:^[ \t]*\|[^\n]*\n)+)" + r"(" + GAP + r"+)" + r"((?:^[ \t]*\|[^\n]*\n)+)", re.M)
    while True:
        found = None
        for m in pat.finditer(md + "\n"):
            a, b = m.group(1).splitlines(), m.group(3).splitlines()
            if ncols_md(a[0]) == ncols_md(b[0]):
                found = (m, a, b); break
        if not found:
            return md.rstrip("\n") + "\n", n
        m, a, b = found
        if len(b) > 1 and re.fullmatch(r"[\s|:-]+", b[1]):          # la seconda ha intestazione e riga ---
            same = re.sub(r"\s+", "", b[0]) == re.sub(r"\s+", "", a[0])
            b = b[2:] if same else [b[0]] + b[2:]                    # ripetuta: via; altrimenti diventa riga di dati
        md = (md + "\n")[:m.start()] + "\n".join(a + b) + "\n" + (md + "\n")[m.end():]
        n += 1


def merge_html_tables(md):
    n = 0
    pat = re.compile(r"</table>" + GAP + r"*<table[^>]*>", re.I)

    def first_row(t):
        r = re.search(r"<tr.*?</tr>", t, re.S | re.I)
        return re.sub(r"\s+", "", r.group(0)) if r else ""

    def cols(t):
        r = re.search(r"<tr.*?</tr>", t, re.S | re.I)
        return sum(int(re.search(r"colspan=\"?(\d+)", c, re.I).group(1)) if re.search(r"colspan=\"?(\d+)", c, re.I) else 1
                   for c in re.findall(r"<t[dh]\b[^>]*>", r.group(0), re.I)) if r else -1
    pos = 0
    while True:
        m = pat.search(md, pos)
        if not m:
            return md, n
        s = md.rfind("<table", 0, m.start()); e = md.find("</table>", m.end())
        a, b = md[s:m.start() + 8], md[m.end() - 0:e]
        ta, tb = a, "<table>" + b
        if e < 0 or cols(ta) != cols(tb):
            pos = m.end(); continue
        rest = b
        if first_row(tb) == first_row(ta):                            # intestazione ripetuta
            rest = re.sub(r"^\s*(?:<t(?:head|body)>\s*)?<tr.*?</tr>", "", b, count=1, flags=re.S | re.I)
        md = md[:m.start()] + "\n" + re.sub(r"^\s*<t(?:head|body)>", "", rest) + md[e:]
        n += 1


def clean(md):
    md = re.sub(r"<!--.*?-->", "", md, flags=re.S)
    return re.sub(r"\n{3,}", "\n\n", md).strip() + "\n"


def build(args):
    stem, src = args
    base = {"docling": DOCLING, "mineru": MINERU, "olmocr": OLMOCR}[src]
    md = (base / f"{stem}.md").read_text(encoding="utf-8", errors="replace")
    md, n1 = merge_md_tables(md)
    md, n2 = merge_html_tables(md)
    md = clean(md)
    tipo = stem.rsplit("_", 1)[1]
    fixed = repair(md, tipo)
    added = sum(1 for a, b in zip(md.splitlines(), fixed.splitlines()) if a != b)
    (OUT / f"{stem}.md").write_text(fixed, encoding="utf-8")
    return stem, src, n1, n2, added


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    choice = {r["doc"]: r["who"] for r in csv.DictReader(open(CHOICES))}
    jobs = [(p.stem, "mineru" if p.stem in GARBLED else choice.get(p.stem, "docling")) for p in sorted(DOCLING.glob("*.md"))]
    with Pool(48) as pool:
        rows = pool.map(build, jobs, chunksize=20)
    with open(OUT / "provenance.csv", "w", newline="") as f:
        w = csv.writer(f); w.writerow(["doc", "source", "md_tables_merged", "html_tables_merged", "headings_added"]); w.writerows(rows)
    print(f"{len(rows)} documenti, tabelle unite {sum(r[2] + r[3] for r in rows)}, titoli aggiunti {sum(r[4] for r in rows)}, "
          f"sorgenti {dict(__import__('collections').Counter(r[1] for r in rows))}")
