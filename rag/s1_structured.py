"""s1 - Layer strutturato: 3 CSV -> SQLite.

Query esatte/join deterministici (ATC, ditta, principio attivo, fornitura).
NON e' RAG: qui niente embeddings. SQL puro.

Chiavi:
  conf.CODICE_AIC  <-> pa.CODICE_AIC     (principi attivi per confezione)
  conf.CODICE_ATC  <-> atc.CODICE_ATC    (classificazione ATC, 100% match)
  conf.COD_FARMACO -> identifica il PDF FI/RCP (un doc per farmaco)
"""
from __future__ import annotations
import sqlite3
import pandas as pd
from config import DATASETS, DB_PATH


def load() -> dict[str, pd.DataFrame]:
    read = lambda name: pd.read_csv(DATASETS / name, sep=";", dtype=str)
    return {
        "atc": read("atc.csv"),
        "pa": read("PA_confezioni.csv"),
        "conf": read("confezioni_fornitura.csv"),
    }


def build(db_path=DB_PATH) -> None:
    dfs = load()
    con = sqlite3.connect(db_path)
    try:
        for name, df in dfs.items():
            df.to_sql(name, con, if_exists="replace", index=False)
            print(f"  {name:5} -> {len(df):>7} righe, {len(df.columns)} col")
        # indici sulle chiavi di join / filtri frequenti
        cur = con.cursor()
        for stmt in (
            "CREATE INDEX IF NOT EXISTS ix_conf_aic  ON conf(CODICE_AIC)",
            "CREATE INDEX IF NOT EXISTS ix_conf_atc  ON conf(CODICE_ATC)",
            "CREATE INDEX IF NOT EXISTS ix_conf_farm ON conf(COD_FARMACO)",
            "CREATE INDEX IF NOT EXISTS ix_pa_aic    ON pa(CODICE_AIC)",
            "CREATE INDEX IF NOT EXISTS ix_atc_code  ON atc(CODICE_ATC)",
        ):
            cur.execute(stmt)
        con.commit()
        print(f"\nDB pronto: {db_path}")
    finally:
        con.close()


if __name__ == "__main__":
    build()
