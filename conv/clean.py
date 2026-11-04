"""Toglie dai PDF il timbro che AIFA aggiunge a ogni pagina, prima della conversione.

Ogni pagina timbrata ha quattro flussi di contenuto: il piè di pagina AIFA (data di pubblicazione e testo legale),
una `q`, la pagina originale del produttore, e una `Q` seguita dalla filigrana diagonale "Agenzia Italiana del Farmaco"
(semitrasparente, ruotata, nello strato di testo: confonde i modelli di layout e finisce nel Markdown). Svuoto il primo
flusso e lascio nell'ultimo solo la `Q` che chiude la `q`, così la pagina originale resta identica. La data di
pubblicazione la tengo in <uscita>/stamp.csv; le pagine con uno schema diverso restano com'erano e le conto.
Uso: .venv/bin/python conv/clean.py <cartella PDF> <cartella uscita>
"""
import csv
import re
import sys
from multiprocessing import Pool
from pathlib import Path

import pymupdf

DATE = re.compile(rb"\(Documento reso disponibile da AIFA il (\d{1,2}/\d{1,2}/\d{4})\)")


def clean(args):
    src, dst = args
    doc = pymupdf.open(src)
    stamped, date = 0, ""
    for page in doc:
        xr = page.get_contents()
        if len(xr) < 3:
            continue
        first, last = doc.xref_stream(xr[0]) or b"", doc.xref_stream(xr[-1]) or b""
        if b"(Documento reso disponibile" in first and last.startswith(b"Q\nq\n/Gs") and b"(Agenzia Italiana del" in last:
            date = date or (m.group(1).decode() if (m := DATE.search(first)) else "")
            doc.update_stream(xr[0], b"")
            doc.update_stream(xr[-1], b"Q\n")
            stamped += 1
    doc.save(dst, garbage=3, deflate=True)
    return {"file": Path(src).name, "pages": len(doc), "stamped": stamped, "date": date}


def check():
    """La pulizia toglie il timbro e lascia identico il resto del testo (pagina sintetica con lo stesso schema)."""
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 100), "4.8 Effetti indesiderati")
    orig = page.get_contents()[0]
    foot = doc.get_new_xref(); doc.update_object(foot, "<<>>")
    doc.update_stream(foot, b"BT /helv 8 Tf 56 26 Td (Documento reso disponibile da AIFA il 01/02/2026)Tj ET")
    q = doc.get_new_xref(); doc.update_object(q, "<<>>"); doc.update_stream(q, b"q\n")
    wm = doc.get_new_xref(); doc.update_object(wm, "<<>>")
    doc.update_stream(wm, b"Q\nq\n/Gs1 gs\nBT /helv 65 Tf 100 400 Td (Agenzia Italiana del Farmaco)Tj ET Q")
    doc.xref_set_key(page.xref, "Contents", f"[{foot} 0 R {q} 0 R {orig} 0 R {wm} 0 R]")
    tmp = Path("/tmp/clean_check.pdf"); doc.save(tmp)
    r = clean((tmp, tmp.with_name("clean_check_out.pdf")))
    text = pymupdf.open(tmp.with_name("clean_check_out.pdf"))[0].get_text()
    assert r["stamped"] == 1 and r["date"] == "01/02/2026", r
    assert "4.8 Effetti indesiderati" in text and "Documento reso" not in text and "Agenzia" not in text, text


if __name__ == "__main__":
    check()
    src, dst = Path(sys.argv[1]), Path(sys.argv[2])
    dst.mkdir(parents=True, exist_ok=True)
    jobs = [(p, dst / p.name) for p in sorted(src.glob("*.pdf"))]
    with Pool() as pool:
        rows = pool.map(clean, jobs, chunksize=16)
    with open(dst / "stamp.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=rows[0].keys())
        w.writeheader()
        w.writerows(rows)
    full = sum(r["stamped"] == r["pages"] for r in rows)
    print(f"{len(rows)} PDF, timbro tolto da tutte le pagine in {full}, da nessuna in "
          f"{sum(r['stamped'] == 0 for r in rows)} -> {dst}")
