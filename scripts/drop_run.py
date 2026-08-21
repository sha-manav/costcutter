#!/usr/bin/env python3
"""Remove specific runs from a results file, loudly.

A run that ends because the provider returned a 5xx is not the agent failing
the task, but nothing downstream can tell the difference: the row has zero
steps and `success: false`, and it lands on the success rate. The honest fix
is to drop that row and re-run the task, not to special-case it in scoring.

Dropping rows from a results file is exactly the move that turns an honest
benchmark into a tuned one, so this refuses to guess: it drops only rows
matched by an explicit (condition, trial, task) triple, prints each one, and
writes the removed rows to a sidecar file so nothing disappears silently.

    python scripts/drop_run.py artifacts/results.jsonl \
        --run A_browser:2:T03_stock_on_hand#1 --reason "provider 500"
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("results")
    ap.add_argument("--run", action="append", required=True,
                    metavar="CONDITION:TRIAL:TASK_ID",
                    help="may be repeated")
    ap.add_argument("--reason", required=True,
                    help="recorded in the sidecar; say why this is not a "
                         "result being tuned away")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    path = Path(args.results)
    targets = set()
    for spec in args.run:
        condition, trial, task_id = spec.split(":", 2)
        targets.add((condition, int(trial), task_id))

    kept, dropped = [], []
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        key = (row.get("condition"), int(row.get("trial", 0)), row.get("task_id"))
        (dropped if key in targets else kept).append((key, row, line))

    found = {key for key, _row, _line in dropped}
    for missing in sorted(targets - found):
        print(f"no row matches {missing}", file=sys.stderr)
    if not dropped:
        return 1

    for key, row, _line in dropped:
        print(f"dropping {key[0]} t{key[1]} {key[2]}  success={row.get('success')} "
              f"steps={row.get('n_steps')}  error={(row.get('error') or '')[:60]}")
    if args.dry_run:
        print("(dry run; nothing written)")
        return 0

    sidecar = path.with_name(path.stem + "_dropped.jsonl")
    with sidecar.open("a") as fh:
        for _key, row, _line in dropped:
            row["dropped_reason"] = args.reason
            fh.write(json.dumps(row, default=str) + "\n")
    path.write_text("".join(line + "\n" for _key, _row, line in kept))
    print(f"dropped {len(dropped)} row(s); kept {len(kept)}")
    print(f"removed rows appended to {sidecar} with the stated reason")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
