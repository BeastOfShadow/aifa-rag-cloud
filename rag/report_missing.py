"""report_missing - elenco documenti mancanti dal corpus RAG.

Legge il manifest `docs` + join anagrafica conf. Scrive data/missing.csv:
ogni riga = un FI/RCP non indicizzato (404, 500, errore, scansione senza OCR).
Stampa riepilogo: per motivo + farmaci totalmente assenti vs parziali.
"""
from __future__ import annotations
import csv
import sqlite3
from config import DB_PATH, DATA


def build():
    con = sqlite3.connect(DB_PATH)
    for col, typ in (("scanned", "INTEGER"), ("md_path", "TEXT")):
        try:
            con.execute(f"ALTER TABLE docs ADD COLUMN {col} {typ}")
        except sqlite3.OperationalError:
            pass  # gia' presente
    con.commit()
    con.row_factory = sqlite3.Row
    # doc mancante = non convertito a md (bad/error/scanned senza md)
    rows = con.execute("""
        SELECT d.cod_farmaco, d.tipo, d.status, d.http, d.scanned, d.url,
               c.DENOMINAZIONE AS denominazione, c.CODICE_ATC AS codice_atc,
               c.RAGIONE_SOCIALE AS ragione_sociale
        FROM docs d
        LEFT JOIN (SELECT DISTINCT COD_FARMACO, DENOMINAZIONE, CODICE_ATC, RAGIONE_SOCIALE
                   FROM conf GROUP BY COD_FARMACO) c
          ON c.COD_FARMACO = d.cod_farmaco
        WHERE d.md_path IS NULL
        ORDER BY d.http, d.cod_farmaco, d.tipo
    """).fetchall()

    out = DATA / "missing.csv"
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["cod_farmaco", "tipo", "denominazione", "codice_atc",
                    "ragione_sociale", "status", "http", "scanned", "motivo", "url"])
        for r in rows:
            motivo = {404: "documento inesistente", 500: "errore server"}.get(
                r["http"], "scansione senza OCR" if r["scanned"] else "errore rete")
            w.writerow([r["cod_farmaco"], r["tipo"], r["denominazione"], r["codice_atc"],
                        r["ragione_sociale"], r["status"], r["http"], r["scanned"],
                        motivo, r["url"]])

    # riepilogo
    by_http = dict(con.execute(
        "SELECT http, COUNT(*) FROM docs WHERE md_path IS NULL GROUP BY http").fetchall())
    tot_farm = con.execute("SELECT COUNT(DISTINCT cod_farmaco) FROM docs").fetchone()[0]
    # farmaci con ENTRAMBI FI+RCP mancanti
    full_miss = con.execute("""
        SELECT COUNT(*) FROM (
          SELECT cod_farmaco FROM docs GROUP BY cod_farmaco
          HAVING SUM(CASE WHEN md_path IS NOT NULL THEN 1 ELSE 0 END)=0)
    """).fetchone()[0]
    indexed = con.execute("SELECT COUNT(*) FROM docs WHERE md_path IS NOT NULL").fetchone()[0]
    con.close()

    print(f"documenti mancanti per http/motivo: {by_http}")
    print(f"documenti indicizzati (md ok): {indexed}")
    print(f"farmaci totali: {tot_farm} | completamente assenti (no FI e no RCP): {full_miss}")
    print(f"CSV mancanti -> {out} ({len(rows)} righe)")


if __name__ == "__main__":
    build()
