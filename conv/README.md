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

# 3. converter bake-off on an H100 VM (each converter in its own venv on a volume); the plan first removes the
#    AIFA stamp (footer and diagonal watermark) from the PDFs with conv/clean.py
make lease up FLAVOR_ID=<g1.h100.pci.1 id> LEASE_NAME=aifa-conv IMAGE=CC-Ubuntu24.04-CUDA VOLUME_GB=150
make deploy conv-push bench PLAN=conv/plan_bakeoff.sh LEASE_NAME=aifa-conv
make conv-fetch down LEASE_NAME=aifa-conv
.venv/bin/python conv/score.py                # -> conv/results/bakeoff_summary.csv, bakeoff_scores.csv
```

```bash
# 4. on the 4xH100 lease: reuse the data volume of the dev VM (models, venvs, cleaned PDFs, first Docling output),
#    attached between `make up` and `make deploy` (deploy mounts /dev/vdb without formatting it)
make lease up FLAVOR_ID=<g1.h100.pci.4 id> LEASE_NAME=aifa-conv-full IMAGE=CC-Ubuntu24.04-CUDA   # VOLUME_GB=0
openstack server add volume <server> aifa-conv-dev-data
make deploy conv-push bench PLAN=conv/plan_h100.sh LEASE_NAME=aifa-conv-full   # bake-off on the H100, no GraniteDocling
make ssh LEASE_NAME=aifa-conv-full                                             # then, once the converter is chosen:
bash aifa-rag-cloud/conv/plan_full.sh > ~/full.log 2>&1                        # all PDFs, one Docling shard per GPU
```

```bash
# 5. on the VM (W=/mnt/vol/conv): quality of every document against the native PDF text, fallback for the documents
#    Docling reads badly (MinerU and olmOCR on those only), final corpus, vocabulary, sections
.venv/bin/python conv/qc.py $W/full/docling $W/data/pdf_clean $W/full/qc.csv          # recall, precision, titles, tables
ONLY="mineru olmocr" IN_DIR=$W/fallback/pdf RUN=out-fallback bash conv/plan_bakeoff.sh # recall < 0.95 only
.venv/bin/python conv/assemble.py $W/full/docling $W/out-fallback/mineru $W/out-fallback/olmocr-flat \
  $W/fallback_choice.csv $W/final                                                     # best version per document
.venv/bin/python conv/vocab.py $W/final $W/vocab                                      # words, pairs, triples, phrases
FARMACI_DB=$W/s4data/farmaci.sqlite QC=$W/final_qc.csv \
  .venv/bin/python conv/split_sections.py $W/final $W/corpus                           # one file per section + corpus.sqlite
.venv/bin/python conv/vocab_viz.py                                                    # conv/docs/vocabulary.html (on the Mac)
```

The final Markdown is in `$W/final` (and in `data/md` on the Mac). Each section file in `$W/corpus/sections/<doc>/` is an
exact slice of the document: joined in order they give the document back byte for byte, which `split_sections.py`
checks on every document. `corpus.sqlite` has one row per document and per section (start and end offset in the
document, file, sha256) and joins `data/farmaci.sqlite` on `cod_farmaco`.

The data volume `aifa-conv-dev-data` outlives the leases: it is not in any Terraform state (so `make down` cannot delete
it) and is attached by hand with `openstack server add volume <server> aifa-conv-dev-data` before `make deploy`.

`make deploy` ships the committed `HEAD`, so `conv/plan_bakeoff.sh` must be committed first.
The scores compare each Markdown with the native text of its PDF (no hand-written reference): word recall and
precision, standard section titles found as titles, table consistency, watermark left; see the docstring of
`conv/score.py`.
