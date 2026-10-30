from pathlib import Path
import pandas as pd

# ---- percorsi ----
ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "results"
BENCH = ROOT / "bench"
ALL_COLS = ["run_id", "config", "sample", "n_docs", "n_chunks", "commit", "host",
    "cpus", "workers", "device", "embed_batch", "phase", "seconds"]
PRICES_COLS = ["config", "provider", "eur_h", "overhead_h"]

all_run = RESULTS/"all.csv"
prices = BENCH/"prices.csv"

df1 = pd.read_csv(all_run)
df2 = pd.read_csv(prices)[PRICES_COLS]

df1["config"] = df1["config"].str.replace(r'(-ocr|-full)$', '', regex=True)

with_s4 = df1.loc[df1["phase"] == "s4", "run_id"]
full = df1[df1["run_id"].isin(with_s4)]
runs = full.groupby(['run_id', 'config', 'sample', 'n_docs', 'workers', 'device', 'embed_batch'])['seconds'].sum().reset_index()

median_runs = runs.groupby(['config', 'sample', 'n_docs', 'workers', 'device', 'embed_batch'])['seconds'].median().reset_index()

costs = median_runs.merge(df2, on="config")
costs['hours'] = costs['seconds'] / 3600 + costs['overhead_h']
costs['cost_eur'] = costs['hours'] * costs['eur_h']
costs['cost_1000_docs'] = costs['cost_eur'] / costs['n_docs'] * 1000

print(costs.sort_values(['cost_eur']).to_string(index=False))

costs.to_csv(RESULTS / "costs.csv", index=False)

# ---- configurazione eterogenea (split): s3 su una VM solo CPU, s4 sulla VM GPU ----
# Composta dai tempi per fase, non eseguita. Vale per ogni VM cloud-cpu-* presente nei dati,
# ciascuna con il suo numero di worker più veloce (serie di scaling, mediana delle ripetizioni).

# s3 migliore per ogni VM solo CPU: config, workers, seconds
s3 = df1[df1["config"].str.startswith("cloud-cpu") & df1["phase"].eq("s3") & df1["workers"].ne("all")]
s3_best = (s3.groupby(["config", "workers"])["seconds"].median().reset_index()
             .sort_values("seconds").drop_duplicates("config"))

def gpu_seconds(sample, phase):
    # run completi s3+s4 sulla VM GPU: tutti i core per s3, batch 64 per s4
    sel = (df1["config"].eq("cloud-gpu") & df1["sample"].eq(sample) & df1["phase"].eq(phase)
           & df1["workers"].eq("all") & df1["embed_batch"].eq(64))
    return df1.loc[sel, "seconds"].median()

# s3 della VM CPU sul corpus completo non è misurato: si scala con il rapporto corpus/campione
# misurato per s3 sulla VM GPU (2109 / 91), ipotizzando lo stesso rapporto su tutte le CPU
s3_scale = gpu_seconds("full", "s3") / gpu_seconds("n500", "s3")
cases = pd.concat([
    s3_best.assign(sample=sample, s3_seconds=s3_best["seconds"] * factor, s4_seconds=gpu_seconds(sample, "s4"))
    for sample, factor in [("n500", 1), ("full", s3_scale)]
]).drop(columns="seconds")

# prezzi: VM CPU per config, VM GPU dello stesso fornitore (restano Azure e AWS, gli unici con entrambe)
gpu_prices = df2[df2["config"].eq("cloud-gpu")].drop(columns="config")
split = cases.merge(df2, on="config").merge(gpu_prices, on="provider", suffixes=("_cpu", "_gpu"))

# due VM in sequenza: ognuna paga il suo lavoro più il suo overhead (provisioning + deploy)
split["hours"] = (split["s3_seconds"] + split["s4_seconds"]) / 3600 + split["overhead_h_cpu"] + split["overhead_h_gpu"]
split["cost_eur"] = ((split["s3_seconds"] / 3600 + split["overhead_h_cpu"]) * split["eur_h_cpu"]
                     + (split["s4_seconds"] / 3600 + split["overhead_h_gpu"]) * split["eur_h_gpu"])
split["n_docs"] = split["sample"].map(df1.groupby("sample")["n_docs"].max())
split["cost_1000_docs"] = split["cost_eur"] / split["n_docs"] * 1000

split.to_csv(RESULTS / "costs_split.csv", index=False)
print(split.sort_values(["sample", "cost_eur"]).round(3).to_string(index=False))