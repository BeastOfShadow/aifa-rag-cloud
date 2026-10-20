"""s3 - PDF -> Markdown, PARALLELO (multiprocessing su tutti i core).

pymupdf4llm estrae il testo nativo E, dove MuPDF+tesseract trovano pagine-immagine,
le OCR-a automaticamente -> immagini/scansioni gestite nel RAG.
Seriale era ~15h (1 core); qui gira su tutti i core -> ~45 min.

Resume: salta i md gia' presenti. PDF cancellato dopo conversione (se non KEEP_PDF).
Marca scanned=1 i doc a bassa densita' testo (per report), ma li converte comunque (OCR).
"""
from __future__ import annotations
import os
import sqlite3
from multiprocessing import Pool
from pathlib import Path
import pymupdf
import pymupdf4llm
from tqdm import tqdm
from config import DB_PATH, PDF_DIR, MD_DIR, KEEP_PDF, OCR_MIN_CHARS_PER_PAGE


def _convert_one(args):
    """Worker: un PDF -> md. Ritorna (farm, tipo, md_path, md_chars, scanned, stato)."""
    farm, tipo = args
    path = str(PDF_DIR / f"{farm}_{tipo}.pdf")   # non il path del manifest: il campione cambia macchina
    md_path = MD_DIR / f"{farm}_{tipo}.md"
    if md_path.exists():
        return (farm, tipo, None, None, None, "skip")
    if not Path(path).exists():
        return (farm, tipo, None, None, None, "nopdf")
    try:
        doc = pymupdf.open(path)
        pages = doc.page_count or 1
        dens = sum(len(p.get_text()) for p in doc) / pages
        doc.close()
        is_scan = 1 if dens < OCR_MIN_CHARS_PER_PAGE else 0
        md = pymupdf4llm.to_markdown(path, show_progress=False)  # OCR-a le pagine-immagine
        md_path.write_text(md, encoding="utf-8")
        if not KEEP_PDF:
            Path(path).unlink(missing_ok=True)
        return (farm, tipo, str(md_path), len(md), is_scan, "ok")
    except Exception as e:
        return (farm, tipo, None, None, None, f"err:{e}")


def convert_all(workers=None):
    workers = workers or int(os.environ.get("WORKERS", "0")) or os.cpu_count()
    con = sqlite3.connect(DB_PATH)
    for col, typ in (("md_path", "TEXT"), ("md_chars", "INTEGER"), ("scanned", "INTEGER")):
        try:
            con.execute(f"ALTER TABLE docs ADD COLUMN {col} {typ}")
        except sqlite3.OperationalError:
            pass
    con.commit()

    rows = con.execute(
        "SELECT cod_farmaco, tipo FROM docs WHERE status IN ('ok','cached')").fetchall()
    todo = [r for r in rows if not (MD_DIR / f"{r[0]}_{r[1]}.md").exists()]
    print(f"da convertire: {len(todo)} (gia' fatti {len(rows) - len(todo)}) | workers={workers}")

    ok = scan = fail = 0
    batch = []
    with Pool(workers) as pool:
        for farm, tipo, mdp, mc, isscan, st in tqdm(
                pool.imap_unordered(_convert_one, todo, chunksize=8),
                total=len(todo), desc="pdf->md", unit="doc"):
            if st == "ok":
                batch.append((mdp, mc, isscan, farm, tipo))
                ok += 1
                scan += isscan or 0
            elif st == "skip":
                ok += 1
            elif st.startswith("err"):
                fail += 1
                tqdm.write(f"  {st}  {farm}_{tipo}")
            if len(batch) >= 200:
                con.executemany("UPDATE docs SET md_path=?,md_chars=?,scanned=? "
                                "WHERE cod_farmaco=? AND tipo=?", batch)
                con.commit()
                batch = []
    if batch:
        con.executemany("UPDATE docs SET md_path=?,md_chars=?,scanned=? "
                        "WHERE cod_farmaco=? AND tipo=?", batch)
        con.commit()
    con.close()
    print(f"\nok={ok} scanned={scan} fail={fail}")


if __name__ == "__main__":
    convert_all()
