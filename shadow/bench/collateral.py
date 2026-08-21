"""Task 4: does a capable model make the write mistake a weak router made?

The lexical router, asked to create a sales order, selected `create_customer`
and succeeded — ERPNext created a customer nobody asked for, the browser
fallback then completed the real task, and the oracle scored a pass. Nine
times in fifty-four runs. The oracle checks a post-condition, so it is blind
to anything else the run touched.

This runs the same four create-type EVAL templates with writes enabled and
diffs the database around each run, so collateral is measured rather than
assumed. It is an experiment, not a configuration: `allow_writes` goes back
to false afterwards.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from shadow.capture.schema import read_catalog
from shadow.config import Config, get_config
from shadow.llm import MissingCredentials, make_client, resolve_model_provider
from shadow.bench.exclusive import instance_lock
from shadow.bench.tasks import Task, eval_tasks
from shadow.route.agent import run_tool_task
from shadow.route.recipes import RECIPES

from oracle.checks import run_check
from oracle.client import OracleClient
from oracle.reset import reset as reset_instance

# Every record type the four create-type EVAL templates could plausibly
# touch, plus the ones the observed write tools can address. A diff over
# this set catches a tool that created the wrong kind of record.
WATCHED_DOCTYPES = (
    "Customer", "Supplier", "Item", "Sales Order", "Sales Invoice",
    "Contact", "Lead", "Warehouse", "Item Group", "Supplier Group",
    "Territory", "Item Price", "Address",
)

# What each template is *entitled* to create. Anything else is collateral.
INTENDED: dict[str, set[str]] = {
    "T09_create_sales_order": {"Sales Order"},
    "T10_create_supplier": {"Supplier"},
    "T12_create_item": {"Item", "Item Price"},
    "T14_create_sales_invoice": {"Sales Invoice"},
}


@dataclass
class CollateralRun:
    task_id: str
    template_id: str
    success: bool
    intended: dict[str, int] = field(default_factory=dict)
    collateral: dict[str, int] = field(default_factory=dict)
    tools_called: list[str] = field(default_factory=list)
    # tool / tool_failed / browser_fallback, per acting step
    served_by: list[str] = field(default_factory=list)
    detail: str = ""

    @property
    def had_collateral(self) -> bool:
        return bool(self.collateral)


def snapshot(oracle: OracleClient) -> dict[str, int]:
    counts: dict[str, int] = {}
    for doctype in WATCHED_DOCTYPES:
        try:
            counts[doctype] = oracle.count(doctype)
        except Exception:
            counts[doctype] = -1
    return counts


def run(cfg: Config | None = None, trials: int = 1,
        out_path: Path | None = None) -> list[CollateralRun]:
    cfg = cfg or get_config()
    catalog = read_catalog(cfg.path("tools"))
    client = make_client(cfg.models.agent, cfg.models.provider)
    oracle = OracleClient(cfg)
    tasks = [t for t in eval_tasks(cfg) if t.template_id in INTENDED]
    out_path = out_path or (cfg.path("artifacts") / "collateral_writes.json")

    results: list[CollateralRun] = []
    for trial in range(trials):
        for task in tasks:
            reset_instance()
            oracle.invalidate()
            before = snapshot(oracle)

            result = run_tool_task(task, catalog, cfg, client,
                                   recipe_factory=RECIPES.get(task.template_id),
                                   allow_writes=True)
            oracle.invalidate()
            after = snapshot(oracle)
            check = run_check(task.check, oracle, task.params, result.answer or "")

            allowed = INTENDED[task.template_id]
            intended: dict[str, int] = {}
            collateral: dict[str, int] = {}
            for doctype, count in after.items():
                delta = count - before.get(doctype, 0)
                if delta <= 0 or count < 0 or before.get(doctype, 0) < 0:
                    continue
                (intended if doctype in allowed else collateral)[doctype] = delta

            record = CollateralRun(
                task_id=task.id, template_id=task.template_id, success=check.ok,
                intended=intended, collateral=collateral,
                tools_called=[s.action.get("name", "?") for s in result.steps
                              if s.action.get("action") == "tool"],
                served_by=[s.served_by for s in result.steps
                           if s.action.get("action") != "done"],
                detail=check.detail[:160])
            results.append(record)
            flag = "COLLATERAL " + json.dumps(collateral) if collateral else "clean"
            print(f"[collateral] t{trial} {task.id:32s} "
                  f"{'PASS' if check.ok else 'FAIL'} {flag}", flush=True)

    reset_instance()
    payload = {
        "model": cfg.models.agent,
        "provider": client.provider,
        "runs": len(results),
        "runs_with_collateral": sum(1 for r in results if r.had_collateral),
        "passes_with_collateral": sum(1 for r in results
                                      if r.had_collateral and r.success),
        "watched_doctypes": list(WATCHED_DOCTYPES),
        "results": [asdict(r) for r in results],
    }
    out_path.write_text(json.dumps(payload, indent=2))
    print(f"\n{payload['runs_with_collateral']}/{payload['runs']} runs produced "
          f"collateral writes; {payload['passes_with_collateral']} of those were "
          f"scored as passes -> {out_path}")
    return results


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--trials", type=int, default=1)
    ap.add_argument("--require-model", action="store_true",
                    help="fail rather than fall back to the offline provider")
    args = ap.parse_args()
    cfg = get_config()
    # The whole point is to re-test the finding against a capable model.
    # Silently re-measuring the lexical router would answer a question we
    # already have an answer to.
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
    with instance_lock("bench.collateral"):
        run(cfg, args.trials)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
