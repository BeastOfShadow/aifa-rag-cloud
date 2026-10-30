#!/usr/bin/env bash
# Split configuration, second VM (H100), started on the VM by `make bench` after `make push-md`:
# s4 only, on the Markdown and database converted by the CPU VM (samples/split),
# then s5 latency on the full index just built.
cd "$(dirname "$0")/.."
export CONFIG=split-gpu
bench/run.sh samples/split s4
.venv/bin/python bench/s5_bench.py "$(ls -d results/$CONFIG/*/data | tail -1)"
echo BENCH_DONE
