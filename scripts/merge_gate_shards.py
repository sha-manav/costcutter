"""Merge per-shard gate outputs into one results file.

Each shard writes its own file so that no two processes append to the same
descriptor. Merging is by `run_id`, last write winning, which is the same
rule `gate.latest_rows` applies within a file -- a row re-run under changed
assertions or a changed harness supersedes its predecessor rather than being
counted twice.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

SHARDS = REPO / "artifacts" / "gate_shards"
DEST = REPO / "artifacts" / "calibration_gate.jsonl"


def main() -> int:
    files = sorted(SHARDS.glob("shard_*.jsonl"))
    if not files:
        print(f"no shard files in {SHARDS}", file=sys.stderr)
        return 1
    by_id: dict[str, dict] = {}
    if DEST.exists():                       # keep anything already measured
        for line in DEST.read_text().splitlines():
            if line.strip():
                row = json.loads(line)
                by_id[row.get("run_id", id(row))] = row
    per_file = {}
    for f in files:
        n = 0
        for line in f.read_text().splitlines():
            if line.strip():
                row = json.loads(line)
                by_id[row.get("run_id", id(row))] = row
                n += 1
        per_file[f.name] = n
    DEST.write_text("\n".join(json.dumps(r, default=str)
                              for r in by_id.values()) + "\n")
    for name, n in per_file.items():
        print(f"  {name:28} {n:4} rows")
    print(f"merged -> {DEST.name}: {len(by_id)} unique rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
