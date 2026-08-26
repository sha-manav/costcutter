"""Merge per-shard gate outputs into one results file.

Each shard writes its own file so that no two processes append to the same
descriptor. Merging is by `run_id`, last write winning, which is the same
rule `gate.latest_rows` applies within a file -- a row re-run under changed
assertions or a changed harness supersedes its predecessor rather than being
counted twice.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

SHARDS = Path(os.environ.get("GATE_OUTDIR",
                             REPO / "artifacts" / "gate_shards"))

# One file per split. The destination used to be hardcoded to the calibration
# results, so finishing an evaluation run would have merged 2,160 evaluation
# rows into the 810-row week-1 record -- two corpora, two reported
# measurements, one file, and the calibration numbers no longer reproducible
# from it. Split is read from the rows themselves rather than passed in,
# because a flag can be forgotten and a template id cannot.
DESTS = {"calibration": REPO / "artifacts" / "calibration_gate.jsonl",
         "evaluation": Path(os.environ.get(
             "GATE_DEST", REPO / "artifacts" / "evaluation_run.jsonl"))}


# A split is not a run. Week 2's baseline and the strengthened-holdout run
# are both "evaluation", so the split guard does not separate them -- and the
# pool driver calls this automatically on completion, with no GATE_DEST set.
# That merged 396 holdout rows into the published 2,160-row baseline and
# silently replaced 156 of its rows with re-runs. Nothing errored; the file
# simply stopped being the artifact the README cites.
#
# A destination is therefore refused rather than defaulted when the shards
# do not match what is already there.
def would_disturb(dest: Path, rows: list[dict]) -> str | None:
    """Why writing these rows into `dest` would change what it already holds."""
    if not dest.exists():
        return None
    existing = [json.loads(l) for l in dest.read_text().splitlines() if l.strip()]
    if not existing:
        return None
    have = {r.get("run_id") for r in existing}
    incoming = {r.get("run_id") for r in rows}
    overwritten = len(have & incoming)
    added = len(incoming - have)
    ex_trials = {r.get("trial_idx") for r in existing}
    in_trials = {r.get("trial_idx") for r in rows}
    if in_trials - ex_trials or added > len(existing) * 0.05:
        return (f"{dest.name} holds {len(existing)} rows; these shards would "
                f"add {added} and overwrite {overwritten}. If this is a "
                f"different run, set GATE_DEST to its own file.")
    return None


def split_of_rows(rows: list[dict]) -> str:
    """Which corpus these rows came from, or a refusal if they are mixed."""
    kinds = {"calibration" if str(r.get("template_id", "")).startswith("C")
             else "evaluation" for r in rows}
    if len(kinds) != 1:
        raise SystemExit(
            "shards contain rows from more than one split; refusing to merge. "
            "Move the stale shard files aside and re-run.")
    return kinds.pop()


def main() -> int:
    files = sorted(SHARDS.glob("shard_*.jsonl"))
    if not files:
        print(f"no shard files in {SHARDS}", file=sys.stderr)
        return 1
    shard_rows, per_file = [], {}
    for f in files:
        rows = [json.loads(l) for l in f.read_text().splitlines() if l.strip()]
        per_file[f.name] = len(rows)
        shard_rows += rows
    if not shard_rows:
        print("shard files are empty", file=sys.stderr)
        return 1

    split = split_of_rows(shard_rows)
    dest = DESTS[split]

    complaint = would_disturb(dest, shard_rows)
    if complaint and not os.environ.get("GATE_MERGE_FORCE"):
        print(f"refusing to merge: {complaint}", file=sys.stderr)
        return 1

    by_id: dict[str, dict] = {}
    if dest.exists():                       # keep anything already measured
        for line in dest.read_text().splitlines():
            if line.strip():
                row = json.loads(line)
                by_id[row.get("run_id", id(row))] = row
    for row in shard_rows:
        by_id[row.get("run_id", id(row))] = row
    dest.write_text("\n".join(json.dumps(r, default=str)
                              for r in by_id.values()) + "\n")
    for name, n in per_file.items():
        print(f"  {name:28} {n:4} rows")
    print(f"merged {split} -> {dest.name}: {len(by_id)} unique rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
