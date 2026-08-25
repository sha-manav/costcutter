#!/usr/bin/env bash
# Drive the calibration gate across the ERPNext site pool.
#
# One OS process per site, each single-threaded and owning exactly one site.
# That is deliberate, for two reasons.
#
# A site owned by two workers is the bug that cost the first throughput
# measurement 15%: one rollout's reset dropped a database another was
# mid-snapshot on. Process-per-site makes that structurally impossible rather
# than a scheduling convention.
#
# And the wall-clock ceiling in shadow/llm.py is a SIGALRM, which Python
# delivers only to the main thread. In-process threads would silently lose
# the one bound that actually stops a hung provider call -- the failure that
# cost this project most of two days. Separate processes each have their own
# main thread, so every shard keeps the alarm.
#
# Usage:
#   bash scripts/run_gate_pool.sh 6 --require-model --resume --trials 3
set -euo pipefail

N="${1:-6}"; shift || true
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO"
OUTDIR="artifacts/gate_shards"
mkdir -p "$OUTDIR"

echo "launching $N shards, one per site"
pids=()
for i in $(seq 1 "$N"); do
  site=$(printf "erp%02d.localhost" "$i")
  out="$OUTDIR/shard_${i}_of_${N}.jsonl"
  .venv/bin/python -m erpbench.gate \
      --site "$site" --shard "$i/$N" --out "$out" "$@" \
      > "$OUTDIR/shard_${i}.log" 2>&1 &
  pids+=($!)
  echo "  shard $i/$N -> $site  (log: $OUTDIR/shard_${i}.log)"
done

echo
echo "waiting; tail a log to watch, e.g.:  tail -f $OUTDIR/shard_1.log"
failed=0
for p in "${pids[@]}"; do wait "$p" || failed=$((failed+1)); done

echo
echo "$((N-failed))/$N shards exited cleanly"
.venv/bin/python scripts/merge_gate_shards.py
