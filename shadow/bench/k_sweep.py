"""Sweep the number of tools put in the prompt.

Isolates the two things that were tangled in the first result: what a tool
costs to *offer* and what it saves when it is *used*. k=0 is the browser
baseline reached through the tool agent; k at or above the catalog size is
the whole-catalog behaviour that lost.

One trial per point by default. Under the offline provider the policy is
deterministic, so repeating a point reproduces its cost and coverage exactly
and varies only wall clock; the trials budget is better spent on the main
A/B comparison. Pass --trials to change it, and do change it when running
against a real model, whose replies are not deterministic.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from shadow.capture.schema import read_catalog
from shadow.config import Config, get_config
from shadow.llm import MissingCredentials, resolve_model_provider
from shadow.bench.exclusive import instance_lock
from shadow.bench.metrics import load_results, score_condition
from shadow.bench.run import run_condition
from shadow.bench.tasks import eval_tasks

DEFAULT_KS = (0, 1, 3, 8)


def sweep(ks: tuple[int, ...], trials: int, cfg: Config | None = None,
          allow_writes: bool = False,
          resume: bool = False) -> list[dict[str, Any]]:
    cfg = cfg or get_config()
    catalog = read_catalog(cfg.path("tools"))
    tasks = eval_tasks(cfg)
    out_dir = cfg.path("artifacts") / "k_sweep"
    out_dir.mkdir(parents=True, exist_ok=True)

    points: list[dict[str, Any]] = []
    for k in ks:
        results_path = out_dir / f"k{k}.jsonl"
        # Under a real model each point costs money, so a resumed sweep keeps
        # what it already paid for. Without --resume the point starts clean.
        if not resume:
            results_path.unlink(missing_ok=True)
        run_condition("B_tools", tasks, cfg, catalog, trials, results_path,
                      allow_writes=allow_writes, tool_k=k, resume=resume)
        rows = load_results(results_path)
        m = score_condition(rows, "B_tools", cfg)
        prompt_chars = [step["prompt_chars"] for row in rows
                        for step in row.get("steps", [])]
        no_catalog = sum(1 for row in rows
                         if row.get("retrieval", {}).get("below_floor"))
        point = {
            "k": k,
            "runs": m.n_runs,
            "success_rate": round(m.success_rate, 4),
            "usd_per_successful_task": round(m.usd_per_successful_task, 6),
            "usd_per_successful_task_uncached": round(
                m.usd_per_successful_task_uncached, 6),
            "usd_per_task": round(m.usd_per_task, 6),
            "p50_latency_s": round(m.p50_latency_s, 2),
            "p95_latency_s": round(m.p95_latency_s, 2),
            "coverage": round(m.coverage, 4),
            "task_coverage": round(m.task_coverage, 4),
            "mean_steps": round(m.mean_steps, 2),
            "failed_tool_actions": m.failed_tool_actions,
            "mean_prompt_chars": round(sum(prompt_chars) / len(prompt_chars), 1)
            if prompt_chars else 0.0,
            "runs_with_no_catalog": no_catalog,
            "simulated_policy": m.simulated_policy,
        }
        points.append(point)
        print(json.dumps(point), flush=True)
    return points


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--ks", default=",".join(str(k) for k in DEFAULT_KS))
    ap.add_argument("--trials", type=int, default=1)
    ap.add_argument("--allow-writes", action="store_true")
    ap.add_argument("--resume", action="store_true",
                    help="keep points already on disk instead of re-paying")
    ap.add_argument("--require-model", action="store_true",
                    help="fail rather than fall back to the offline provider")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    cfg = get_config()
    try:
        provider = resolve_model_provider(cfg.models.agent, cfg.models.provider)
    except MissingCredentials as exc:
        if args.require_model:
            print(f"--require-model: {exc}", file=sys.stderr)
            return 2
        raise
    if args.require_model and provider != "litellm":
        print(f"--require-model given but the resolved provider is "
              f"{provider!r}", file=sys.stderr)
        return 2
    ks = tuple(int(x) for x in args.ks.split(","))
    with instance_lock("bench.k_sweep"):
        points = sweep(ks, args.trials, cfg, args.allow_writes, args.resume)
    out = Path(args.out) if args.out else cfg.path("artifacts") / "k_sweep.json"
    out.write_text(json.dumps(points, indent=2))
    print(f"k sweep written to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
