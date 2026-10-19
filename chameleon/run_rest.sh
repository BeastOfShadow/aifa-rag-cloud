#!/usr/bin/env bash
# Riprende dal punto s3 (download s2 gia' completo). s3 parallelo -> s4 GPU -> report.
set -euo pipefail
cd "$(dirname "$0")/../rag"
. ../.venv/bin/activate
export PILOT_N=0 EMBED_BATCH=256

echo "===== RESUME (s3->s4->report) start: $(date) ====="
echo "[s3] PDF -> md PARALLELO (OCR immagini incluso)"; python s3_pdf_to_md.py
echo "[s4] embed bge-m3 (GPU) -> Chroma";               python s4_index.py
echo "[report] documenti mancanti";                     python report_missing.py
echo "===== FULL RUN done: $(date) ====="
