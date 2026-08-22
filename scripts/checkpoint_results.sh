#!/usr/bin/env bash
# Push benchmark results as they are produced.
#
# artifacts/ is gitignored, so results survive a container rollback only if
# they are force-added. Milestone commits are not enough: this container was
# restored from an older snapshot mid-run and took the uncommitted rows with
# it. Committed work came back from the remote untouched, so the fix is to
# make the remote the running record rather than the finished one.
set -uo pipefail
cd /home/user/costcutter

FILES=$(git status --porcelain --ignored=matching artifacts 2>/dev/null \
        | awk '$1 ~ /^(\?\?|!!|.M)$/ {print $2}' \
        | grep -E 'results.*\.jsonl$|frontier\.json$|tools\.json$|verify_report\.json$' || true)
[ -z "$FILES" ] && exit 0

# shellcheck disable=SC2086
git add -f $FILES 2>/dev/null
git diff --cached --quiet && exit 0

ROWS=$(for f in $FILES; do wc -l < "$f" 2>/dev/null; done | paste -sd+ | bc 2>/dev/null || echo "?")
git commit -q -m "checkpoint: benchmark results in progress (${ROWS} rows)

Written by scripts/checkpoint_results.sh while a run is live. Results are
committed as they are produced rather than at milestones, because
uncommitted artifacts do not survive a container rollback."
for i in 1 2 3 4; do
  git push -q origin HEAD:claude/shadow-http-tool-synthesis-upjv6m 2>/dev/null && exit 0
  sleep $((i * 2))
done
exit 1
