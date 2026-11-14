"""Suddivisione in sezioni QRD e vocabolario del corpus finale (Markdown di conv/assemble.py), per sezione.
Per ogni documento taglia il testo sui titoli QRD (RCP 1-10, 4.1-6.6; FI 1-6) e scrive:
- sections.csv: una riga per sezione (documento, tipo, sezione, titolo, parole, caratteri, tabelle);
- terms.csv: parole, coppie e terne di parole (tipo, sezione, n, termine, df, tf): df = in quanti documenti compare;
- terms_global.csv: le parole sull'intero documento (tipo, termine, df, tf, docs), da 2 documenti in su: così si trovano
  anche i nomi commerciali rari, divisi tra più sezioni, e df è il numero vero di documenti;
- phrases.csv: frasi che tornano identiche (minuscole, numeri come #) in molti documenti della stessa sezione, con la quota
  di documenti: sono i candidati da togliere (boilerplate) quando passano una soglia alta.
Uso: .venv/bin/python conv/vocab.py <cartella md> <cartella uscita> [limite documenti]"""
import csv
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

CONV = Path(__file__).resolve().parent
__file__ = str(CONV / "exp" / "hybrid_tests.py")
exec(open(__file__).read().split("rows = []")[0])      # heading_positions, md_plain, section_patterns, words
MD, OUT = Path(sys.argv[1]), Path(sys.argv[2])
LIMIT = int(sys.argv[3]) if len(sys.argv) > 3 else 0
MIN_DF = {1: 5, 2: 20, 3: 20}                          # soglie di scrittura di parole, coppie, terne
MIN_PHRASE_DF = 20
MIN_GLOBAL_DF = 2
TOKEN = re.compile(r"[^\W\d_]{2,}|\d+(?:[.,]\d+)?")


def sections_of(md, tipo):
    pos = sorted((p, k) for k, p in heading_positions(md, tipo).items())
    if not pos:
        return [("", "", md)]
    out = [("pre", "", md[:pos[0][0]])] if md[:pos[0][0]].strip() else []
    for i, (p, k) in enumerate(pos):
        body = md[p:pos[i + 1][0] if i + 1 < len(pos) else len(md)]
        title = body.split("\n", 1)[0].strip("# *\t")[:100]
        out.append((k, title, body))
    return out


def sentences(text):
    for s in re.split(r"(?<=[.;:!?])\s+|\n+", text):
        s = re.sub(r"\s+", " ", s.lower()).strip(" -*|#")
        s = re.sub(r"\d+(?:[.,]\d+)*", "#", s)
        if len(s) >= 40 and not s.startswith(("<", "|")):
            yield s


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    df = defaultdict(Counter); tf = defaultdict(Counter); ph = defaultdict(Counter); ndoc = Counter()
    gdf = defaultdict(Counter); gtf = defaultdict(Counter); gdocs = Counter()
    secs = csv.writer(open(OUT / "sections.csv", "w", newline=""))
    secs.writerow(["doc", "tipo", "section", "title", "words", "chars", "tables"])
    files = sorted(MD.glob("*.md"))[: LIMIT or None]
    for n, f in enumerate(files):
        md = f.read_text(encoding="utf-8", errors="replace")
        tipo = f.stem.rsplit("_", 1)[1]
        alltoks = [t.lower() for t in TOKEN.findall(md_plain(md))]
        gdocs[tipo] += 1; gtf[tipo].update(alltoks); gdf[tipo].update(set(alltoks))
        for k, title, body in sections_of(md, tipo):
            plain = md_plain(body)
            toks = [t.lower() for t in TOKEN.findall(plain)]
            secs.writerow([f.stem, tipo, k, title, len(toks), len(body), len(re.findall(r"<table|^\s*\|---", body, re.M | re.I))])
            key = (tipo, k); ndoc[key] += 1
            tf[key, 1].update(toks)
            for m in (1, 2, 3):
                df[key, m].update({" ".join(toks[i:i + m]) for i in range(len(toks) - m + 1)})
            ph[key].update(set(sentences(plain)))
        if n % 2000 == 0:
            print(n, "documenti", flush=True)
    with open(OUT / "terms.csv", "w", newline="") as fo:
        w = csv.writer(fo); w.writerow(["tipo", "section", "n", "term", "df", "tf", "docs"])
        for (key, m), c in df.items():
            for term, d in c.items():
                if d >= MIN_DF[m]:
                    w.writerow([*key, m, term, d, tf[key, 1][term] if m == 1 else "", ndoc[key]])
    with open(OUT / "terms_global.csv", "w", newline="") as fo:
        w = csv.writer(fo); w.writerow(["tipo", "term", "df", "tf", "docs"])
        for tipo, c in gdf.items():
            for term, d in c.items():
                if d >= MIN_GLOBAL_DF:
                    w.writerow([tipo, term, d, gtf[tipo][term], gdocs[tipo]])
    with open(OUT / "phrases.csv", "w", newline="") as fo:
        w = csv.writer(fo); w.writerow(["tipo", "section", "phrase", "df", "pct", "docs"])
        for key, c in ph.items():
            for s, d in c.most_common():
                if d < MIN_PHRASE_DF:
                    break
                w.writerow([*key, s, d, round(d / ndoc[key], 4), ndoc[key]])
    print(f"{len(files)} documenti, {sum(ndoc.values())} sezioni -> {OUT}")
