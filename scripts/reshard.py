"""Redistribute existing rows when a run changes shard count.

`--resume` reads only the shard's own `--out` file, so re-sharding from N to
M orphans every completed row: shard 3 of 12 looks for `shard_3_of_12.jsonl`
and never sees what `shard_3_of_24.jsonl` already did. Those rows are then
silently re-run, or worse, quietly absent.

Rows are placed by recomputing the job ordering and finding each row's index,
which is the same strided rule the driver uses.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from erpbench import gate, splits                              # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dir", required=True)
    ap.add_argument("--to", type=int, required=True)
    ap.add_argument("--models", required=True)
    ap.add_argument("--bucket", default=None)
    ap.add_argument("--trials", type=int, default=6)
    ap.add_argument("--split", default="evaluation")
    args = ap.parse_args()

    d = Path(args.dir)
    rows: list[dict] = []
    for f in d.glob("shard_*_of_*.jsonl"):
        rows += [json.loads(l) for l in f.read_text().splitlines() if l.strip()]
        f.rename(f.with_suffix(".jsonl.resharded"))
    if not rows:
        print("no rows to redistribute")
        return 0

    jobs = gate.build_jobs([m for m in args.models.split(",") if m],
                           ["A", "B", "C"], ["naive", "corrected"],
                           args.trials, args.split)
    if args.bucket:
        jobs = [j for j in jobs
                if splits.split_of(j.template.template_id, j.firm.firm_id,
                                   j.trial_idx) == args.bucket]
    index = {j.rid: n for n, j in enumerate(jobs)}

    buckets: dict[int, list[dict]] = {}
    orphaned = 0
    for r in rows:
        n = index.get(r.get("run_id"))
        if n is None:
            orphaned += 1
            continue
        buckets.setdefault(n % args.to + 1, []).append(r)

    for shard, rs in sorted(buckets.items()):
        out = d / f"shard_{shard}_of_{args.to}.jsonl"
        out.write_text("\n".join(json.dumps(r, default=str) for r in rs) + "\n")
        print(f"  shard_{shard}_of_{args.to}.jsonl  {len(rs)} rows")
    print(f"redistributed {sum(len(v) for v in buckets.values())} rows across "
          f"{args.to} shards"
          + (f"; {orphaned} orphaned (not in this job set)" if orphaned else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
