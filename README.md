# aifa-rag-cloud

Retrieval-Augmented Generation over the package leaflets (FI) and summaries of product characteristics
(RCP) published by AIFA, the Italian Medicines Agency: ~18,000 PDFs converted to Markdown, split into
~805,000 chunks and indexed in a vector database, with a structured SQLite layer for tabular questions.

University project for the Cloud Computing course of Prof. Massimo Canonico (Università del Piemonte
Orientale). The cloud extension automates the infrastructure as code and compares time and cost of
local vs cloud, CPU vs GPU configurations. The full write-up, in Italian, is
[docs/RELAZIONE.md](docs/RELAZIONE.md).

## Results at a glance

- **Provisioning as code:** a VM on Chameleon Cloud is reachable in ~70 s with `make lease up`
  (vs ~7 min by hand in the dashboard) and ready for measurements in ~5 min; `make down` releases
  everything in 19 s.
- **PDF → Markdown (s3)** scales almost linearly on cloud VMs (efficiency 0.86 at 16 workers), but
  stops at 2 workers on an Apple M4.
- **Embedding (s4)** on an H100 is ~30× faster than the M4 CPU and ~88× faster than an 8 vCPU VM.
- **Full corpus on one H100 VM:** 2 h 40 min download, 35 min conversion, 2 h 44 min embedding.
  The GPU sits idle 54% of the time; moving download and conversion to a CPU VM cuts the estimated
  cost by ~45%.
- **Provider matters more than architecture:** s3 + s4 on the full corpus costs from €11.77 (H100 on
  RunPod) to €33.10 (H100 on Azure).

![s3 speedup](docs/charts/s3_scaling.png)

## Repository layout

| Path | Content |
|---|---|
| `rag/` | the pipeline: `s1`…`s5` scripts, `config.py`, Streamlit explorer `ui.py` |
| `datasets/` | AIFA open-data CSVs (drug list, active ingredients, ATC codes) |
| `bench/` | benchmark harness: sample creation, instrumented runs, results collection, cost model, charts |
| `infra/` | Terraform for the Chameleon VM (security group, port, instance, floating IP) |
| `Makefile` | VM lifecycle on Chameleon: `lease`, `up`, `deploy`, `bench`, `fetch`, `down`, `ssh` |
| `chameleon/` | `bootstrap.sh` (VM setup), scripts of the original manual full run (baseline v0) |
| `results/` | measurements: one folder per run, plus `all.csv`, `costs.csv`, `costs_split.csv`, `infra_times.csv` |
| `docs/` | report, charts, screenshots of the manual procedure, sources |

Not in the repository (`.gitignore`): `data/` (PDFs, Markdown, Chroma index, ~13 GB), `samples/`,
credentials, Terraform state.

## Pipeline

| Phase | Script | What it does | Bound by |
|---|---|---|---|
| s1 | `s1_structured.py` | CSV → SQLite | disk |
| s2 | `s2_download.py` | async download of FI/RCP PDFs | network (and politeness to AIFA) |
| s3 | `s3_pdf_to_md.py` | PDF → Markdown with PyMuPDF4LLM + Tesseract OCR, multiprocess | CPU |
| s4 | `s4_index.py` | section-aware chunking, bge-m3 embeddings, ChromaDB | GPU |
| s5 | `s5_query.py` | retrieve 30 → rerank with bge-reranker-v2-m3 → top 6 → LLM answer | latency |

### Setup

```bash
python3 -m venv .venv && .venv/bin/pip install -r rag/requirements.txt
brew install tesseract tesseract-lang      # macOS; Debian/Ubuntu: apt install tesseract-ocr tesseract-ocr-ita
```

Python 3.13 locally (3.12 on the Chameleon images). On a GPU machine install the CUDA build of PyTorch
first, as `chameleon/bootstrap.sh` does.

### Run

```bash
cd rag
export PILOT_N=100                          # number of drugs; 0 = all 12,599 (~25,000 PDFs)
../.venv/bin/python s1_structured.py
../.venv/bin/python s2_download.py
../.venv/bin/python s3_pdf_to_md.py
../.venv/bin/python s4_index.py
../.venv/bin/python s5_query.py "Per cosa si usa la senna? Controindicazioni?"
../.venv/bin/streamlit run ui.py --server.fileWatcherType none
```

