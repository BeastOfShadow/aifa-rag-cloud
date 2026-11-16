from pathlib import Path
import pandas as pd

# ---- percorsi ----
ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "results"
BENCH = ROOT / "bench"
ALL_COLS = ["run_id", "config", "sample", "n_docs", "n_chunks", "commit", "host",
    "cpus", "workers", "device", "embed_batch", "phase", "seconds"]
PRICES_COLS = ["config", "provider", "eur_h", "overhead_h", "overhead_v2_h"]

all_run = RESULTS/"all.csv"
prices = BENCH/"prices.csv"

df1 = pd.read_csv(all_run)
df1 = df1[~df1["config"].str.startswith("emb-")]   # confronto tra embedder (§7.9): non ha un costo da stimare
df2 = pd.read_csv(prices)[PRICES_COLS]

# due tornate di misure (§4.4 della relazione): la 2 ha gli ambienti allineati (config -v2) e la split eseguita
df1["tornata"] = (df1["config"].str.endswith("-v2") | df1["config"].str.startswith("split-")).map({True: 2, False: 1})
df1["config"] = df1["config"].str.replace(r'(-ocr|-full|-v2)$', '', regex=True)

with_s4 = df1.loc[df1["phase"] == "s4", "run_id"]
full = df1[df1["run_id"].isin(with_s4)]
runs = full.groupby(['tornata', 'run_id', 'config', 'sample', 'n_docs', 'workers', 'device', 'embed_batch'])['seconds'].sum().reset_index()

median_runs = runs.groupby(['tornata', 'config', 'sample', 'n_docs', 'workers', 'device', 'embed_batch'])['seconds'].median().reset_index()

# il full run monolitico l'ho eseguito solo nella prima tornata: nella seconda lo riuso con l'overhead della seconda
# (s4 sul corpus completo cambia poco: 9833 s nella prima, 9756 s in split-gpu nella seconda)
mono_full = median_runs[median_runs["config"].eq("cloud-gpu") & median_runs["sample"].eq("full")]
median_runs = pd.concat([median_runs, mono_full.assign(tornata=2)])

# s1 + s2 sono misurati solo sul corpus completo (full_s1s2.csv): variante "s1-s4" del monolitico, download sulla VM GPU
s12 = lambda config: pd.read_csv(RESULTS / config / "full_s1s2.csv")["seconds"].sum()
median_runs["scope"] = "s3+s4"
mono_s2 = median_runs[median_runs["config"].eq("cloud-gpu") & median_runs["sample"].eq("full")]
median_runs = pd.concat([median_runs, mono_s2.assign(scope="s1-s4", seconds=mono_s2["seconds"] + s12("cloud-gpu-full"))])

costs = median_runs.merge(df2, on="config")
# overhead (make up + make deploy) misurato in ciascuna tornata
costs['overhead_h'] = costs['overhead_h'].where(costs['tornata'].eq(1), costs['overhead_v2_h'])
costs['hours'] = costs['seconds'] / 3600 + costs['overhead_h']
costs['cost_eur'] = costs['hours'] * costs['eur_h']
costs['cost_1000_docs'] = costs['cost_eur'] / costs['n_docs'] * 1000
costs = costs.drop(columns="overhead_v2_h")

print(costs.sort_values(['tornata', 'scope', 'cost_eur']).to_string(index=False))

costs.to_csv(RESULTS / "costs.csv", index=False)

# ---- configurazione eterogenea (split): s3 su una VM solo CPU, s4 sulla VM GPU ----
# Prima tornata: composta dai tempi per fase. Seconda: eseguita sul corpus completo con 16 vCPU (split-cpu,
# make fetch-md e push-md attraverso il Mac, split-gpu), composta per il campione e per la VM da 8 vCPU.
infra = pd.read_csv(RESULTS / "infra_times.csv").set_index("target")["seconds"]   # fetch-md e push-md: una riga


def median(tornata, config, sample, phase):
    sel = df1["tornata"].eq(tornata) & df1["config"].eq(config) & df1["sample"].eq(sample) & df1["phase"].eq(phase)
    if config == "cloud-gpu":   # run completi s3+s4 sulla VM GPU: tutti i core per s3, batch 64 per s4
        sel &= df1["workers"].eq("all") & df1["embed_batch"].eq(64)
    return df1.loc[sel, "seconds"].median()


