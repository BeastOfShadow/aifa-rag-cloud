"""Sezioni QRD separate, grezze e pulite, di ogni documento del corpus finale (conv/out/final), più un indice.
- <uscita>/raw/<documento>/<sezione>.md: il testo della sezione, identico a quello del documento (con il titolo);
- <uscita>/clean/<documento>/<sezione>.md: lo stesso testo senza (a) le frasi che tornano in almeno PCT dei documenti della
  stessa sezione (formule di rito, indice dei FI: conv/out/vocab/phrases.csv) e (b) i simboli di scritture che non sono
  italiano, né greco (cirillico, arabo, CJK…) e i caratteri di controllo. Titoli, tabelle, numeri, unità, negazioni e nomi
  restano. Nessun documento intero viene modificato;
- <uscita>/sections.csv: una riga per sezione (documento, farmaco, ATC, titolare, parole e caratteri prima e dopo);
- <uscita>/removed.csv: ogni frase tolta, con quante volte e in quale sezione.
Uso: .venv/bin/python conv/split_sections.py <md finali> <phrases.csv> <uscita> [PCT=0.5] [limite documenti]"""
import csv
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path

CONV = Path(__file__).resolve().parent
__file__ = str(CONV / "exp" / "hybrid_tests.py")
exec(open(__file__).read().split("rows = []")[0])      # heading_positions, md_plain
SPLIT = re.compile(r"((?<=[.;:!?])\s+)")                # frasi, conservando i separatori
NOISE = re.compile(r"\S*[Ѐ-ԯ֐-ۿऀ-෿฀-๿぀-鿿가-힯]\S*|[\x00-\x08\x0b\x0c\x0e-\x1f]")


def sections_of(md, tipo):
    """[(id sezione, titolo, testo)] sui titoli QRD; il testo prima della prima sezione è 'pre'"""
    pos = sorted((p, k) for k, p in heading_positions(md, tipo).items())
    if len(pos) < 3:                         # formato vecchio, senza il modello QRD: un'unica sezione
        return [("", "", md)]
    out = [("pre", "", md[:pos[0][0]])] if md[:pos[0][0]].strip() else []
    for i, (p, k) in enumerate(pos):
        body = md[p:pos[i + 1][0] if i + 1 < len(pos) else len(md)]
        out.append((k, body.split("\n", 1)[0].strip("# *\t")[:100], body))
    return out


def norm(sentence):
    """frase normalizzata come nel vocabolario: minuscole, numeri come #, solo se lunga e non di tabella"""
    s = re.sub(r"\s+", " ", md_plain(sentence).lower()).strip(" -*|#")
    s = re.sub(r"\d+(?:[.,]\d+)*", "#", s)
    return s if len(s) >= 40 and not sentence.lstrip().startswith(("<", "|")) else ""


def clean(body, boiler, removed):
    lines = body.split("\n")
    out = [lines[0]]                                       # il titolo resta sempre
    for line in lines[1:]:
        if line.lstrip().startswith(("#", "|", "<")):
            kept = line
        else:
            parts = SPLIT.split(line); kept = ""
            for j in range(0, len(parts), 2):
                n = norm(parts[j])
                if n and n in boiler:
                    removed[n] += 1
                else:
                    kept += parts[j] + (parts[j + 1] if j + 1 < len(parts) else "")
        kept = NOISE.sub("", kept).rstrip()
        if kept.strip() or not line.strip():
            out.append(kept)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip() + "\n"


if __name__ == "__main__":
    MD, PHR, OUT = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
    PCT = float(sys.argv[4]) if len(sys.argv) > 4 else 0.5
    LIMIT = int(sys.argv[5]) if len(sys.argv) > 5 else 0
    boiler = defaultdict(set)
    for r in csv.DictReader(open(PHR)):
        if float(r["pct"]) >= PCT:
            boiler[r["tipo"], r["section"]].add(r["phrase"])
    con = sqlite3.connect(CONV.parent / "data" / "farmaci.sqlite")
    meta = {}
    for farm, den, atc, rag, forma in con.execute("SELECT COD_FARMACO, DENOMINAZIONE, CODICE_ATC, RAGIONE_SOCIALE, FORMA FROM conf"):
        meta.setdefault(farm, (den or "", atc or "", rag or "", forma or ""))
    OUT.mkdir(parents=True, exist_ok=True)
    idx = csv.writer(open(OUT / "sections.csv", "w", newline=""))
    idx.writerow(["doc", "cod_farmaco", "tipo", "section", "title", "denominazione", "codice_atc", "ragione_sociale", "forma",
                  "words_raw", "words_clean", "chars_raw", "chars_clean"])
    removed = defaultdict(Counter); nfiles = 0
    for f in sorted(MD.glob("*.md"))[: LIMIT or None]:
        cod, tipo = f.stem.rsplit("_", 1)
        for k, title, body in sections_of(f.read_text(encoding="utf-8", errors="replace"), tipo):
            if not body.strip():
                continue
            sid = k or "intero"
            c = clean(body, boiler[tipo, k], removed[tipo, k])
            for kind, text in (("raw", body.strip() + "\n"), ("clean", c)):
                d = OUT / kind / f.stem; d.mkdir(parents=True, exist_ok=True)
                (d / f"{sid}.md").write_text(text, encoding="utf-8")
            w = lambda t: len(re.findall(r"\w+", md_plain(t)))
            idx.writerow([f.stem, cod, tipo, sid, title, *meta.get(cod, ("", "", "", "")), w(body), w(c), len(body), len(c)])
            nfiles += 1
    with open(OUT / "removed.csv", "w", newline="") as fo:
        w2 = csv.writer(fo); w2.writerow(["tipo", "section", "phrase", "times"])
        for (tipo, k), c in sorted(removed.items()):
            for s, n in c.most_common():
                w2.writerow([tipo, k, s, n])
    print(f"{nfiles} sezioni -> {OUT} (soglia frasi {PCT:.0%})")
