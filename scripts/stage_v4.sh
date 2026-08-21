#!/usr/bin/env bash
# The v4 measurement sequence. Every step is resumable and idempotent.
set -uo pipefail
cd /home/user/costcutter
PY=.venv/bin/python

step() { echo "=== $* at $(date -Is)"; }

step "held-out rerun on the fixed system"
$PY -m shadow.bench.run --require-model --trials 3 --resume \
    --out artifacts/results_heldout_v4.jsonl >>/tmp/heldout_v4.log 2>&1 || exit 1
[ "$(wc -l < artifacts/results_heldout_v4.jsonl)" -ge 108 ] || exit 1

step "in-distribution capture"
if [ ! -s artifacts/indist/capture/out.jsonl ]; then
  $PY -m shadow.bench.indist_pipeline capture --sessions 3 \
      >>/tmp/indist_capture.log 2>&1 || exit 1
fi

step "in-distribution distill"
if [ ! -s artifacts/indist/tools.json ]; then
  $PY -m shadow.bench.indist_pipeline distill >>/tmp/indist_distill.log 2>&1 || exit 1
fi

step "in-distribution verify"
if [ ! -s artifacts/indist/verify_report.json ]; then
  $PY -m shadow.bench.indist_pipeline verify >>/tmp/indist_verify.log 2>&1 || exit 1
fi

step "in-distribution benchmark"
$PY -m shadow.bench.indist_pipeline bench --require-model --trials 3 --resume \
    >>/tmp/indist_bench.log 2>&1 || exit 1

step "v4 sequence complete"