rows = []
for t in (1, 2):
    # s3 migliore per ogni VM solo CPU: numero di worker più veloce nella serie di scaling, mediana delle ripetizioni
    s3 = df1[df1["tornata"].eq(t) & df1["config"].str.startswith("cloud-cpu") & df1["phase"].eq("s3") & df1["workers"].ne("all")]
    best = (s3.groupby(["config", "workers"])["seconds"].median().reset_index()
              .sort_values("seconds").drop_duplicates("config").set_index("config"))
    for cpu, b in best.iterrows():
        base = dict(tornata=t, config=cpu, workers=b["workers"], scope="s3+s4", s12_seconds=0, move_cpu_seconds=0,
                    move_gpu_seconds=0, measured=False)
        rows.append({**base, "sample": "n500", "s3_seconds": b["seconds"], "s4_seconds": median(t, "cloud-gpu", "n500", "s4")})
        if t == 1:
            # s3 sul corpus completo non misurato: rapporto corpus/campione di s3 sulla VM GPU (2109 / 91)
            s3_full = b["seconds"] * median(1, "cloud-gpu", "full", "s3") / median(1, "cloud-gpu", "n500", "s3")
            rows.append({**base, "sample": "full", "s3_seconds": s3_full, "s4_seconds": median(1, "cloud-gpu", "full", "s4")})
            # variante con s1 + s2 sulla VM CPU, con la durata misurata sulla VM GPU (dipende dalla rete)
            if cpu == "cloud-cpu-l":
                rows.append({**rows[-1], "scope": "s1-s4", "s12_seconds": s12("cloud-gpu-full")})
        else:
            # eseguita con 16 vCPU; con 8 vCPU s3 si scala con il rapporto corpus/campione della stessa CPU (9426 / 326)
            s3_full = median(2, "split-cpu", "full", "s3")
            measured = cpu == "cloud-cpu-l"
            rows.append({**base, "sample": "full", "measured": measured,
                         "s3_seconds": s3_full if measured else b["seconds"] * s3_full / best.loc["cloud-cpu-l", "seconds"],
                         "move_cpu_seconds": infra["fetch-md"], "move_gpu_seconds": infra["push-md"],
                         "s4_seconds": median(2, "split-gpu", "full", "s4")})
            if measured:
                rows.append({**rows[-1], "scope": "s1-s4", "s12_seconds": s12("split-cpu")})
cases = pd.DataFrame(rows)

# prezzi: VM CPU per config, VM GPU dello stesso fornitore (restano Azure e AWS, gli unici con entrambe)
gpu_prices = df2[df2["config"].eq("cloud-gpu")].drop(columns="config")
split = cases.merge(df2, on="config").merge(gpu_prices, on="provider", suffixes=("_cpu", "_gpu"))
for vm in ("cpu", "gpu"):
    split[f"overhead_h_{vm}"] = split[f"overhead_h_{vm}"].where(split["tornata"].eq(1), split[f"overhead_v2_h_{vm}"])
split = split.drop(columns=["overhead_v2_h_cpu", "overhead_v2_h_gpu"])

# due VM in sequenza: ognuna paga il suo lavoro, la sua parte di trasferimento e il suo overhead (provisioning + deploy)
cpu_h = (split["s12_seconds"] + split["s3_seconds"] + split["move_cpu_seconds"]) / 3600 + split["overhead_h_cpu"]
gpu_h = (split["move_gpu_seconds"] + split["s4_seconds"]) / 3600 + split["overhead_h_gpu"]
split["hours"] = cpu_h + gpu_h
split["cost_eur"] = cpu_h * split["eur_h_cpu"] + gpu_h * split["eur_h_gpu"]
split = split.merge(df1.groupby(["tornata", "sample"])["n_docs"].max().reset_index(), on=["tornata", "sample"])
split["cost_1000_docs"] = split["cost_eur"] / split["n_docs"] * 1000

split.to_csv(RESULTS / "costs_split.csv", index=False)
print(split.sort_values(["tornata", "scope", "sample", "cost_eur"]).round(3).to_string(index=False))
