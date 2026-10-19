"""Genera i grafici benchmark (PNG) dai dati reali misurati durante il progetto."""
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

plt.rcParams.update({"font.size": 11, "figure.dpi": 130, "axes.spines.top": False,
                     "axes.spines.right": False, "axes.grid": True, "grid.alpha": 0.25})
BLUE, ORANGE, GREEN, RED = "#2563eb", "#f59e0b", "#16a34a", "#dc2626"


def bar(ax, labels, vals, colors, title, ylabel, fmt="{:.1f}"):
    b = ax.bar(labels, vals, color=colors, width=0.6)
    ax.set_title(title, fontweight="bold")
    ax.set_ylabel(ylabel)
    for r, v in zip(b, vals):
        ax.text(r.get_x() + r.get_width() / 2, v, fmt.format(v),
                ha="center", va="bottom", fontweight="bold")
    ax.margins(y=0.18)


# 1) Embedding bge-m3: throughput per device (chunk/s, batch 256)
fig, ax = plt.subplots(figsize=(6, 4))
dev = ["CPU\n(M4, 10-core)", "MPS\n(M4 GPU)", "H100\n(CUDA)"]
tput = [256 / 68, 256 / 106, 256 / 4.62]          # chunk/s
bar(ax, dev, tput, [BLUE, ORANGE, GREEN],
    "Embedding bge-m3 — throughput per device", "chunk / s")
ax.text(2, tput[2] * 0.5, "~15x CPU\n~23x MPS", ha="center", color="white", fontweight="bold")
fig.tight_layout(); fig.savefig("charts/embedding_device.png"); plt.close(fig)

# 2) s3 PDF->md: seriale vs parallelo (wall-clock, ore)
fig, ax = plt.subplots(figsize=(6, 4))
bar(ax, ["Seriale\n(1 core)", "Parallelo\n(48 core)"], [15.0, 0.62],
    [RED, GREEN], "s3 PDF->md — wall-clock (17.959 doc)", "ore", "{:.2f} h")
fig.tight_layout(); fig.savefig("charts/s3_parallel.png"); plt.close(fig)

# 3) pymupdf4llm: overhead OCR per-doc (con tesseract installato)
fig, ax = plt.subplots(figsize=(6, 4))
bar(ax, ["Testo nativo\n(no OCR)", "OCR immagini\n(MuPDF+tesseract)"], [0.07, 6.9],
    [GREEN, ORANGE], "pymupdf4llm — tempo per documento", "secondi", "{:.2f} s")
fig.tight_layout(); fig.savefig("charts/pdf_extract.png"); plt.close(fig)

# 4) Qualita' retrieval: rerank top-hit prima/dopo miglioramenti
fig, ax = plt.subplots(figsize=(6, 4))
bar(ax, ["Splitter base", "+ sezioni bold", "+ boilerplate\n+ contesto"], [0.767, 0.767, 0.819],
    [BLUE, BLUE, GREEN], "Retrieval — score reranker (top hit, query senna)", "rerank score", "{:.3f}")
fig.tight_layout(); fig.savefig("charts/retrieval_quality.png"); plt.close(fig)

# 5) Ricerca RAG (query): CPU-only vs ibrido (MPS+CPU), breakdown per stadio (s)
fig, ax = plt.subplots(figsize=(6.2, 4))
labels = ["CPU only", "Ibrido\n(MPS+CPU)"]
embed = [4.82, 0.27]          # embed query bge-m3
chroma = [0.015, 0.006]       # chroma HNSW (sempre CPU)
rerank = [12.43, 4.20]        # cross-encoder su 30 candidati
b1 = ax.bar(labels, embed, color=BLUE, width=0.55, label="embed query (bge-m3)")
b2 = ax.bar(labels, chroma, bottom=embed, color=ORANGE, width=0.55, label="chroma HNSW (CPU)")
bot2 = [e + c for e, c in zip(embed, chroma)]
b3 = ax.bar(labels, rerank, bottom=bot2, color=RED, width=0.55, label="rerank (cross-encoder)")
tot = [e + c + r for e, c, r in zip(embed, chroma, rerank)]
for i, t in enumerate(tot):
    ax.text(i, t, f"{t:.1f} s", ha="center", va="bottom", fontweight="bold")
ax.set_title("Ricerca RAG — latenza per query (mediana)", fontweight="bold")
ax.set_ylabel("secondi / query"); ax.legend(fontsize=9, loc="upper right")
ax.margins(y=0.18)
fig.tight_layout(); fig.savefig("charts/rag_query_device.png"); plt.close(fig)

print("5 grafici salvati in docs/charts/")
