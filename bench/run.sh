#!/usr/bin/env bash
# Run strumentato su un campione: tempo per fase + utilizzo risorse.
# Uso: CONFIG=local-mps bench/run.sh samples/n500 [fasi...]   (default: s3 s4)
# Env: WORKERS (s3), DEVICE / EMBED_BATCH (s4). Output: results/<CONFIG>/<run_id>/
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CONFIG="${CONFIG:?es. CONFIG=local-mps}"
SAMPLE="$(cd "${1:?dir campione (farmaci.sqlite + pdf/)}" && pwd)"; shift
PHASES="${*:-s3 s4}"
PY="${PY:-$ROOT/.venv/bin/python}"
RUN_ID="$(date +%Y%m%dT%H%M%S)"
OUT="$ROOT/results/$CONFIG/$RUN_ID"

# dati del run isolati: sqlite copiato (s3 lo modifica), pdf in sola lettura via symlink
export DATA_DIR="$OUT/data" KEEP_PDF=1
mkdir -p "$DATA_DIR"
cp "$SAMPLE/farmaci.sqlite" "$DATA_DIR/"
ln -s "$SAMPLE/pdf" "$DATA_DIR/pdf"

# monitor risorse in background, chiusi all'uscita
if command -v nvidia-smi >/dev/null; then
  nvidia-smi --query-gpu=timestamp,utilization.gpu,memory.used --format=csv -l 1 > "$OUT/gpu.csv" &
fi
# ponytail: vmstat solo su Linux; sul Mac solo tempi, aggiungere `top -l` se serve la CPU locale
if [ "$(uname)" = Linux ]; then vmstat -n -t 1 > "$OUT/vmstat.txt" & fi
trap 'kill $(jobs -p) 2>/dev/null || true' EXIT

COMMIT="$(git -C "$ROOT" rev-parse --short HEAD)$(git -C "$ROOT" diff --quiet || echo -dirty)"
echo "run_id,config,commit,host,cpus,workers,device,embed_batch,phase,start,end,seconds" > "$OUT/phases.csv"
cd "$ROOT/rag"
for p in $PHASES; do
  t0=$(date +%s)
  "$PY" "$p"_*.py 2>&1 | tee "$OUT/$p.log"
  t1=$(date +%s)
  echo "$RUN_ID,$CONFIG,$COMMIT,$(hostname -s),$(getconf _NPROCESSORS_ONLN),${WORKERS:-all},${DEVICE:-auto},${EMBED_BATCH:-64},$p,$t0,$t1,$((t1-t0))" >> "$OUT/phases.csv"
done
cat "$OUT/phases.csv"
