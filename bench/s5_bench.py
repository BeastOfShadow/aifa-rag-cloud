"""Benchmark di s5: latenza di una domanda, fase per fase, su un indice già costruito da s4.

Uso (dalla radice del repo):
  CONFIG=local-mps DEVICE=mps .venv/bin/python bench/s5_bench.py results/local-mps/<run_id>/data
  CONFIG=cloud-gpu            .venv/bin/python bench/s5_bench.py results/cloud-gpu/<run_id>/data
  LLM=1 ...   misura anche la risposta dell'LLM (rete + API: non dipende dalla macchina)
Env: REPS (ripetizioni per domanda, default 3), DEVICE (come s4).
Output: results/<CONFIG>/<run_id>/s5.csv, una riga per domanda e ripetizione; mediane a schermo.
Qualità del retrieval: ogni domanda in s5_questions.txt porta il farmaco atteso ("domanda | DENOMINAZIONE");
dense_rank = posizione del primo frammento di quel farmaco tra i RETRIEVE_N candidati (vuoto se manca),
right_top = quanti dei TOP_K frammenti tenuti dopo il rerank sono di quel farmaco.
Env: EMBED_MODEL (come s4: deve essere lo stesso modello con cui è stato costruito l'indice).
"""
import csv
import os
import socket
import statistics
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.environ["DATA_DIR"] = str(Path(sys.argv[1]).resolve())   # prima di importare config
sys.path.insert(0, str(ROOT / "rag"))
import s5_query as q                                          # noqa: E402
from config import RETRIEVE_N, TOP_K, pick_device             # noqa: E402

CONFIG = os.environ["CONFIG"]
REPS = int(os.environ.get("REPS", "3"))
LLM = os.environ.get("LLM") == "1"
questions = [line.split(" | ") for line in
             (ROOT / "bench" / "s5_questions.txt").read_text(encoding="utf-8").splitlines()]

t = time.perf_counter()
model, reranker, coll = q._lazy()
load_s = time.perf_counter() - t


def ask(question, drug):
    """Una domanda, come q.answer ma con un cronometro per fase. Ritorna i ms per fase e la qualità."""
    t0 = time.perf_counter()
    qemb = model.encode_query([question], normalize_embeddings=True)[0].tolist()
    t1 = time.perf_counter()
    res = coll.query(query_embeddings=[qemb], n_results=RETRIEVE_N)
    t2 = time.perf_counter()
    docs = res["documents"][0]
    scores = reranker.predict([(question, d) for d in docs])
    t3 = time.perf_counter()
    if LLM:
        top = [d for _, d in sorted(zip(scores, docs), reverse=True)][:TOP_K]
        q.generate(q.PROMPT.format(q=question, ctx="\n\n".join(top)))
    t4 = time.perf_counter()
    ms = lambda a, b: round((b - a) * 1000, 1)
    right = [m["denominazione"] == drug for m in res["metadatas"][0]]   # fuori dal cronometro
    reranked = [r for _, r in sorted(zip(scores, right), key=lambda x: x[0], reverse=True)]
    return {"embed_ms": ms(t0, t1), "search_ms": ms(t1, t2), "rerank_ms": ms(t2, t3),
            "llm_ms": ms(t3, t4) if LLM else "", "total_ms": ms(t0, t4),
            "dense_rank": right.index(True) + 1 if True in right else "",
            "right_top": sum(reranked[:TOP_K])}


for question, drug in questions[:3]:   # riscaldamento: prima esecuzione di modelli e cache, non conta
    ask(question, drug)

run_id = time.strftime("%Y%m%dT%H%M%S")
out = ROOT / "results" / CONFIG / run_id
out.mkdir(parents=True)
commit = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"],
                        capture_output=True, text=True).stdout.strip()
rows = []
for rep in range(1, REPS + 1):
    for i, (question, drug) in enumerate(questions, 1):
        rows.append({"run_id": run_id, "config": CONFIG, "commit": commit,
                     "host": socket.gethostname().split(".")[0], "device": pick_device(),
                     "embed_model": q.EMBED_MODEL, "question": i, "rep": rep, **ask(question, drug)})

with open(out / "s5.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=rows[0].keys())
    w.writeheader()
    w.writerows(rows)

print(f"{CONFIG} su {rows[0]['device']}: caricamento modelli {load_s:.1f} s, "
      f"{len(questions)} domande x {REPS}, mediane in ms")
for k in ("embed_ms", "search_ms", "rerank_ms", "llm_ms", "total_ms"):
    if rows[0][k] != "":
        print(f"  {k:10s} {statistics.median(r[k] for r in rows):8.1f}")
print(f"  p95 totale {sorted(r['total_ms'] for r in rows)[int(0.95 * len(rows)) - 1]:8.1f}")
first = rows[:len(questions)]   # la qualità non cambia tra ripetizioni
print(f"  farmaco giusto tra i {RETRIEVE_N} candidati: {sum(r['dense_rank'] != '' for r in first)}/{len(first)}, "
      f"frammenti giusti nei primi {TOP_K} dopo il rerank: {sum(r['right_top'] for r in first)}/{TOP_K * len(first)}")
print("->", out / "s5.csv")
