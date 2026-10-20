"""s2 - Download PDF (pilot).

Un farmaco (COD_FARMACO) = 1 FI + 1 RCP. Dedup: 159k righe -> 12.599 farmaci.
Pilot: primi PILOT_N farmaci.

- async httpx, concorrenza limitata (gentile con AIFA)
- cache: salta file gia' scaricati (resumable)
- retry con backoff
- manifest in tabella sqlite `docs` (stato, http, bytes) per tracciare
"""
from __future__ import annotations
import asyncio
import sqlite3
import httpx
from tqdm import tqdm
from config import (DB_PATH, PDF_DIR, MD_DIR, PILOT_N, HTTP_TIMEOUT,
                    HTTP_CONCURRENCY, HTTP_RETRIES, USER_AGENT)


def pilot_docs(limit=PILOT_N) -> list[tuple[str, str, str]]:
    """Ritorna [(cod_farmaco, tipo, url)] per FI e RCP dei primi `limit` farmaci."""
    con = sqlite3.connect(DB_PATH)
    # ORDER BY: stesso campione a ogni run (benchmark confrontabili)
    q = ("SELECT DISTINCT COD_FARMACO, LINK_FI, LINK_RCP FROM conf WHERE COD_FARMACO IS NOT NULL"
         " ORDER BY COD_FARMACO, LINK_FI, LINK_RCP")
    if limit:
        q += f" LIMIT {int(limit)}"
    rows = con.execute(q).fetchall()
    con.close()
    docs = []
    for farm, fi, rcp in rows:
        if fi and fi.strip():
            docs.append((farm, "FI", fi.strip()))
        if rcp and rcp.strip():
            docs.append((farm, "RCP", rcp.strip()))
    return docs


def _init_manifest(con):
    con.execute("""CREATE TABLE IF NOT EXISTS docs(
        cod_farmaco TEXT, tipo TEXT, url TEXT, path TEXT,
        status TEXT, http INTEGER, bytes INTEGER,
        PRIMARY KEY (cod_farmaco, tipo))""")
    con.commit()


async def _fetch(client, sem, farm, tipo, url):
    path = PDF_DIR / f"{farm}_{tipo}.pdf"
    md_path = MD_DIR / f"{farm}_{tipo}.md"
    if md_path.exists():        # md gia' fatto (PDF magari cancellato) -> non riscaricare
        return (farm, tipo, url, str(path), "cached", 200, 0)
    if path.exists() and path.stat().st_size > 0:
        return (farm, tipo, url, str(path), "cached", 200, path.stat().st_size)
    async with sem:
        for attempt in range(1, HTTP_RETRIES + 1):
            try:
                r = await client.get(url)
                if r.status_code == 200 and r.content[:4] == b"%PDF":
                    path.write_bytes(r.content)
                    return (farm, tipo, url, str(path), "ok", 200, len(r.content))
                if attempt == HTTP_RETRIES:
                    return (farm, tipo, url, "", "bad", r.status_code, 0)
            except Exception:
                if attempt == HTTP_RETRIES:
                    return (farm, tipo, url, "", "error", 0, 0)
            await asyncio.sleep(2 * attempt)  # backoff


async def run(limit=PILOT_N):
    docs = pilot_docs(limit)
    print(f"da scaricare: {len(docs)} PDF ({len({d[0] for d in docs})} farmaci)")
    sem = asyncio.Semaphore(HTTP_CONCURRENCY)
    headers = {"User-Agent": USER_AGENT}
    con = sqlite3.connect(DB_PATH)
    _init_manifest(con)
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT, headers=headers,
                                 follow_redirects=True) as client:
        tasks = [_fetch(client, sem, f, t, u) for f, t, u in docs]
        done = 0
        for coro in tqdm(asyncio.as_completed(tasks), total=len(tasks),
                         desc="download", unit="pdf"):
            row = await coro
            con.execute("INSERT OR REPLACE INTO docs VALUES (?,?,?,?,?,?,?)", row)
            done += 1
            if done % 25 == 0:
                con.commit()
    con.commit()
    # riepilogo
    stats = con.execute("SELECT status, COUNT(*) FROM docs GROUP BY status").fetchall()
    con.close()
    print("riepilogo:", dict(stats))


if __name__ == "__main__":
    asyncio.run(run())
