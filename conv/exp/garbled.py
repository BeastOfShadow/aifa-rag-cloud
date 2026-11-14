"""PDF con il testo nativo illeggibile (font senza mappa Unicode): quota di caratteri di controllo e di parole che non
sembrano italiano (nessuna vocale), per documento. Per questi il testo nativo non è un riferimento e serve l'OCR
sull'immagine. Uso: .venv/bin/python conv/exp/garbled.py <cartella PDF> <uscita.csv>"""
import csv
import re
import sys
from multiprocessing import Pool
from pathlib import Path

import pymupdf


def scan(pdf):
    try:
        t = "\n".join(p.get_text() for p in pymupdf.open(pdf))
    except Exception as e:
        return pdf.stem, -1, -1, 0
    ctrl = sum(1 for c in t if ord(c) < 32 and c not in "\n\r\t") + t.count("�")
    toks = re.findall(r"[^\W\d_]{4,}", t)
    novowel = sum(1 for w in toks if not re.search(r"[aeiouàèéìòùAEIOU]", w))
    return pdf.stem, ctrl / max(len(t), 1), novowel / max(len(toks), 1), len(t)


if __name__ == "__main__":
    with Pool(48) as p:
        rows = p.map(scan, sorted(Path(sys.argv[1]).glob("*.pdf")), chunksize=20)
    with open(sys.argv[2], "w", newline="") as f:
        w = csv.writer(f); w.writerow(["doc", "ctrl_ratio", "novowel_ratio", "chars"]); w.writerows(rows)
    bad = [r for r in rows if r[1] > 0.01]
    print(f"{len(bad)} documenti su {len(rows)} con più dell'1% di caratteri di controllo")
