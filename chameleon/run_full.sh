#!/usr/bin/env bash
# Full run pipeline sul nodo Chameleon (H100). Lanciare dentro tmux.
set -euo pipefail
cd "$(dirname "$0")/../rag"
. ../.venv/bin/activate
export PILOT_N=0 EMBED_BATCH=256      # tutti i farmaci, batch grande per H100

echo "===== FULL RUN start: $(date) ====="
echo "[s1] strutturato -> SQLite";      python s1_structured.py
echo "[s2] download ~25k PDF";          python s2_download.py
echo "[s3] PDF -> md (+OCR mirato)";    python s3_pdf_to_md.py
echo "[s4] embed bge-m3 (GPU) -> Chroma"; python s4_index.py
echo "[report] documenti mancanti";       python report_missing.py
echo "===== FULL RUN done: $(date) ====="
