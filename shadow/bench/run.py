"""Run the benchmark: condition A vs condition B on the EVAL split.

Every task starts from an identical database. Every run is oracle-checked.
Results are appended to results.jsonl, one row per (condition, trial, task).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

from shadow.capture.schema import ToolCatalog, read_catalog
from shadow.config import Config, get_config
from shadow.llm import make_client
from shadow.bench.tasks import Task, eval_tasks
from shadow.route.agent import run_tool_task
from shadow.route.browser_agent import RunResult, run_browser_task
from shadow.route.recipes import RECIPES

from oracle.checks import run_check
from oracle.client import OracleClient
from oracle.reset import reset as reset_instance


def _row(result: RunResult, task: Task, trial: int, success: bool, detail: str,
         provider: str, reset_s: float) -> dict[str, Any]:
    d = result.to_dict()
    d.update({
        "trial": trial,
        "success": success,
        "check_detail": detail,
        "params": task.params,
        "kind": task.kind,
        "provider": provider,
        "reset_s": round(reset_s, 2),
        "ts": time.time(),
    })
    return d


def completed_runs(out_path: Path) -> set[tuple[str, int, str]]:
    """(condition, trial, task) already on disk.

    A long run can be interrupted — a wrapper timeout, a lost container — and
    a real-model rerun of work already paid for is money burned twice. Rows
    are appended as each task finishes, so the file is the resume point.
    """
    done: set[tuple[str, int, str]] = set()
    if not out_path.exists():
        return done
    for line in out_path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue          # a row truncated by a kill mid-write
        done.add((row.get("condition", ""), int(row.get("trial", 0)),
                  row.get("task_id", "")))
    return done


def run_condition(condition: str, tasks: list[Task], cfg: Config,
                  catalog: ToolCatalog, trials: int, out_path: Path,
                  allow_writes: bool, require_verified: bool = True,
                  tool_k: int | None = None, label: str | None = None,
                  resume: bool = False) -> None:
    client = make_client(cfg.models.agent, cfg.models.provider)
    oracle = OracleClient(cfg)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    done = completed_runs(out_path) if resume else set()

    for trial in range(trials):
        for task in tasks:
            if (label or condition, trial, task.id) in done:
                print(f"[{condition}] t{trial} {task.id:34s} SKIP already on disk",
                      flush=True)
                continue
            reset_s = reset_instance()
            oracle.invalidate()          # the session died with the DB
            recipe = RECIPES.get(task.template_id)
            t0 = time.time()
            if condition == "A_browser":
                result = run_browser_task(task, cfg, client, recipe_factory=recipe)
            else:
                result = run_tool_task(task, catalog, cfg, client,
                                       recipe_factory=recipe,
                                       allow_writes=allow_writes,
                                       require_verified=require_verified,
                                       tool_k=tool_k)
                if label:
                    result.condition = label
            check = run_check(task.check, oracle, task.params, result.answer or "")
            row = _row(result, task, trial, check.ok, check.detail,
                       client.provider, reset_s)
            with out_path.open("a") as fh:
                fh.write(json.dumps(row, default=str) + "\n")
            print(f"[{condition}] t{trial} {task.id:34s} "
                  f"{'PASS' if check.ok else 'FAIL'} "
                  f"steps={row['n_steps']:2d} {time.time() - t0:5.1f}s  "
                  f"{check.detail[:70]}", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--trials", type=int, default=None)
    ap.add_argument("--conditions", default="A_browser,B_tools")
    ap.add_argument("--allow-writes", action="store_true",
                    help="expose synthesized write tools to condition B")
    ap.add_argument("--include-unverified", action="store_true")
    ap.add_argument("--out", default=None)
    ap.add_argument("--fresh", action="store_true",
                    help="start a new results file, archiving any completed run")
    ap.add_argument("--resume", action="store_true",
                    help="skip (condition, trial, task) rows already in the "
                         "results file instead of paying for them again")
    ap.add_argument("--tasks", default=None, help="comma-separated task ids")
    ap.add_argument("--require-model", action="store_true",
                    help="fail rather than fall back to the offline provider")
    ap.add_argument("--smoke", action="store_true",
                    help="one task per condition, for checking a provider cheaply")
    ap.add_argument("--tool-k", type=int, default=None,
                    help="tools retrieved into the prompt (default: config)")
    args = ap.parse_args()

    cfg = get_config()
    trials = args.trials or cfg.bench.trials
    out_path = Path(args.out) if args.out else cfg.path("results")
    if args.fresh and out_path.exists():
        # Never unlink without archiving first. The original picked one fixed
        # archive name and, if it was taken, deleted the results anyway --
        # which with a real model in the loop destroys a run that cost money.
        stem = out_path.stem
        archive = next(
            out_path.with_name(f"{stem}_archive_{n}{out_path.suffix}")
            for n in range(1, 1000)
            if not out_path.with_name(
                f"{stem}_archive_{n}{out_path.suffix}").exists())
        archive.write_text(out_path.read_text())
        print(f"archived the previous run to {archive}")
        out_path.unlink()
    catalog = read_catalog(cfg.path("tools"))
    tasks = eval_tasks(cfg)
    if args.tasks:
        wanted = set(args.tasks.split(","))
        tasks = [t for t in tasks if t.id in wanted]

    if args.smoke:
        seen: set[str] = set()
        smoke = []
        for task in tasks:
            if task.template_id not in seen:
                seen.add(task.template_id)
                smoke.append(task)
        tasks = smoke[:1]
        trials = 1

    provider = make_client(cfg.models.agent, cfg.models.provider).provider
    if args.require_model and provider != "litellm":
        print(f"--require-model given but the resolved provider is {provider!r}; "
              "set models.provider and supply credentials", file=sys.stderr)
        return 2
    k = cfg.bench.tool_k if args.tool_k is None else args.tool_k
    print(f"provider: {provider}  model: {cfg.models.agent}  tool_k: {k}")
    print(f"EVAL tasks: {len(tasks)} across "
          f"{len({t.template_id for t in tasks})} held-out templates")
    print(f"catalog: {len(catalog.tools)} tools "
          f"({sum(1 for t in catalog.tools if t.verified)} verified)")

    for condition in args.conditions.split(","):
        run_condition(condition.strip(), tasks, cfg, catalog, trials, out_path,
                      args.allow_writes, not args.include_unverified,
                      tool_k=args.tool_k, resume=args.resume)
    print(f"results appended to {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
