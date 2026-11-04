# conv/ — faithful PDF to Markdown conversion

A second pipeline, separate from the one measured for the exam (`rag/`, `bench/`): a structure-preserving
conversion of the AIFA PDFs (sections, tables, no watermark), then section splitting, a corpus vocabulary and a
map of documents and sections. Plan, method and results in [docs/RELAZIONE.md](docs/RELAZIONE.md) (Italian).
It writes only under `conv/` (`conv/data/` and `conv/out/` are not in the repository).

## Steps so far

```bash
# 1. all PDFs available on AIFA, kept this time (~6.6 GB, ~3.5 h): s2 of the project on a copy of the s1 database
mkdir -p conv/data && cp data/farmaci.sqlite conv/data/ && sqlite3 conv/data/farmaci.sqlite "DROP TABLE docs"
DATA_DIR=conv/data PILOT_N=0 .venv/bin/python rag/s2_download.py

# 2. gold set: 40 documents in groups (standard, old format, broken tables, long, radiopharmaceuticals),
#    its PDFs downloaded right away
.venv/bin/python conv/gold.py                 # -> conv/gold.csv

# 3. converter bake-off on an H100 VM (each converter in its own venv on a volume)
make lease up FLAVOR_ID=<g1.h100.pci.1 id> LEASE_NAME=aifa-conv IMAGE=CC-Ubuntu24.04-CUDA VOLUME_GB=150
make deploy conv-push bench PLAN=conv/plan_bakeoff.sh LEASE_NAME=aifa-conv
make conv-fetch down LEASE_NAME=aifa-conv
.venv/bin/python conv/score.py                # -> conv/results/bakeoff_summary.csv, bakeoff_scores.csv
```

`make deploy` ships the committed `HEAD`, so `conv/plan_bakeoff.sh` must be committed first.
The scores compare each Markdown with the native text of its PDF (no hand-written reference): word recall and
precision, standard section titles found as titles, table consistency, watermark left; see the docstring of
`conv/score.py`.
