#!/usr/bin/env bash
# Repeat of cloud-gpu (H100, 48 vCPU) with the aligned environment (Python 3.13.4, torch 2.14.1):
# s3 scaling (1 worker once, 8/16/32/48 x3), s3+s4 with batch 64 x3, 256 x3, 32 and 128 once,
# then s5 latency on the index of the last batch-64 run.
cd "$(dirname "$0")/.."
export CONFIG=cloud-gpu-v2
WORKERS=1 bench/run.sh samples/n500 s3
for w in 8 16 32 48; do
  for i in 1 2 3; do WORKERS=$w bench/run.sh samples/n500 s3; done
done
for b in 64 64 64 256 256 256 32 128; do EMBED_BATCH=$b bench/run.sh samples/n500; done
B64=$(grep -l ',64,s4,' results/$CONFIG/*/phases.csv | tail -1 | xargs dirname)
.venv/bin/python bench/s5_bench.py "$B64/data"
echo BENCH_DONE
