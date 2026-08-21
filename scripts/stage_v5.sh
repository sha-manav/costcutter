#!/usr/bin/env bash
# Run 2: the in-distribution experiment, over all 12 D templates including
# the two child-table workflows. Every step is resumable and idempotent.
set -uo pipefail
cd /home/user/costcutter
PY=.venv/bin/python
step() { echo "=== $* at $(date -Is)"; }

step "in-distribution capture (12 templates, 3 sessions)"
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

step "in-distribution benchmark (3 trials, both conditions)"
$PY -m shadow.bench.indist_pipeline bench --require-model --trials 3 --resume \
    >>/tmp/indist_bench.log 2>&1 || exit 1

step "v5 run 2 complete"
