"""Measure rollouts/hour — SPEC §6.

This is the one input the spec cannot supply: the dataset target is sized
against it, so it must be measured on the real environment rather than
assumed.

What counts as one rollout here is everything the environment does around a
task, and nothing the model does:

    reset the site · snapshot before · run a representative action sequence ·
    snapshot after · diff

Model latency is deliberately excluded. It varies by an order of magnitude
across the models on the ladder, and SPEC §6's claim is that throughput is
bounded by the ERP rather than the GPU -- so the number that sizes the
corpus is the ERP's ceiling. A real rollout is this plus model time.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import statistics
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from erpbench.adapter import ERPNextAdapter
from erpbench.sites import Site, provision_pool, restore


@dataclass
class RolloutTiming:
    site: str
    ok: bool
    total_s: float
    reset_s: float
    snapshot_s: float
    actions_s: float
    n_actions: int
    error: str = ""


def representative_rollout(site: Site) -> RolloutTiming:
    """One task's worth of environment work.

    The action sequence mirrors the shape of the benchmark's write tasks --
    look something up, create a parent document, populate a child table,
    read it back -- because a rollout made only of reads would understate
    the cost of the ones that matter.
    """
    t0 = time.time()
    adapter = ERPNextAdapter(base_url=site.base_url, site=site.name)
    reset_s = snap_s = act_s = 0.0
    n = 0
    try:
        t = time.time()
        restore(site)
        adapter.invalidate()
        reset_s = time.time() - t

        t = time.time()
        before = adapter.snapshot()
        snap_s += time.time() - t

        t = time.time()
        client = adapter._client()
        adapter.query("Customer", filters=[["customer_name", "like", "%a%"]],
                      fields=["name"], limit=10); n += 1
        adapter.query("Item", fields=["name", "item_name"], limit=10); n += 1
        customers = adapter.query("Customer", fields=["name"], limit=1)
        items = adapter.query("Item", fields=["name"], limit=1)
        n += 2
        r = client.post("/api/resource/Sales Order", json={
            "customer": customers[0]["name"],
            "delivery_date": "2026-12-31",
            "items": [{"item_code": items[0]["name"], "qty": 3,
                       "rate": 100, "delivery_date": "2026-12-31"}]})
        n += 1
        if r.status_code not in (200, 201):
            raise RuntimeError(f"create failed {r.status_code}: {r.text[:160]}")
        name = r.json()["data"]["name"]
        adapter.read("Sales Order", name); n += 1
        act_s = time.time() - t

        t = time.time()
        after = adapter.snapshot()
        diff = adapter.diff(before, after)
        snap_s += time.time() - t
        if not diff.created:
            raise RuntimeError("diff saw no created rows")
        return RolloutTiming(site.name, True, time.time() - t0, reset_s,
                             snap_s, act_s, n)
    except Exception as exc:
        return RolloutTiming(site.name, False, time.time() - t0, reset_s,
                             snap_s, act_s, n, f"{type(exc).__name__}: {exc}")
    finally:
        adapter.close()


def measure(pool: list[Site], rounds: int = 2) -> dict[str, Any]:
    """Run `rounds` rollouts on every site, one worker per site.

    A site is a resource owned by exactly one worker. Handing the same site
    to two workers lets one rollout's reset drop the database another is
    mid-snapshot on -- which shows up as "table doesn't exist" and reads as
    an environment limit when it is really a scheduling bug.
    """
    def _serial(site: Site) -> list[RolloutTiming]:
        return [representative_rollout(site) for _ in range(rounds)]

    t0 = time.time()
    with cf.ThreadPoolExecutor(max_workers=len(pool)) as ex:
        timings = [t for batch in ex.map(_serial, pool) for t in batch]
    wall = time.time() - t0

    ok = [t for t in timings if t.ok]
    per = [t.total_s for t in ok]
    return {
        "sites": len(pool),
        "rollouts_attempted": len(timings),
        "rollouts_ok": len(ok),
        "wall_s": round(wall, 2),
        "rollouts_per_hour": round(len(ok) / wall * 3600, 1) if wall else 0.0,
        "per_rollout_s": {
            "mean": round(statistics.fmean(per), 2) if per else 0.0,
            "median": round(statistics.median(per), 2) if per else 0.0,
            "max": round(max(per), 2) if per else 0.0,
        },
        "breakdown_mean_s": {
            "reset": round(statistics.fmean([t.reset_s for t in ok]), 2) if ok else 0,
            "snapshots": round(statistics.fmean([t.snapshot_s for t in ok]), 2) if ok else 0,
            "actions": round(statistics.fmean([t.actions_s for t in ok]), 2) if ok else 0,
        },
        "failures": [{"site": t.site, "error": t.error} for t in timings if not t.ok][:5],
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sites", type=int, default=8)
    ap.add_argument("--rounds", type=int, default=2)
    ap.add_argument("--sweep", default="", help="comma-separated site counts")
    ap.add_argument("--out", default="artifacts/throughput.json")
    args = ap.parse_args()

    pool = provision_pool(args.sites)
    counts = ([int(x) for x in args.sweep.split(",")] if args.sweep
              else [args.sites])
    results = []
    for n in counts:
        res = measure(pool[:n], args.rounds)
        results.append(res)
        print(json.dumps(res), flush=True)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, indent=2))
    print(f"\nthroughput -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
