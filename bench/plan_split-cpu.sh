#!/usr/bin/env bash
# Split configuration, first VM (m1.xxlarge, 16 vCPU), started on the VM by `make bench`:
# s1 + s2 on the full corpus into samples/full (as in the full run), then s3 with 16 workers.
# The Markdown reaches the H100 VM through `make fetch-md` here and `make push-md` there.
cd "$(dirname "$0")/.."
export CONFIG=split-cpu
FULL="$PWD/samples/full"
mkdir -p "$FULL" "results/$CONFIG"
echo "phase,start,end,seconds" > "results/$CONFIG/full_s1s2.csv"
for p in s1 s2; do
  t0=$(date +%s)
  (cd rag && DATA_DIR="$FULL" PILOT_N=0 KEEP_PDF=1 ../.venv/bin/python "$p"_*.py) > "results/$CONFIG/$p.log" 2>&1
  t1=$(date +%s)
  echo "$p,$t0,$t1,$((t1-t0))" >> "results/$CONFIG/full_s1s2.csv"
done
WORKERS=16 bench/run.sh "$FULL" s3
echo BENCH_DONE
