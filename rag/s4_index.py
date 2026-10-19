"""s4 - Index vettoriale.

md -> chunk per sezione -> embed bge-m3 (LOCALE) -> ChromaDB (persistente locale).

Chunking: segmenta per heading/sezione (RCP/FI numerati), poi spezza le sezioni
troppo lunghe con overlap. Ogni chunk porta metadati per filtro + join strutturato:
cod_farmaco, tipo, section, denominazione, codice_atc, ragione_sociale, forma.
"""
from __future__ import annotations
import re
import sqlite3
from pathlib import Path
import chromadb
from tqdm import tqdm
from sentence_transformers import SentenceTransformer
from config import (DB_PATH, CHROMA_DIR, CHROMA_COLLECTION, EMBED_MODEL,
                    EMBED_BATCH, CHUNK_MAX_CHARS, CHUNK_OVERLAP, pick_device)

# Sezioni AIFA = marker grassetto numerato INLINE: **4.3 Controindicazioni**, **7. TITOLARE...**
# Numero (1, 4.1, 6.6) + titolo che inizia maiuscolo, dentro ** ** (o ## md).
_SEC = re.compile(
    r"(?:\*\*|#{1,6}\s*)\s*(\d+(?:\.\d+)*)\.?\s+([A-ZÀ-Ù][^*\n]{2,90}?)\s*(?:\*\*|$)",
    re.M)


def _clean(s: str) -> str:
    s = re.sub(r"<[^>]+>", " ", s)          # tag html (<mark> ecc.)
    s = re.sub(r"[\*_`>#]+", " ", s)        # markup md
    return re.sub(r"\s+", " ", s).strip()


# boilerplate legale AIFA ripetuto in ogni doc -> rumore, via prima del chunking
_BOILER = [
    re.compile(r"Documento reso disponibile da AIFA(?:\s*il\s*\d{1,2}/\d{1,2}/\d{2,4})?", re.I),
    # frase legale completa: da "Esula..." fino a "titolare AIC)."
    re.compile(r"Esula dalla competenza dell['’]AIFA[\s\S]{0,800}?titolare AIC\)\.?", re.I),
    # frammento troncato ricorrente (fine pagina, senza chiusura)
    re.compile(r"Esula dalla competenza dell['’]AIFA ogni eventuale disputa[\s\S]{0,400}?"
               r"(?:dati relati\w*(?:[\s\S]{0,40}?medicinali)?|titolare AIC\)\.?|\Z)", re.I),
]


def strip_boilerplate(md: str) -> str:
    for rx in _BOILER:
        md = rx.sub(" ", md)
    return md


def split_sections(md: str):
    """Segmenta sui marker di sezione numerati. Ritorna [(titolo, testo)]."""
    md = strip_boilerplate(md)
    marks = [(m.start(), f"{m.group(1)} {_clean(m.group(2))}"[:100])
             for m in _SEC.finditer(md)]
    if len(marks) < 3:                       # doc senza struttura -> blocco unico
        return [("", md)]
    out = []
    # eventuale preambolo prima della 1a sezione
    if marks[0][0] > 0:
        out.append(("intestazione", md[:marks[0][0]].strip()))
    for i, (pos, title) in enumerate(marks):
        end = marks[i + 1][0] if i + 1 < len(marks) else len(md)
        body = md[pos:end].strip()
        if body:
            out.append((title, body))
    return out


def chunk_section(title: str, body: str):
    """Spezza una sezione lunga in finestre con overlap."""
    if len(body) <= CHUNK_MAX_CHARS:
        return [body]
    step = CHUNK_MAX_CHARS - CHUNK_OVERLAP
    return [body[i:i + CHUNK_MAX_CHARS] for i in range(0, len(body), step)]


def farm_meta(con):
    """{cod_farmaco: {denominazione, codice_atc, ragione_sociale, forma}} da conf."""
    rows = con.execute("""SELECT COD_FARMACO, DENOMINAZIONE, CODICE_ATC,
                                 RAGIONE_SOCIALE, FORMA FROM conf""").fetchall()
    d = {}
    for farm, den, atc, rag, forma in rows:
        d.setdefault(farm, {"denominazione": den or "", "codice_atc": atc or "",
                            "ragione_sociale": rag or "", "forma": forma or ""})
    return d


def build():
    con = sqlite3.connect(DB_PATH)
    meta = farm_meta(con)
    docs = con.execute(
        "SELECT cod_farmaco, tipo, md_path FROM docs WHERE md_path IS NOT NULL"
    ).fetchall()
    con.close()
    print(f"documenti md: {len(docs)}")

    dev = pick_device()
    print(f"carico modello {EMBED_MODEL} su device={dev} (prima volta scarica ~2GB)...")
    model = SentenceTransformer(EMBED_MODEL, device=dev)

    client = chromadb.PersistentClient(path=str(CHROMA_DIR))
    try:
        client.delete_collection(CHROMA_COLLECTION)
    except Exception:
        pass
    coll = client.create_collection(CHROMA_COLLECTION, metadata={"hnsw:space": "cosine"})

    ids, texts, embed_texts, metas = [], [], [], []
    for farm, tipo, md_path in tqdm(docs, desc="chunking", unit="doc"):
        md = Path(md_path).read_text(encoding="utf-8")
        fm = meta.get(farm, {})
        den = fm.get("denominazione", "")
        tipo_full = "Foglio Illustrativo" if tipo == "FI" else "Riassunto Caratteristiche Prodotto"
        n = 0
        for title, body in split_sections(md):
            for chunk in chunk_section(title, body):
                if len(chunk.strip()) < 40:
                    continue
                ids.append(f"{farm}_{tipo}_{n}")
                texts.append(chunk)                                  # raw: display + contesto LLM
                embed_texts.append(f"{den} — {tipo_full} — {title}: {chunk}")  # con contesto: si embedda
                metas.append({"cod_farmaco": farm, "tipo": tipo, "section": title, **fm})
                n += 1
    print(f"chunk totali: {len(texts)}")

    # embed (testo con contesto) + add a chroma (documento raw)
    for i in tqdm(range(0, len(texts), 256), desc="embed+index", unit="batch"):
        sl = slice(i, i + 256)
        emb = model.encode(embed_texts[sl], batch_size=EMBED_BATCH,
                           normalize_embeddings=True, show_progress_bar=False)
        coll.add(ids=ids[sl], documents=texts[sl], metadatas=metas[sl],
                 embeddings=emb.tolist())
    print(f"\nChroma pronto: {CHROMA_DIR} | collection '{CHROMA_COLLECTION}' count={coll.count()}")


if __name__ == "__main__":
    build()
