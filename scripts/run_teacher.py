"""Round 0 runner — Sonnet teacher traces, corrected harness, Firm A.

Writes every rollout, accepted or not, so the acceptance rate is measurable
rather than assumed, and so a rejected trajectory can be inspected instead of
vanishing. Only accepted ones carry `training: true`.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import erpbench.calibration               # noqa: E402,F401
import erpbench.evaluation                # noqa: E402,F401
import erpbench.evaluation_extra          # noqa: E402,F401
from erpbench import gate, teacher        # noqa: E402
from erpbench.adapter import make_adapter  # noqa: E402
from erpbench.firms import get_firm       # noqa: E402
from erpbench.preflight import record_spend, remaining, spent_on  # noqa: E402


def main() -> int:
    from shadow.config import get_config
    from shadow.llm import make_client, resolve_model_provider

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--model", default="claude-sonnet-5")
    ap.add_argument("--total", type=int, default=400)
    ap.add_argument("--site", default="erp01.localhost")
    ap.add_argument("--shard", default=None, metavar="I/N")
    ap.add_argument("--out", type=Path, default=teacher.TRACES)
    ap.add_argument("--line-item", default="teacher_traces")
    ap.add_argument("--max-steps", type=int, default=14)
    ap.add_argument("--resume", action="store_true")
    args = ap.parse_args()

    resolve_model_provider(args.model, "litellm")     # refuses without a key

    specs = teacher.plan(args.total)
    if args.shard:
        i, n = (int(x) for x in args.shard.split("/"))
        specs = [s for k, s in enumerate(specs) if k % n == i - 1]

    done: set[str] = set()
    if args.resume and args.out.exists():
        done = {json.loads(l)["run_id"]
                for l in args.out.read_text().splitlines() if l.strip()}

    cfg = get_config()
    adapter = make_adapter("erpnext", site=args.site)
    client = make_client(args.model, "litellm")
    firm = get_firm("A")

    kept = seen = 0
    args.out.parent.mkdir(parents=True, exist_ok=True)
    for k, spec in enumerate(specs, start=1):
        left = remaining(args.line_item)
        if left <= 0:
            print(f"hard ceiling on {args.line_item!r}; stopping", file=sys.stderr)
            break
        job = gate.Job(spec.template, firm, "corrected", args.model, 0,
                       spec.seed, line_item=args.line_item)
        if job.rid in done:
            continue
        instance = teacher.instantiate(spec)
        try:
            row = gate.run_one(job, adapter, client, cfg,
                               max_steps=args.max_steps,
                               instance=instance)
        except gate.GateHalt as exc:
            print(f"HALTED: {exc}", file=sys.stderr)
            break

        ok, why = teacher.accept(row)
        stage = teacher.restage(row, spec.stage)
        row.update({"category": spec.category, "planned_stage": spec.stage,
                    "stage": stage, "training": ok, "rejected_because": None if ok else why,
                    "recovered": teacher.is_hard_recovery(row)})
        with args.out.open("a") as fh:
            fh.write(json.dumps(row, default=str) + "\n")
        record_spend("anthropic", args.model, job.rid, args.line_item,
                     row["usage"]["input_tokens"], row["usage"]["output_tokens"],
                     row["usage"]["usd"], row["usage"]["cached_input_tokens"])
        seen += 1
        kept += ok
        print(f"  [{k}/{len(specs)}] {spec.category:14} {stage} "
              f"{'KEEP' if ok else 'drop':4} {why:22} "
              f"rec={row['recovered']!s:5} ${spent_on(args.line_item):.2f}")

    print(f"\n{kept}/{seen} accepted ({kept/max(1,seen):.0%})   "
          f"spent ${spent_on(args.line_item):.2f} of the line")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
