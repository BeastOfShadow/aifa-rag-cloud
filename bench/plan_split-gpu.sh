#!/usr/bin/env bash
# Split configuration, second VM (H100), started on the VM by `make bench` after `make push-md`:
# s4 only, on the Markdown and database converted by the CPU VM (samples/split).
cd "$(dirname "$0")/.."
export CONFIG=split-gpu
bench/run.sh samples/split s4
echo BENCH_DONE
