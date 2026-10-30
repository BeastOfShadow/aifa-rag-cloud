from pathlib import Path
import pandas as pd
import os
import sqlite3
import re

# ---- percorsi ----
ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "results"
COLS = ["run_id", "config", "sample", "n_docs", "n_chunks", "commit", "host",
    "cpus", "workers", "device", "embed_batch", "phase", "seconds"]

percorsi = Path(RESULTS).glob('*/*/phases.csv')
frames = []

for percorso in percorsi:
    df = pd.read_csv(percorso)
    run_dir = percorso.parent

    if not df.empty:
        df["sample"] = Path(os.readlink(run_dir / "data/pdf")).parent.name
        con = sqlite3.connect(run_dir / "data/farmaci.sqlite")
        df["n_docs"] = con.execute("SELECT COUNT(*) FROM docs WHERE md_path IS NOT NULL").fetchone()[0]
        con.close()
        log = run_dir / "s4.log"
        m = re.search(r"chunk totali: (\d+)", log.read_text()) if log.exists() else None
        df["n_chunks"] = int(m.group(1)) if m else None
        frames.append(df)

all_df = pd.concat(frames)[COLS]
all_df.to_csv(RESULTS / "all.csv", index=False)
print(f"{len(frames)} run, {len(all_df)} righe -> {RESULTS / 'all.csv'}")
