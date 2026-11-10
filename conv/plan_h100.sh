#!/usr/bin/env bash
# Confronto dei converter sulla H100 (conv/plan_bakeoff.sh senza GraniteDocling, già escluso), per i tempi veri e per
# olmOCR, che sulla vGPU da 12 GB non parte. Sul volume vecchio: modelli e venv sono già lì, i risultati vanno in
# /mnt/vol/conv/out-h100 e non toccano quelli della vGPU. Dopo il confronto si sceglie il converter e si lancia
# conv/plan_full.sh (Docling, un pezzo per GPU). Uso: make bench PLAN=conv/plan_h100.sh; ultima riga BENCH_DONE.
cd "$(dirname "$0")/.."
bash conv/mount_vol.sh || exit 1
RUN=out-h100 SKIP_VLM=1 bash conv/plan_bakeoff.sh
