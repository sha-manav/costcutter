#!/usr/bin/env python3
"""Spot-check segmentation against what was actually demonstrated.

The demonstration manifest records which template each demonstration drove
and when. Episodes are recovered from the traffic alone, with no access to
that manifest, so lining the two up afterwards measures how well the
segmenter recovered the task boundaries.

    python scripts/segmentation_check.py [--limit 20]
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shadow.capture.schema import read_episodes  # noqa: E402
from shadow.config import get_config  # noqa: E402


def overlap(a_start: float, a_end: float, b_start: float, b_end: float) -> float:
    return max(0.0, min(a_end, b_end) - max(a_start, b_start))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int, default=20)
    args = ap.parse_args()

    cfg = get_config()
    manifest_path = cfg.path("artifacts") / "observe_manifest.json"
    if not manifest_path.exists():
        print("no observe manifest", file=sys.stderr)
        return 2
    entries = json.loads(manifest_path.read_text())
    episodes = read_episodes(cfg.path("episodes"))
    if not episodes:
        print("no episodes; run `python -m shadow.cli distill` first", file=sys.stderr)
        return 2

    by_template: dict[str, Counter] = defaultdict(Counter)
    spanning = 0
    unmatched = 0
    rows = []
    for ep in episodes:
        best, best_overlap = None, 0.0
        touched = 0
        for entry in entries:
            o = overlap(ep.ts_start, ep.ts_end, entry["ts_start"], entry["ts_end"])
            if o > 0:
                touched += 1
            if o > best_overlap:
                best, best_overlap = entry, o
        if best is None:
            unmatched += 1
            continue
        if touched > 1:
            spanning += 1
        by_template[best["template_id"]][ep.label or "(none)"] += 1
        rows.append((ep.id, len(ep.records), best["template_id"], ep.label))

    print(f"episodes: {len(episodes)}  demonstrations: {len(entries)}")
    print(f"episodes spanning more than one demonstration: {spanning}")
    print(f"episodes overlapping no demonstration: {unmatched}  "
          "(login, idle navigation)")
    print()
    print("what each demonstrated template was labelled as:")
    for template_id in sorted(by_template):
        labels = ", ".join(f"{label!r}×{n}"
                           for label, n in by_template[template_id].most_common(3))
        print(f"  {template_id:28s} {labels}")
    print()
    print(f"first {args.limit} episodes:")
    for ep_id, n, template_id, label in rows[:args.limit]:
        print(f"  {ep_id} n={n:3d}  demonstrated={template_id:28s} label={label!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
