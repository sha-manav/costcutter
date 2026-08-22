#!/usr/bin/env bash
# Opus rung of the model ladder. Harness frozen at harness-v1; the only
# variable is models.agent, passed with --model so config.yaml is untouched.
set -uo pipefail
cd /home/user/costcutter
.venv/bin/python -m shadow.bench.run --require-model \
    --model claude-opus-5 --trials 3 --resume \
    --out artifacts/ladder/opus/results.jsonl >>/tmp/ladder_opus.log 2>&1 || exit 1
bash scripts/checkpoint_results.sh
[ "$(wc -l < artifacts/ladder/opus/results.jsonl)" -ge 108 ] || exit 1
echo "opus rung complete"
