#!/usr/bin/env bash
# Haiku rung of the model ladder. Harness frozen at harness-v1; the only
# variable is models.agent, passed with --model so config.yaml is untouched.
set -uo pipefail
cd /home/user/costcutter
.venv/bin/python -m shadow.bench.run --require-model \
    --model claude-haiku-4-5-20251001 --trials 3 --resume \
    --out artifacts/ladder/haiku/results.jsonl >>/tmp/ladder_haiku.log 2>&1 || exit 1
[ "$(wc -l < artifacts/ladder/haiku/results.jsonl)" -ge 108 ] || exit 1
echo "haiku rung complete"
