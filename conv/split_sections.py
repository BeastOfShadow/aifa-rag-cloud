"""Sezioni QRD separate di ogni documento del corpus finale (conv/out/final), senza perdere niente: ogni file di sezione
è un pezzo esatto del documento, e rimettendo insieme le sezioni in ordine si riottiene il documento byte per byte
(lo verifica alla fine, su tutti). Il documento intero non si tocca.
Scrive in <uscita>:
- sections/<documento>/<NN>_<sezione>.md: il testo della sezione, identico al documento (NN = ordine nel documento);
  "pre" è il testo prima della prima sezione, "intero" un documento senza modello QRD (formato vecchio);
- sections/<documento>/index.md: le sezioni del documento, con i link;
- corpus.sqlite: documents (un documento per riga, con farmaco, converter, recall, impronta sha256) e sections (sezione,
  titolo, inizio e fine nel documento, percorso, impronta, parole), da unire a farmaci.sqlite con cod_farmaco.
Opzionale, solo se richiesta: la versione pulita (frasi ricorrenti oltre PCT e simboli di scritture non italiane) in
clean/, con le frasi tolte in removed.csv.
Uso: .venv/bin/python conv/split_sections.py <md finali> <uscita> [<phrases.csv> <PCT>]
     FARMACI_DB=<farmaci.sqlite> (default data/farmaci.sqlite), QC=<qc.csv del corpus finale> (opzionale)"""
import csv
import hashlib
import os
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
    """[(id sezione, titolo, testo)] sui titoli QRD, pezzi esatti e contigui del documento: concatenati danno md.
    Il testo prima della prima sezione è 'pre' (se è solo spazio va nella prima sezione); con meno di 3 sezioni
    (formato vecchio, senza modello QRD) un'unica sezione 'intero'"""
    pos = sorted((p, k) for k, p in heading_positions(md, tipo).items())
    if len(pos) < 3:
        return [("intero", "", md)]
    out = []
    if md[:pos[0][0]].strip():
        out.append(("pre", "", md[:pos[0][0]]))
    else:
        pos[0] = (0, pos[0][1])
    for i, (p, k) in enumerate(pos):
        body = md[p:pos[i + 1][0] if i + 1 < len(pos) else len(md)]
        out.append((k, body.lstrip().split("\n", 1)[0].strip("# *\t")[:100], body))
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


def sha(t):
    return hashlib.sha256(t.encode("utf-8")).hexdigest()


if __name__ == "__main__":
    MD, OUT = Path(sys.argv[1]), Path(sys.argv[2])
    PHR, PCT = (Path(sys.argv[3]), float(sys.argv[4])) if len(sys.argv) > 4 else (None, None)
    boiler = defaultdict(set)
    if PHR:
        for r in csv.DictReader(open(PHR)):
            if float(r["pct"]) >= PCT:
                boiler[r["tipo"], r["section"]].add(r["phrase"])
    con = sqlite3.connect(os.environ.get("FARMACI_DB", CONV.parent / "data" / "farmaci.sqlite"))
    meta = {}
    for farm, den, atc, rag, forma in con.execute("SELECT COD_FARMACO, DENOMINAZIONE, CODICE_ATC, RAGIONE_SOCIALE, FORMA FROM conf"):
        meta.setdefault(str(farm), (den or "", atc or "", rag or "", forma or ""))
    prov = {r["doc"]: r["source"] for r in csv.DictReader(open(MD / "provenance.csv"))} if (MD / "provenance.csv").exists() else {}
    qc = {r["doc"]: r for r in csv.DictReader(open(os.environ["QC"]))} if os.environ.get("QC") else {}
    OUT.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(OUT / "corpus.sqlite")
    db.executescript("""DROP TABLE IF EXISTS documents; DROP TABLE IF EXISTS sections;
        CREATE TABLE documents(doc TEXT PRIMARY KEY, cod_farmaco TEXT, tipo TEXT, denominazione TEXT, codice_atc TEXT,
          ragione_sociale TEXT, forma TEXT, md_file TEXT, source TEXT, recall REAL, precision REAL, chars INTEGER,
          n_sections INTEGER, structure TEXT, sha256 TEXT);
        CREATE TABLE sections(doc TEXT, ord INTEGER, section TEXT, title TEXT, start INTEGER, end INTEGER, file TEXT,
          words INTEGER, chars INTEGER, tables INTEGER, sha256 TEXT, PRIMARY KEY(doc, ord));
        CREATE INDEX sections_by_id ON sections(section);""")
    removed = defaultdict(Counter); bad = 0; nsec = 0
    for f in sorted(MD.glob("*.md")):
        cod, tipo = f.stem.rsplit("_", 1)
        md = f.read_text(encoding="utf-8")
        secs = sections_of(md, tipo)
        d = OUT / "sections" / f.stem; d.mkdir(parents=True, exist_ok=True)
        start, idx = 0, [f"# {f.stem}\n", f"{meta.get(cod, ('',))[0]} · {tipo} · [documento intero]({os.path.relpath(f, d)})\n"]
        for o, (k, title, body) in enumerate(secs):
            name = f"{o:02d}_{k}.md"
            (d / name).write_text(body, encoding="utf-8")
            db.execute("INSERT INTO sections VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                       (f.stem, o, k, title, start, start + len(body), f"sections/{f.stem}/{name}",
                        len(re.findall(r"\w+", md_plain(body))), len(body),
                        len(re.findall(r"<table|^\s*\|[\s:-]+\|", body, re.M | re.I)), sha(body)))
            idx.append(f"- [{k}{' · ' + title if title else ''}]({name})\n")
            if PHR:
                c = OUT / "clean" / f.stem; c.mkdir(parents=True, exist_ok=True)
                (c / name).write_text(clean(body, boiler[tipo, k], removed[tipo, k]), encoding="utf-8")
            start += len(body)
        (d / "index.md").write_text("".join(idx), encoding="utf-8")
        # verifica: le sezioni, rilette dal disco e rimesse insieme, sono il documento identico
        back = "".join((d / f"{o:02d}_{k}.md").read_text(encoding="utf-8") for o, (k, _, _) in enumerate(secs))
        bad += back != md
        q = qc.get(f.stem, {})
        db.execute("INSERT INTO documents VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                   (f.stem, cod, tipo, *meta.get(cod, ("", "", "", "")), f"{MD.name}/{f.name}", prov.get(f.stem, ""),
                    float(q["recall"]) if q.get("recall") else None, float(q["precision"]) if q.get("precision") else None,
                    len(md), len(secs), "intero" if secs[0][0] == "intero" else "qrd", sha(md)))
        nsec += len(secs)
    db.commit()
    if PHR:
        with open(OUT / "removed.csv", "w", newline="") as fo:
            w = csv.writer(fo); w.writerow(["tipo", "section", "phrase", "times"])
            for (tipo, k), c in sorted(removed.items()):
                for s, n in c.most_common():
                    w.writerow([tipo, k, s, n])
    n = db.execute("SELECT count(*) FROM documents").fetchone()[0]
    print(f"{n} documenti, {nsec} sezioni -> {OUT}; documenti non identici rimettendo insieme le sezioni: {bad}")
