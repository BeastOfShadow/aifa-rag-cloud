#!/usr/bin/env bash
# Repeat of cloud-cpu-s (m1.xlarge, 8 vCPU) with the aligned environment (Python 3.13.4, torch 2.14.1):
# s3 scaling 1/2/4/8 workers x3, then one s3+s4 run on CPU (s4 needs s3 in the same run).
cd "$(dirname "$0")/.."
export CONFIG=cloud-cpu-s-v2
for w in 1 2 4 8; do
  for i in 1 2 3; do WORKERS=$w bench/run.sh samples/n500 s3; done
done
DEVICE=cpu bench/run.sh samples/n500
echo BENCH_DONE
