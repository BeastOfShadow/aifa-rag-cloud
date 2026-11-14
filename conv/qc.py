"""Controllo di qualità di ogni documento convertito, senza GPU: confronta il Markdown con il testo nativo del PDF pulito
(stesse misure di score.py e conv/exp/hybrid_tests.py) e scrive una riga per documento.
Uso sulla VM: .venv/bin/python conv/qc.py <cartella md> <cartella pdf puliti> <uscita.csv>
Colonne: recall e precision sulle parole, titoli QRD attesi/riconosciuti prima e dopo la riparazione, tabelle e coppie di
tabelle consecutive da unire, segnaposto di immagini, parole del Markdown."""
import csv
import re
import sys
from multiprocessing import Pool
from pathlib import Path

import pymupdf

CONV = Path(__file__).resolve().parent
__file__ = str(CONV / "exp" / "hybrid_tests.py")
exec(open(__file__).read().split("rows = []")[0])     # score.py + riparazione titoli, tabelle consecutive, pulizia
MD, PDF, OUT = (Path(a) for a in sys.argv[1:4])


def qc(pdf):
    stem = pdf.stem
    tipo = stem.rsplit("_", 1)[1]
    r = {"doc": stem, "tipo": tipo}
    try:
        d = pymupdf.open(pdf)
        text = re.sub(r"(\w)-\n(\w)", r"\1\2", "\n".join(p.get_text() for p in d))
        r["pages"] = len(d)
    except Exception as e:
        return {**r, "converted": False, "error": f"pdf: {e}"[:80]}
    md_path = MD / f"{stem}.md"
    if not md_path.exists():
        return {**r, "converted": False, "error": "no md"}
    md = md_path.read_text(encoding="utf-8", errors="replace")
    ref, got = words(strip_boilerplate(text)), words(strip_boilerplate(md_plain(md)))
    common = sum((ref & got).values())
    exp, before, plain = sections(text, md, tipo)
    _, after, _ = sections(text, repair(md, tipo), tipo)
    rep, img, tags = clean_stats(md)
    return {**r, "converted": True, "recall": common / max(sum(ref.values()), 1),
            "precision": common / max(sum(got.values()), 1), "ref_words": sum(ref.values()), "md_words": sum(got.values()),
            "sections_expected": exp, "sections_marked": before, "sections_repaired": after,
            "tables": len(tables(md)), "adjacent_tables": adjacent_tables(md), "images": img, "html_tags": tags}


if __name__ == "__main__":
    pdfs = sorted(PDF.glob("*.pdf"))
    with Pool(48) as pool:
        rows = pool.map(qc, pdfs, chunksize=20)
    cols = ["doc", "tipo", "pages", "converted", "recall", "precision", "ref_words", "md_words", "sections_expected",
            "sections_marked", "sections_repaired", "tables", "adjacent_tables", "images", "html_tags", "error"]
    with open(OUT, "w", newline="") as f:
        w = csv.DictWriter(f, cols, restval="")
        w.writeheader(); w.writerows(rows)
    print(f"{sum(r['converted'] for r in rows)} documenti su {len(rows)} -> {OUT}")
