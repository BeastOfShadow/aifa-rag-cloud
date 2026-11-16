"""Grafici dei benchmark da results/all.csv, costs.csv, costs_split.csv -> docs/charts/*.png
Prima tornata: s3_scaling.png, s4_batch.png, costs.png; seconda (ambienti allineati): stessi nomi con _v2;
confronto tra le tornate: rounds_times.png, rounds_costs.png.
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
ROUNDS = {1: ("round 1", "#b9b7b0"), 2: ("round 2 (aligned environments)", SERIES_COLORS[0])}  # la 1 fa da riferimento

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "results"
CHARTS = ROOT / "docs" / "charts"

runs = pd.read_csv(RESULTS / "all.csv")


def save(fig, name):
    fig.tight_layout()
    fig.savefig(CHARTS / name)
    plt.close(fig)


# ---- 1) s3: quante volte più veloce con p worker (assi lineari) ----
def s3_scaling(scaling, name, reference=None):
    """scaling: config -> etichetta, posizione dell'etichetta, allineamento; l'ordine fissa il colore.
    reference: (config, etichetta) di una serie da mostrare in grigio, per confronto."""
    configs = list(scaling) + ([reference[0]] if reference else [])
    s3 = runs[runs["phase"].eq("s3") & runs["workers"].ne("all") & runs["config"].isin(configs)]
    s3 = s3.assign(workers=s3["workers"].astype(int)).groupby(["config", "workers"])["seconds"].median().reset_index()
    s3["speedup"] = s3["config"].map(s3[s3["workers"].eq(1)].set_index("config")["seconds"]) / s3["seconds"]

    fig, ax = plt.subplots(figsize=(7.5, 5))
    ax.plot([1, 48], [1, 48], "--", color=GRAY, lw=1)
    ax.text(30, 33, "ideal: p workers = p times faster", color=INK_2, fontsize=9, rotation=33)
    if reference:
        d = s3[s3["config"].eq(reference[0])]
        ax.plot(d["workers"], d["speedup"], ":", marker="o", color=GRAY, lw=1.5, ms=5, label=reference[1])
    for (config, (label, offset, ha)), color, marker in zip(scaling.items(), SERIES_COLORS, "osD^"):
        d = s3[s3["config"].eq(config)]
        # le due VM Haswell coincidono: la seconda tratteggiata, così si vedono entrambe
        ax.plot(d["workers"], d["speedup"], marker=marker, color=color, lw=2, ms=7, label=label,
                ls="--" if config.startswith("cloud-cpu-l") else "-")
        last = d.iloc[-1]
        ax.annotate(label, (last["workers"], last["speedup"]), xytext=offset, textcoords="offset points",
                    ha=ha, color=INK_2, fontsize=9)
    ax.set_xticks([1, 4, 8, 16, 32, 48])
    ax.set_yticks([1, 8, 16, 24, 32, 40, 48])
    ax.set_xlabel("workers (parallel processes)")
    ax.set_ylabel("speedup over 1 worker")
    ax.set_title("s3 — speedup with more workers (sample, 972 documents)", fontweight="bold", loc="left")
    ax.legend(fontsize=9, frameon=False, loc="upper left")
    save(fig, name)


VMS = {"cloud-cpu-s": ("VM 8 vCPU Haswell", (8, -16), "left"),
       "cloud-cpu-l": ("VM 16 vCPU Haswell", (8, -16), "left"),
       "cloud-gpu": ("VM 48 vCPU Sapphire Rapids", (-6, 10), "right")}
s3_scaling({"local-cpu-ocr": ("Mac M4 (10 cores)", (8, 6), "left"), **VMS}, "s3_scaling.png")
s3_scaling({"local-cpu-ocr-1t": ("Mac M4 (10 cores)", (8, -4), "left"),
            **{f"{c}-v2": v for c, v in VMS.items()}}, "s3_scaling_v2.png",
           reference=("local-cpu-ocr", "Mac M4, round 1 (before the s3 fix)"))


# ---- 2) s4 sulla H100: tempo per dimensione del batch (campione) ----
def s4_batch(config, name):
    s4 = runs[runs["config"].eq(config) & runs["sample"].eq("n500") & runs["phase"].eq("s4")]
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
    save(fig, name)


s4_batch("cloud-gpu", "s4_batch.png")
s4_batch("cloud-gpu-v2", "s4_batch_v2.png")


# ---- 3) costo di s3 + s4 per configurazione e fornitore: barre ordinate, € e ore scritti ----
NAMES = {"local-cpu": "Mac CPU", "local-mps": "Mac MPS", "cloud-cpu-s": "VM 8 vCPU",
         "cloud-cpu-l": "VM 16 vCPU", "cloud-gpu": "VM H100"}
KINDS = dict(zip(["local (Mac)", "cloud, single VM", "cloud, split (2 VMs)"], SERIES_COLORS))   # max 3 categorie
costs = pd.read_csv(RESULTS / "costs.csv")
costs = costs[costs["embed_batch"].eq(64) & costs["scope"].eq("s3+s4")]          # per la GPU solo il batch scelto
costs["kind"] = costs["config"].map(lambda c: "local (Mac)" if c.startswith("local") else "cloud, single VM")
costs["name"] = costs["config"].map(NAMES) + costs["provider"].map(lambda p: "" if p == "locale" else f" · {p}")
split = pd.read_csv(RESULTS / "costs_split.csv")
split = split[split["scope"].eq("s3+s4")]
split["kind"] = "cloud, split (2 VMs)"
split["name"] = ("split " + split["config"].map(NAMES) + " + H100 · " + split["provider"]
                 + split["measured"].map({True: " (run)", False: ""}))
bars = pd.concat([costs, split])[["tornata", "kind", "config", "provider", "name", "sample", "n_docs", "hours", "cost_eur"]]

TITLES = {"n500": "Sample", "full": "Full corpus"}


def costs_chart(tornata, name):
    b = bars[bars["tornata"].eq(tornata)]
    sizes = [b["sample"].eq(s).sum() for s in TITLES]
    fig, axes = plt.subplots(2, 1, figsize=(8.5, 0.34 * sum(sizes) + 2.4), gridspec_kw={"height_ratios": sizes})
    for ax, (sample, title) in zip(axes, TITLES.items()):
        d = b[b["sample"].eq(sample)].sort_values("cost_eur", ascending=False)   # il più economico in alto
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
    save(fig, name)


costs_chart(1, "costs.png")
costs_chart(2, "costs_v2.png")


# ---- 4) confronto tra le tornate: barre affiancate, prima tornata in grigio ----
def rounds_panel(ax, values, title, unit, log=False, fmt="{:,.0f}"):
    """values: DataFrame con indice = etichetta e colonne 1, 2 (le tornate); la prima riga in alto."""
    y = range(len(values))
    for i, (t, (label, color)) in enumerate(ROUNDS.items()):
        pos = [k + (0.2 if i == 0 else -0.2) for k in y]
        ax.barh(pos, values[t], height=0.38, color=color, label=label, zorder=2)
        for p, v in zip(pos, values[t]):
            ax.text(v, p, " " + fmt.format(v) + unit, va="center", color=INK, fontsize=8)
    ax.set_yticks(list(y), values.index)
    ax.invert_yaxis()
    ax.grid(axis="y", visible=False)
    ax.tick_params(axis="y", length=0)
    if log:
        ax.set_xscale("log")
    ax.margins(x=0.25)
    ax.set_title(title, fontweight="bold", loc="left", fontsize=11)


def median_s(config, phase, workers=None, batch=64):
    d = runs[runs["config"].eq(config) & runs["sample"].eq("n500") & runs["phase"].eq(phase)]
    d = d[d["embed_batch"].eq(batch)] if phase == "s4" else d
    return d[d["workers"].eq(workers)]["seconds"].median() if workers else d["seconds"].median()


S3 = {"Mac M4, 10 workers": ("local-cpu-ocr", "local-cpu-ocr-1t", "10"),
      "VM 8 vCPU, 8 workers": ("cloud-cpu-s", "cloud-cpu-s-v2", "8"),
      "VM 16 vCPU, 16 workers": ("cloud-cpu-l", "cloud-cpu-l-v2", "16"),
      "VM 48 vCPU, 48 workers": ("cloud-gpu", "cloud-gpu-v2", "48")}
S4 = {"Mac CPU": "local-cpu", "Mac GPU (MPS)": "local-mps", "VM 8 vCPU": "cloud-cpu-s",
      "VM 16 vCPU": "cloud-cpu-l", "H100": "cloud-gpu"}
s3_times = pd.DataFrame({k: {1: median_s(a, "s3", w), 2: median_s(b, "s3", w)} for k, (a, b, w) in S3.items()}).T
s4_times = pd.DataFrame({k: {1: median_s(c, "s4"), 2: median_s(f"{c}-v2", "s4")} for k, c in S4.items()}).T

fig, (a1, a2) = plt.subplots(1, 2, figsize=(11, 4.2), gridspec_kw={"width_ratios": [4, 5]})
rounds_panel(a1, s3_times, "s3 with all cores — seconds", " s")
rounds_panel(a2, s4_times, "s4, batch 64 — seconds (log scale)", " s", log=True)
a1.legend(fontsize=9, frameon=False, loc="lower right")
fig.suptitle("Sample (972 documents): round 1 vs round 2", fontweight="bold", x=0.01, ha="left")
save(fig, "rounds_times.png")

# costo del corpus completo: monolitica (full run) e split, stimata nella prima tornata, eseguita nella seconda
full = bars[bars["sample"].eq("full") & bars["provider"].isin(["Azure", "AWS"]) & bars["config"].ne("cloud-cpu-s")]
full = full.assign(row=full["kind"].map({"cloud, single VM": "monolithic H100", "cloud, split (2 VMs)":
                                         "split 16 vCPU + H100"}) + " · " + full["provider"])
fig, ax = plt.subplots(figsize=(8, 3.4))
rounds_panel(ax, full.pivot(index="row", columns="tornata", values="cost_eur").sort_index(),
             "Full corpus — s3 + s4 cost in €", " €", fmt="{:.2f}")
ax.set_xlabel("split: estimated from phase times in round 1, run end to end in round 2")
ax.legend(fontsize=9, frameon=False, loc="upper left", bbox_to_anchor=(1, 1))
save(fig, "rounds_costs.png")

print("grafici in", CHARTS)