s5 answers with Claude by default: it needs `pip install anthropic` (not in `requirements.txt`) and
`ANTHROPIC_API_KEY`. With `LLM_PROVIDER=ollama` it uses a local Ollama model instead. Only the question
and the retrieved chunks leave the machine.

| Variable | Default | Meaning |
|---|---|---|
| `DATA_DIR` | `data/` | where SQLite, PDFs, Markdown and Chroma live |
| `PILOT_N` | `100` | drugs to process (`0` = all) |
| `KEEP_PDF` | `0` | `1` keeps the PDFs after conversion |
| `WORKERS` | all cores | s3 processes |
| `DEVICE` | auto | s4/s5 device: `cuda`, `mps` or `cpu` |
| `EMBED_BATCH` | `64` | s4 model batch size |
| `LLM_PROVIDER` / `LLM_MODEL` | `claude` / `claude-opus-4-8` | s5 generator |
| `OLLAMA_MODEL` / `OLLAMA_HOST` | `qwen2.5:7b` / `http://localhost:11434` | s5 with Ollama |

## Benchmarks

Every measurement runs on a fixed sample (the first 500 drugs by code: 972 valid PDFs) in an isolated
folder, so runs never touch `data/` or each other.

```bash
bench/make_sample.sh 500                                     # samples/n500/ + n500.tar.gz (downloads ~1,000 PDFs)
CONFIG=local-cpu-ocr WORKERS=4 bench/run.sh samples/n500 s3  # one phase, 4 workers
CONFIG=local-cpu DEVICE=cpu bench/run.sh samples/n500        # s3 + s4 (s4 needs s3 in the same run)
.venv/bin/python bench/collect.py                            # -> results/all.csv
.venv/bin/python bench/cost_model.py                         # -> results/costs.csv, costs_split.csv
.venv/bin/python bench/charts.py                             # -> docs/charts/*.png
```

Each run writes `results/<config>/<run_id>/`: `phases.csv` (start, end, seconds, commit, host,
workers, device, batch), one log per phase, and on Linux `vmstat.txt` / `gpu.csv` (one sample per
second). Prices and their sources are in `bench/prices.csv`.

## Cloud runs on Chameleon

Prerequisites:
- an allocation on Chameleon KVM@TACC and an application credential saved as
  `~/.config/openstack/clouds.yaml`, with the cloud named `chameleon`;
- Terraform, the OpenStack CLI with the Blazar plugin
  (`pipx install python-openstackclient && pipx inject python-openstackclient python-blazarclient`),
  `jq`;
- an SSH key registered on Chameleon as keypair `farmaci-h100` (private key in
  `~/.ssh/chameleon_farmaci`; both can be changed in `Makefile` and `infra/main.tf`).

```bash
make lease up deploy bench     # reserve flavor, create VM, ship code + sample, start the benchmark plan
make ssh                       # optional: tail -f bench.log, the plan ends with BENCH_DONE
make fetch down                # copy results to results/<config>/, destroy VM and lease
```

- The lease is created with the OpenStack CLI (Blazar is not covered by the Terraform provider); the VM,
  its security group, port and floating IP by Terraform.
- Code is shipped as a `git bundle` of the current commit: commit before `make deploy`.
- The benchmark plan is `bench/plan_<config>.sh`. Other configurations:
  `make lease up deploy bench FLAVOR_ID=<flavor id> LEASE_NAME=<name> CONFIG=<config>`
  (flavor ids from `openstack --os-cloud chameleon flavor list`).
- Every target appends its duration to `results/infra_times.csv`.
- A lease consumes Service Units for its whole duration: run `make down` as soon as the plan is done.
- One VM at a time: there is a single Terraform state in `infra/`. `make lease` uses BSD `date -v`
  (macOS); on Linux replace it with `date -d "+N hours"`.

## Documentation

- [docs/RELAZIONE.md](docs/RELAZIONE.md) — the report (Italian): method, manual vs IaC provisioning,
  results, cost model, limitations.
- [APPROFONDIMENTO_IAC.md](APPROFONDIMENTO_IAC.md) — the original plan of the cloud extension.
- [LAVORO_SVOLTO.md](LAVORO_SVOLTO.md) — log of the first version (baseline v0).

Data: AIFA open data, [aifa.gov.it/liste-dei-farmaci](https://www.aifa.gov.it/it/liste-dei-farmaci).
