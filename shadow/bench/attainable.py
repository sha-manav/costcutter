"""Attainable coverage: what the *catalog* can do, independent of the router.

Achieved coverage (from results.jsonl) confounds two things: whether a tool
exists that could serve a held-out task, and whether the agent's router
picked it. This probe measures only the first. It is allowed to use the
oracle to construct arguments, so it is an UPPER bound — the number a
perfect router would reach — and is reported alongside, never instead of,
achieved coverage.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass, field
from typing import Any

from shadow.capture.schema import ToolSpec, read_catalog
from shadow.config import Config, get_config
from shadow.bench.tasks import Task, eval_tasks
from shadow.route.agent import extract_answer
from shadow.serve.executor import ToolExecutor
from shadow.verify.replay import watched_doctypes

from oracle.checks import run_check
from oracle.client import OracleClient
from oracle.reset import reset as reset_instance

# Oracle-side argument hints: the doctype and filter each held-out task is
# really about. This is privileged information, which is exactly why the
# resulting number is labelled an upper bound.
HINTS: dict[str, dict[str, Any]] = {
    "T01_customer_outstanding": {
        "doctype": "Sales Invoice", "field": "customer",
        "fields": ["outstanding_amount"]},
    "T02_overdue_invoices": {
        "doctype": "Sales Invoice", "field": "status", "value": "Overdue",
        "fields": ["name"]},
    "T03_stock_on_hand": {
        "doctype": "Bin", "field": "item_code", "fields": ["actual_qty"]},
    "T04_customer_open_orders": {
        "doctype": "Sales Order", "field": "customer", "fields": ["name"]},
    "T05_item_price": {
        "doctype": "Item Price", "field": "item_code",
        "fields": ["price_list_rate"]},
    "T06_latest_order_total": {
        "doctype": "Sales Order", "field": "customer", "fields": ["grand_total"]},
    "T07_items_below_price": {
        "doctype": "Item Price", "field": None, "fields": ["price_list_rate"]},
    "T08_create_customer": {"doctype": "Customer"},
    "T09_create_sales_order": {"doctype": "Sales Order"},
    "T10_create_supplier": {"doctype": "Supplier"},
    "T11_update_item_price": {"doctype": "Item Price"},
    "T12_create_item": {"doctype": "Item"},
    "T13_update_customer_group": {"doctype": "Customer"},
    "T14_create_sales_invoice": {"doctype": "Sales Invoice"},
}


@dataclass
class Probe:
    task_id: str
    template_id: str
    attainable: bool = False
    tool: str | None = None
    detail: str = ""
    attempts: list[dict[str, Any]] = field(default_factory=list)


def _filter_value(task: Task, hint: dict[str, Any]) -> Any:
    if "value" in hint:
        return hint["value"]
    field_name = hint.get("field")
    if not field_name:
        return None
    for key in ("customer", "item_code", "supplier_name", "customer_name"):
        if key in task.params and (key == field_name or field_name in key):
            return task.params[key]
    return next(iter(task.params.values()), None)


def build_arguments(spec: ToolSpec, task: Task) -> dict[str, Any]:
    """Fill the tool's parameters using the oracle hint for this template."""
    hint = HINTS.get(task.template_id, {})
    args: dict[str, Any] = dict(spec.example_args)
    value = _filter_value(task, hint)
    for name, schema in spec.params_schema.get("properties", {}).items():
        lname = name.lower()
        if "doctype" in lname and hint.get("doctype"):
            args[name] = hint["doctype"]
        elif lname.startswith("filter"):
            args[name] = ([[hint["field"], "=", value]]
                          if hint.get("field") and value is not None else [])
        elif lname == "fields" and hint.get("fields"):
            args[name] = ["name"] + list(hint["fields"])
        elif "order_by" in lname:
            # The observed value names the record type it was captured on
            # (`\`tabItem Price\`.\`modified\` desc`), which is a SQL error
            # against any other one.
            args[name] = "modified desc"
        elif lname in {"page_length", "limit", "limit_page_length"}:
            args[name] = 200
        elif lname in task.params:
            args[name] = task.params[lname]
    return args


def probe_task(task: Task, tools: list[ToolSpec], executor: ToolExecutor,
               oracle: OracleClient, reset_between_writes: bool) -> Probe:
    probe = Probe(task_id=task.id, template_id=task.template_id)
    hint = HINTS.get(task.template_id, {})
    target = hint.get("doctype")

    for spec in tools:
        if task.kind == "read" and spec.mutation_class != "read":
            continue
        if task.kind == "write":
            if spec.mutation_class == "read":
                continue
            # A write tool can only serve a doctype it can actually address.
            addressable = watched_doctypes(spec)
            parameterised = any(
                "doctype" in name.lower()
                for name in spec.params_schema.get("properties", {}))
            if target and not parameterised and target not in addressable:
                probe.attempts.append({
                    "tool": spec.name, "ok": False,
                    "why": f"doctype is a literal ({addressable}); "
                           f"cannot address {target}"})
                continue
            if reset_between_writes:
                reset_instance()
                oracle.invalidate()

        args = build_arguments(spec, task)
        result = executor.execute(spec, args)
        if not result.ok:
            probe.attempts.append({"tool": spec.name, "ok": False,
                                   "why": (result.error or "")[:160]})
            continue
        answer = extract_answer(task.goal, result) if task.kind == "read" else "done"
        check = run_check(task.check, oracle, task.params, answer or "")
        probe.attempts.append({"tool": spec.name, "ok": check.ok,
                               "answer": answer, "why": check.detail[:160]})
        if check.ok:
            probe.attainable = True
            probe.tool = spec.name
            probe.detail = check.detail
            break
    if not probe.attainable and probe.attempts:
        probe.detail = probe.attempts[-1].get("why", "")
    return probe


def run(cfg: Config | None = None, allow_writes: bool = False,
        require_verified: bool = True) -> list[Probe]:
    cfg = cfg or get_config()
    catalog = read_catalog(cfg.path("tools"))
    tools = [t for t in catalog.tools if t.verified or not require_verified]
    executor = ToolExecutor(cfg, allow_writes=allow_writes)
    oracle = OracleClient(cfg)
    reset_instance()
    oracle.invalidate()
    probes = []
    for task in eval_tasks(cfg):
        probes.append(probe_task(task, tools, executor, oracle,
                                 reset_between_writes=allow_writes))
        print(f"[attainable] {task.id:34s} "
              f"{'YES via ' + str(probes[-1].tool) if probes[-1].attainable else 'no'}",
              flush=True)
    reset_instance()
    return probes


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--allow-writes", action="store_true")
    ap.add_argument("--include-unverified", action="store_true")
    args = ap.parse_args()
    cfg = get_config()
    probes = run(cfg, args.allow_writes, not args.include_unverified)
    attainable = sum(1 for p in probes if p.attainable)
    payload = {
        "tasks": len(probes),
        "attainable": attainable,
        "rate": round(attainable / len(probes), 4) if probes else 0.0,
        "by_template": {},
        "probes": [asdict(p) for p in probes],
    }
    for template_id in sorted({p.template_id for p in probes}):
        rows = [p for p in probes if p.template_id == template_id]
        payload["by_template"][template_id] = round(
            sum(1 for p in rows if p.attainable) / len(rows), 3)
    out = cfg.path("artifacts") / "attainable_coverage.json"
    out.write_text(json.dumps(payload, indent=2, default=str))
    print(f"attainable on EVAL: {attainable}/{len(probes)} "
          f"({payload['rate']:.0%}) -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
