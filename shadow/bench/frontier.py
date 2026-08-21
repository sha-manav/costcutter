"""The model ladder: success against cost, one point per (model, condition).

The interesting axis is not which condition wins -- that was settled, and the
tool condition lost -- but how far a properly instrumented harness moves a
given model, and how cheap a model can be run once it is.

The pre-fix Sonnet run is included as a labelled point on purpose. It is the
naive-harness baseline, and the distance from it to the instrumented points
is the whole finding.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from shadow.config import Config, get_config
from shadow.bench.metrics import load_results, score_condition

# (label, harness, path). A run that has not been produced yet is skipped
# rather than faked, so a partial ladder still plots.
LADDER: tuple[tuple[str, str, str], ...] = (
    ("Sonnet", "naive (pre-fix)", "artifacts/results_prefix_harness.jsonl"),
    ("Sonnet", "instrumented", "artifacts/results.jsonl"),
    ("Haiku", "instrumented", "artifacts/ladder/haiku/results.jsonl"),
    ("Opus", "instrumented", "artifacts/ladder/opus/results.jsonl"),
)


@dataclass
class Point:
    model: str
    harness: str
    condition: str
    n_runs: int
    success_rate: float
    usd_per_successful_task: float
    usd_per_successful_task_uncached: float
    p50_latency_s: float
    p95_latency_s: float
    mean_steps: float
    coverage: float
    task_coverage: float
    cached_input_tokens: int
    source: str


def build(cfg: Config | None = None) -> list[Point]:
    cfg = cfg or get_config()
    points: list[Point] = []
    for model, harness, rel in LADDER:
        path = Path(rel)
        if not path.exists():
            continue
        rows = load_results(path)
        if not rows:
            continue
        for condition in ("A_browser", "B_tools"):
            m = score_condition(rows, condition, cfg)
            if not m.n_runs:
                continue
            points.append(Point(
                model=model, harness=harness, condition=condition,
                n_runs=m.n_runs,
                success_rate=round(m.success_rate, 4),
                usd_per_successful_task=round(m.usd_per_successful_task, 6)
                if m.usd_per_successful_task != float("inf") else float("inf"),
                usd_per_successful_task_uncached=round(
                    m.usd_per_successful_task_uncached, 6)
                if m.usd_per_successful_task_uncached != float("inf") else float("inf"),
                p50_latency_s=round(m.p50_latency_s, 2),
                p95_latency_s=round(m.p95_latency_s, 2),
                mean_steps=round(m.mean_steps, 2),
                coverage=round(m.coverage, 4),
                task_coverage=round(m.task_coverage, 4),
                cached_input_tokens=m.cached_input_tokens,
                source=rel))
    return points


def headline(points: list[Point]) -> dict[str, Any]:
    """Haiku on the instrumented harness against Sonnet on the naive one.

    The commercially interesting comparison: does fixing the harness buy more
    than paying for a bigger model?
    """
    def find(model: str, harness: str, condition: str = "A_browser"):
        for p in points:
            if p.model == model and p.harness == harness and p.condition == condition:
                return p
        return None

    naive = find("Sonnet", "naive (pre-fix)")
    out: dict[str, Any] = {}
    if naive is None:
        return out
    for model in ("Haiku", "Sonnet", "Opus"):
        p = find(model, "instrumented")
        if p is None:
            continue
        entry = {
            "accuracy_delta_pct_points": round(
                (p.success_rate - naive.success_rate) * 100, 1),
            "success_instrumented": p.success_rate,
            "success_sonnet_naive": naive.success_rate,
            "usd_instrumented": p.usd_per_successful_task,
            "usd_sonnet_naive": naive.usd_per_successful_task,
        }
        if p.usd_per_successful_task not in (0, float("inf")):
            entry["cost_ratio_vs_sonnet_naive"] = round(
                naive.usd_per_successful_task / p.usd_per_successful_task, 2)
            entry["cheaper_than_sonnet_naive"] = (
                p.usd_per_successful_task < naive.usd_per_successful_task)
        entry["more_accurate_than_sonnet_naive"] = (
            p.success_rate > naive.success_rate)
        out[f"{model}_instrumented_vs_Sonnet_naive"] = entry
    return out


def condition_ordering(points: list[Point]) -> dict[str, Any]:
    """Does the A/B ordering flip by model tier?

    If synthesized tools help a weaker model more than a stronger one, that is
    a real finding about who tools are for.
    """
    out: dict[str, Any] = {}
    for model in sorted({p.model for p in points if p.harness == "instrumented"}):
        a = next((p for p in points if p.model == model
                  and p.harness == "instrumented" and p.condition == "A_browser"), None)
        b = next((p for p in points if p.model == model
                  and p.harness == "instrumented" and p.condition == "B_tools"), None)
        if a is None or b is None:
            continue
        ratio = (a.usd_per_successful_task / b.usd_per_successful_task
                 if b.usd_per_successful_task not in (0, float("inf")) else float("inf"))
        out[model] = {
            "success_A": a.success_rate, "success_B": b.success_rate,
            "usd_A": a.usd_per_successful_task, "usd_B": b.usd_per_successful_task,
            "cost_ratio_A_over_B": round(ratio, 3) if ratio != float("inf") else None,
            "tools_cheaper": (b.usd_per_successful_task < a.usd_per_successful_task),
            "tools_more_accurate": b.success_rate > a.success_rate,
        }
    return out


def markdown(points: list[Point]) -> str:
    lines = ["| Model | Harness | Condition | Success | $/successful task | "
             "uncached | p50 wall | steps |",
             "| --- | --- | --- | --- | --- | --- | --- | --- |"]
    for p in points:
        usd = ("n/a" if p.usd_per_successful_task == float("inf")
               else f"${p.usd_per_successful_task:.4f}")
        unc = ("n/a" if p.usd_per_successful_task_uncached == float("inf")
               else f"${p.usd_per_successful_task_uncached:.4f}")
        cond = "A browser" if p.condition == "A_browser" else "B tools"
        lines.append(f"| {p.model} | {p.harness} | {cond} | "
                     f"{p.success_rate:.0%} | {usd} | {unc} | "
                     f"{p.p50_latency_s:.1f}s | {p.mean_steps:.1f} |")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default="artifacts/ladder/frontier.json")
    args = ap.parse_args()
    cfg = get_config()
    points = build(cfg)
    payload = {
        "points": [asdict(p) for p in points],
        "headline": headline(points),
        "condition_ordering": condition_ordering(points),
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2, default=str))

    from shadow.bench.charts import frontier as frontier_chart
    chart = frontier_chart(payload["points"], cfg.path("charts") / "model_ladder.png")
    if chart:
        print(f"chart -> {chart}")
    print(markdown(points))
    print()
    print(json.dumps(payload["headline"], indent=2, default=str))
    print(f"\nfrontier -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
