"""Measure end-to-end rollout throughput — SPEC §6.

Runs the *real* rollout path — reset, prepare, snapshot, agent loop, snapshot,
diff, verify — with a canned model so that what is measured is the
environment rather than the provider. Model latency is added back afterwards
from observed figures, because it varies by an order of magnitude between
serving paths and mixing the two produces a number that describes neither.

One process per site, matching how generation actually runs, because a
threaded measurement would understate contention that a process-per-site
deployment really pays.
"""
from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))


def _worker(site: str, n: int, seed_offset: int, q) -> None:
    import erpbench.calibration            # noqa: F401
    import erpbench.evaluation             # noqa: F401
    import erpbench.evaluation_extra        # noqa: F401
    from erpbench import gate
    from erpbench.adapter import ERPNextAdapter
    from erpbench.firms import get_firm
    from erpbench.templates import REGISTRY, seeds_for
    from shadow.config import get_config
    from shadow.llm import LLMResponse, LLMUsage

    M = "openrouter/qwen/qwen3-14b"

    class Canned:
        """A model that answers instantly. Isolates environment cost."""
        provider = "litellm"

        def __init__(self, replies):
            self.replies, self.i = replies, 0

        def complete(self, messages, **kw):
            t = self.replies[min(self.i, len(self.replies) - 1)]
            self.i += 1
            return LLMResponse(text=t, usage=LLMUsage(
                model=M, input_tokens=1, output_tokens=1))

    cfg = get_config()
    ad = ERPNextAdapter(base_url="http://localhost:8080", site=site)
    templates = REGISTRY.evaluation
    read_only = json.dumps({"action": "query", "doctype": "Customer",
                            "fields": ["name"]})
    done = json.dumps({"action": "done", "answer": "none"})
    write = json.dumps({"action": "create", "doctype": "Supplier",
                        "fields": {"supplier_name": "Throughput Probe",
                                   "supplier_group": "Services"}})

    t0 = time.time()
    ok = 0
    for k in range(n):
        t = templates[(k + seed_offset) % len(templates)]
        firm = get_firm("AB"[k % 2])
        # Match the observed mutation rate rather than assuming a split.
        # Across the 2,160-row evaluation baseline only 14% of rollouts
        # changed anything, so 86% can skip their reset -- and the two paths
        # differ by an order of magnitude in cost, which makes the mix the
        # single most important input to this measurement.
        replies = [write, done] if k % 7 == 0 else [read_only, done]
        job = gate.Job(t, firm, "corrected", M, 0,
                       seeds_for(t.template_id, firm.firm_id, 1)[0])
        try:
            gate.run_one(job, ad, Canned(replies), cfg, max_steps=4)
            ok += 1
        except Exception:
            pass
    q.put((site, ok, time.time() - t0))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sites", type=int, default=12)
    ap.add_argument("--per-site", type=int, default=12)
    args = ap.parse_args()

    q = mp.Queue()
    procs = []
    t0 = time.time()
    for i in range(1, args.sites + 1):
        p = mp.Process(target=_worker,
                       args=(f"erp{i:02d}.localhost", args.per_site, i * 7, q))
        p.start()
        procs.append(p)
    for p in procs:
        p.join()
    wall = time.time() - t0

    results = [q.get() for _ in range(len(procs))]
    total = sum(r[1] for r in results)
    print(f"{total} rollouts across {args.sites} sites in {wall:.1f}s")
    print(f"  environment-only throughput: {total / wall * 3600:,.0f} rollouts/hour")
    per = wall / max(1, total / args.sites)
    print(f"  per-rollout, per-site: {per:.2f}s")
    print()
    print("  end-to-end with model latency added back:")
    for label, lat in (("Fireworks-served 14B (~3s/step, 3 steps)", 9.0),
                       ("OpenRouter 14B observed (~13s/step, 3 steps)", 39.0)):
        eff = wall / max(1, total) * args.sites + lat
        print(f"    {label:44} {args.sites / eff * 3600:,.0f} rollouts/hour")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
