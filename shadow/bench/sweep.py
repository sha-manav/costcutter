"""Coverage vs observation volume — the compounding curve.

One traffic run produces N sessions. Each prefix of that capture is then put
through the full pipeline (filter, segment, induce, verify) and evaluated, so
the curve shows what changes as more of the same user's work is observed.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from shadow.capture.schema import (
    read_records, write_catalog, write_episodes, write_jsonl,
)
from shadow.config import Config, REPO_ROOT, get_config
from shadow.distill.emit import emit
from shadow.distill.filter import filter_records
from shadow.distill.induce import induce
from shadow.distill.segment import segment
from shadow.bench.metrics import score_condition, load_results


def session_cutoffs(manifest_path: Path) -> list[float]:
    entries = json.loads(manifest_path.read_text())
    sessions = sorted({e["session"] for e in entries})
    return [max(e["ts_end"] for e in entries if e["session"] <= s) for s in sessions]


def pipeline_for_prefix(cfg: Config, cutoff: float, tag: str) -> dict[str, Any]:
    records = [r for r in read_records(cfg.path("capture")) if r.ts_end <= cutoff]
    kept, stats = filter_records(records)
    episodes = segment(kept, cfg).episodes
    catalog = induce(episodes, cfg).catalog
    out_dir = cfg.path("artifacts") / "sweep" / tag
    out_dir.mkdir(parents=True, exist_ok=True)
    write_jsonl(out_dir / "filtered.jsonl", kept)
    write_episodes(out_dir / "episodes.json", episodes)
    write_catalog(out_dir / "tools.json", catalog)
    return {"records": len(records), "kept": len(kept),
            "episodes": len(episodes), "tools": len(catalog.tools),
            "catalog_path": str(out_dir / "tools.json"),
            "retention": round(stats.retention, 4)}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--verify", action="store_true",
                    help="replay-verify each prefix's catalog (slower, accurate)")
    ap.add_argument("--bench", action="store_true",
                    help="run condition B on EVAL for each prefix")
    args = ap.parse_args()
    cfg = get_config()
    manifest = cfg.path("artifacts") / "observe_manifest.json"
    if not manifest.exists():
        print("no observe manifest; run `python -m shadow.cli observe` first")
        return 2

    points: list[dict[str, Any]] = []
    for i, cutoff in enumerate(session_cutoffs(manifest), start=1):
        tag = f"s{i}"
        point = {"sessions": i, **pipeline_for_prefix(cfg, cutoff, tag)}
        catalog_path = Path(point.pop("catalog_path"))

        if args.verify:
            from shadow.capture.schema import read_catalog
            from shadow.verify.replay import verify_catalog

            catalog = read_catalog(catalog_path)
            verify_catalog(catalog, cfg, allow_writes=False)
            write_catalog(catalog_path, catalog)
            point["verified_tools"] = sum(1 for t in catalog.tools if t.verified)

        if args.bench:
            from shadow.bench.run import run_condition
            from shadow.bench.tasks import eval_tasks
            from shadow.capture.schema import read_catalog

            results_path = cfg.path("artifacts") / "sweep" / tag / "results.jsonl"
            results_path.unlink(missing_ok=True)
            run_condition("B_tools", eval_tasks(cfg), cfg, read_catalog(catalog_path),
                          1, results_path, allow_writes=False)
            metrics = score_condition(load_results(results_path), "B_tools", cfg)
            point["coverage"] = round(metrics.coverage, 4)
            point["success_rate"] = round(metrics.success_rate, 4)
            point["usd_per_task"] = round(metrics.usd_per_task, 6)

        from shadow.bench.attainable import run as attainable_run
        from shadow.capture.schema import read_catalog

        # Point the attainable probe at this prefix's catalog.
        real_tools = cfg.path("tools")
        backup = real_tools.with_suffix(".bak") if real_tools.exists() else None
        if backup:
            real_tools.replace(backup)
        write_catalog(real_tools, read_catalog(catalog_path))
        try:
            probes = attainable_run(cfg, allow_writes=False,
                                    require_verified=args.verify)
            point["attainable_coverage"] = round(
                sum(1 for p in probes if p.attainable) / len(probes), 4) if probes else 0.0
        finally:
            if backup:
                backup.replace(real_tools)

        points.append(point)
        print(json.dumps(point), flush=True)

    out = cfg.path("artifacts") / "coverage_sweep.json"
    out.write_text(json.dumps(points, indent=2))
    print(f"sweep written to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
