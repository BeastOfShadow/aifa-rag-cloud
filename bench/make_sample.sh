#!/usr/bin/env bash
# Campione fisso per i benchmark: s1 + s2 sui primi N farmaci -> samples/nN/ + samples/nN.tar.gz
# Uso: bench/make_sample.sh [N]   (default 500)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
N="${1:-500}"
OUT="$ROOT/samples/n$N"
PY="${PY:-$ROOT/.venv/bin/python}"

export DATA_DIR="$OUT" PILOT_N="$N" KEEP_PDF=1
cd "$ROOT/rag"
"$PY" s1_structured.py
"$PY" s2_download.py
# niente attributi estesi/file ._ di macOS nell'archivio (rumore su Linux)
COPYFILE_DISABLE=1 tar --no-xattrs -C "$OUT" -czf "$ROOT/samples/n$N.tar.gz" farmaci.sqlite pdf
echo "campione: $OUT ($(ls "$OUT/pdf" | wc -l | tr -d ' ') pdf) -> samples/n$N.tar.gz"
