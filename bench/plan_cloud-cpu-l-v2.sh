#!/usr/bin/env bash
# Repeat of cloud-cpu-l (m1.xxlarge, 16 vCPU) with the aligned environment (Python 3.13.4, torch 2.14.1):
# s3 scaling (1 worker once, 4/8/16 workers x3), then one s3+s4 run on CPU.
cd "$(dirname "$0")/.."
export CONFIG=cloud-cpu-l-v2
WORKERS=1 bench/run.sh samples/n500 s3
for w in 4 8 16; do
  for i in 1 2 3; do WORKERS=$w bench/run.sh samples/n500 s3; done
done
DEVICE=cpu bench/run.sh samples/n500
echo BENCH_DONE
