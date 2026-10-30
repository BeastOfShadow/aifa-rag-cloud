"""Grafici dei benchmark da results/all.csv, costs.csv, costs_split.csv -> docs/charts/*.png
Uso: .venv/bin/python bench/charts.py   (dopo collect.py e cost_model.py)"""
from pathlib import Path
import pandas as pd
import matplotlib
matplotlib.use("Agg")                       # solo PNG, nessuna finestra
import matplotlib.pyplot as plt
from matplotlib.patches import Patch

plt.rcParams.update({"font.size": 11, "figure.dpi": 130, "axes.spines.top": False,
                     "axes.spines.right": False, "axes.grid": True, "grid.alpha": 0.25})
# palette categoriale validata (ordine fisso, mai ciclato); testo sempre in inchiostro, mai nel colore della serie
SERIES_COLORS = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100"]
INK, INK_2, GRAY = "#0b0b0b", "#52514e", "#8a8984"

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "results"
CHARTS = ROOT / "docs" / "charts"

runs = pd.read_csv(RESULTS / "all.csv")


# ---- 1) s3: quante volte più veloce con p worker (assi lineari) ----
SCALING = {  # config -> etichetta, posizione dell'etichetta rispetto all'ultimo punto; l'ordine fissa il colore
    "local-cpu-ocr": ("Mac M4 (10 cores)", (8, 6), "left"),
    "cloud-cpu-s": ("VM 8 vCPU Haswell", (8, -16), "left"),
    "cloud-cpu-l": ("VM 16 vCPU Haswell", (8, -16), "left"),
    "cloud-gpu": ("VM 48 vCPU Sapphire Rapids", (-6, 10), "right"),
}
s3 = runs[runs["phase"].eq("s3") & runs["workers"].ne("all") & runs["config"].isin(SCALING)]
s3 = s3.assign(workers=s3["workers"].astype(int)).groupby(["config", "workers"])["seconds"].median().reset_index()
s3["speedup"] = s3["config"].map(s3[s3["workers"].eq(1)].set_index("config")["seconds"]) / s3["seconds"]

fig, ax = plt.subplots(figsize=(7.5, 5))
ax.plot([1, 48], [1, 48], "--", color=GRAY, lw=1)
ax.text(30, 33, "ideal: p workers = p times faster", color=INK_2, fontsize=9, rotation=33)
for (config, (label, offset, ha)), color, marker in zip(SCALING.items(), SERIES_COLORS, "osD^"):
    d = s3[s3["config"].eq(config)]
    # le due VM Haswell coincidono: la seconda tratteggiata, così si vedono entrambe
    ax.plot(d["workers"], d["speedup"], marker=marker, color=color, lw=2, ms=7, label=label,
            ls="--" if config == "cloud-cpu-l" else "-")
    last = d.iloc[-1]
    ax.annotate(label, (last["workers"], last["speedup"]), xytext=offset, textcoords="offset points",
                ha=ha, color=INK_2, fontsize=9)
ax.set_xticks([1, 4, 8, 16, 32, 48])
ax.set_yticks([1, 8, 16, 24, 32, 40, 48])
ax.set_xlabel("workers (parallel processes)")
ax.set_ylabel("speedup over 1 worker")
ax.set_title("s3 — speedup with more workers (sample, 972 documents)", fontweight="bold", loc="left")
ax.legend(fontsize=9, frameon=False, loc="upper left")
fig.tight_layout()
fig.savefig(CHARTS / "s3_scaling.png")
plt.close(fig)


# ---- 2) s4 sulla H100: tempo per dimensione del batch (campione) ----
s4 = runs[runs["config"].eq("cloud-gpu") & runs["sample"].eq("n500") & runs["phase"].eq("s4")]
s4 = s4.groupby("embed_batch")["seconds"].median()

fig, ax = plt.subplots(figsize=(6.4, 4.2))
bars = ax.bar([str(b) for b in s4.index], s4.values, color=SERIES_COLORS[0], width=0.6, zorder=2)
ax.grid(axis="x", visible=False)
for b, v in zip(bars, s4.values):
    ax.text(b.get_x() + b.get_width() / 2, v, f"{v:.0f} s", ha="center", va="bottom", color=INK)
ax.margins(y=0.15)
ax.set_xlabel("model batch size (texts per GPU pass)")
ax.set_ylabel("seconds")
ax.set_title("s4 on H100 — 31,365 chunks of the sample", fontweight="bold", loc="left")
fig.tight_layout()
fig.savefig(CHARTS / "s4_batch.png")
plt.close(fig)


# ---- 3) costo di s3 + s4 per configurazione e fornitore: barre ordinate, € e ore scritti ----
NAMES = {"local-cpu": "Mac CPU", "local-mps": "Mac MPS", "cloud-cpu-s": "VM 8 vCPU",
         "cloud-cpu-l": "VM 16 vCPU", "cloud-gpu": "VM H100"}
KINDS = dict(zip(["local (Mac)", "cloud, single VM", "cloud, split (2 VMs)"], SERIES_COLORS))   # max 3 categorie
costs = pd.read_csv(RESULTS / "costs.csv")
costs = costs[costs["embed_batch"].eq(64)]          # per la GPU solo il batch scelto
costs["kind"] = costs["config"].map(lambda c: "local (Mac)" if c.startswith("local") else "cloud, single VM")
costs["name"] = costs["config"].map(NAMES) + costs["provider"].map(lambda p: "" if p == "locale" else f" · {p}")
split = pd.read_csv(RESULTS / "costs_split.csv")
split["kind"] = "cloud, split (2 VMs)"
split["name"] = "split " + split["config"].map(NAMES) + " + H100 · " + split["provider"]
bars = pd.concat([costs, split])[["kind", "name", "sample", "n_docs", "hours", "cost_eur"]]

TITLES = {"n500": "Sample", "full": "Full corpus"}
sizes = [bars["sample"].eq(s).sum() for s in TITLES]
fig, axes = plt.subplots(2, 1, figsize=(8.5, 0.34 * sum(sizes) + 2.4), gridspec_kw={"height_ratios": sizes})
for ax, (sample, title) in zip(axes, TITLES.items()):
    d = bars[bars["sample"].eq(sample)].sort_values("cost_eur", ascending=False)   # il più economico in alto
    ax.barh(d["name"], d["cost_eur"], color=d["kind"].map(KINDS), height=0.6, zorder=2)
    for y, r in enumerate(d.itertuples()):
        h = format(r.hours, ".2f" if r.hours < 1 else ".1f")
        ax.text(r.cost_eur, y, f"  €{r.cost_eur:.2f} · {h} h", va="center", color=INK, fontsize=9)
    ax.grid(axis="y", visible=False)
    ax.margins(x=0.28)
    ax.tick_params(axis="y", length=0)
    ax.set_title(f"{title} ({int(d['n_docs'].iloc[0]):,} documents)", fontweight="bold", loc="left")
axes[-1].set_xlabel("s3 + s4 cost in €, overhead included (label: cost · billed hours)")
axes[0].legend(handles=[Patch(color=c, label=k) for k, c in KINDS.items()], fontsize=9, frameon=False,
               loc="upper right")
fig.tight_layout()
fig.savefig(CHARTS / "costs.png")
plt.close(fig)

print("grafici in", CHARTS)
