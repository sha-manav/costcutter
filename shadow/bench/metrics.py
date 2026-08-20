"""Scoring. Cost, latency, coverage, and synthesis ground truth."""
from __future__ import annotations

import json
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from shadow.config import Config, get_config


class MissingCost(RuntimeError):
    """Refuse to score a model with no price in the config table."""


def usd(cfg: Config, usage: dict[str, Any]) -> float:
    model = usage.get("model", "")
    price = cfg.costs.get(model)
    if price is None:
        raise MissingCost(
            f"no cost entry for model {model!r}; add it to config.yaml costs")
    return (usage.get("input_tokens", 0) / 1e6 * price.input
            + usage.get("cached_input_tokens", 0) / 1e6 * price.cached_input
            + usage.get("output_tokens", 0) / 1e6 * price.output)


def _tool_only(row: dict[str, Any]) -> bool:
    """Did this run finish on synthesized tools alone?"""
    served = [step.get("served_by") for step in row.get("steps", [])]
    return "tool" in served and "browser_fallback" not in served and "browser" not in served


def _pct(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, int(round(q * (len(ordered) - 1)))))
    return ordered[idx]


@dataclass
class ConditionMetrics:
    condition: str
    n_runs: int = 0
    n_tasks: int = 0
    success_rate: float = 0.0
    pass_hat_k: float = 0.0
    usd_per_task: float = 0.0
    usd_per_successful_task: float = 0.0
    total_usd: float = 0.0
    p50_latency_s: float = 0.0
    p95_latency_s: float = 0.0
    mean_steps: float = 0.0
    coverage: float = 0.0
    # Fraction of runs finished without ever touching the browser. Action
    # coverage counts a tool call that succeeded but did not advance the
    # task; this does not.
    task_coverage: float = 0.0
    tool_actions: int = 0
    browser_actions: int = 0
    failed_tool_actions: int = 0
    simulated_policy: bool = False
    per_template: dict[str, dict[str, Any]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = dict(self.__dict__)
        for key in ("success_rate", "pass_hat_k", "coverage", "task_coverage"):
            d[key] = round(d[key], 4)
        for key in ("usd_per_task", "usd_per_successful_task", "total_usd"):
            d[key] = round(d[key], 6)
        for key in ("p50_latency_s", "p95_latency_s", "mean_steps"):
            d[key] = round(d[key], 3)
        return d


def load_results(path: str | Path) -> list[dict[str, Any]]:
    p = Path(path)
    if not p.exists():
        return []
    return [json.loads(line) for line in p.read_text().splitlines() if line.strip()]


def score_condition(rows: list[dict[str, Any]], condition: str,
                    cfg: Config | None = None) -> ConditionMetrics:
    cfg = cfg or get_config()
    rows = [r for r in rows if r["condition"] == condition]
    m = ConditionMetrics(condition=condition, n_runs=len(rows))
    if not rows:
        return m

    by_task: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_task[r["task_id"]].append(r)
    m.n_tasks = len(by_task)

    successes = [bool(r["success"]) for r in rows]
    m.success_rate = sum(successes) / len(successes)
    m.pass_hat_k = sum(1 for runs in by_task.values()
                       if all(bool(r["success"]) for r in runs)) / len(by_task)

    costs = [usd(cfg, r["usage"]) for r in rows]
    m.total_usd = sum(costs)
    m.usd_per_task = m.total_usd / len(rows)
    successful_cost = sum(c for c, ok in zip(costs, successes))
    n_success = sum(successes)
    # Reported per SUCCESSFUL task: the cost of failed attempts still counts,
    # otherwise a condition can look cheap by failing fast.
    m.usd_per_successful_task = (m.total_usd / n_success) if n_success else float("inf")

    latencies = [float(r["wall_s"]) for r in rows]
    m.p50_latency_s = _pct(latencies, 0.50)
    m.p95_latency_s = _pct(latencies, 0.95)
    m.mean_steps = statistics.fmean(float(r["n_steps"]) for r in rows)

    m.tool_actions = sum(int(r.get("tool_actions", 0)) for r in rows)
    m.browser_actions = sum(int(r.get("browser_actions", 0)) for r in rows)
    total_actions = m.tool_actions + m.browser_actions
    m.coverage = m.tool_actions / total_actions if total_actions else 0.0
    m.failed_tool_actions = sum(
        1 for r in rows for step in r.get("steps", [])
        if step.get("served_by") == "tool_failed")
    m.task_coverage = sum(1 for r in rows if _tool_only(r)) / len(rows)
    m.simulated_policy = any(r["usage"].get("simulated") for r in rows)

    for template_id in sorted({r["template_id"] for r in rows}):
        trows = [r for r in rows if r["template_id"] == template_id]
        tcosts = [usd(cfg, r["usage"]) for r in trows]
        tsucc = [bool(r["success"]) for r in trows]
        t_tool = sum(int(r.get("tool_actions", 0)) for r in trows)
        t_all = t_tool + sum(int(r.get("browser_actions", 0)) for r in trows)
        m.per_template[template_id] = {
            "runs": len(trows),
            "success_rate": round(sum(tsucc) / len(tsucc), 3),
            "usd_per_task": round(sum(tcosts) / len(tcosts), 6),
            "mean_steps": round(statistics.fmean(float(r["n_steps"]) for r in trows), 2),
            "coverage": round(t_tool / t_all, 3) if t_all else 0.0,
            "task_coverage": round(
                sum(1 for r in trows if _tool_only(r)) / len(trows), 3),
            "p95_latency_s": round(_pct([float(r["wall_s"]) for r in trows], 0.95), 2),
        }
    return m


@dataclass
class Comparison:
    a: ConditionMetrics
    b: ConditionMetrics

    @property
    def cost_ratio(self) -> float:
        if self.b.usd_per_successful_task in (0, float("inf")):
            return float("inf")
        return self.a.usd_per_successful_task / self.b.usd_per_successful_task

    @property
    def p95_ratio(self) -> float:
        return (self.a.p95_latency_s / self.b.p95_latency_s
                if self.b.p95_latency_s else float("inf"))

    @property
    def p50_ratio(self) -> float:
        return (self.a.p50_latency_s / self.b.p50_latency_s
                if self.b.p50_latency_s else float("inf"))

    def render(self) -> str:
        return (
            f"condition A (browser only)   : success {self.a.success_rate:.0%}  "
            f"${self.a.usd_per_successful_task:.5f}/successful task  "
            f"p50 {self.a.p50_latency_s:.1f}s  p95 {self.a.p95_latency_s:.1f}s  "
            f"steps {self.a.mean_steps:.1f}\n"
            f"condition B (tools+fallback) : success {self.b.success_rate:.0%}  "
            f"${self.b.usd_per_successful_task:.5f}/successful task  "
            f"p50 {self.b.p50_latency_s:.1f}s  p95 {self.b.p95_latency_s:.1f}s  "
            f"steps {self.b.mean_steps:.1f}\n"
            f"coverage: {self.b.coverage:.0%} of actions served by synthesized "
            f"tools; {self.b.task_coverage:.0%} of tasks finished on tools alone\n"
            f"cost per successful task: {self.cost_ratio:.1f}x cheaper\n"
            f"p95 latency: {self.p95_ratio:.1f}x faster")


def compare(rows: list[dict[str, Any]], cfg: Config | None = None) -> Comparison:
    cfg = cfg or get_config()
    return Comparison(a=score_condition(rows, "A_browser", cfg),
                      b=score_condition(rows, "B_tools", cfg))


# --------------------------------------------------------------------------
# Synthesis ground truth (oracle side)
# --------------------------------------------------------------------------

@dataclass
class SynthesisScore:
    observed_endpoints: dict[str, int] = field(default_factory=dict)
    synthesized_endpoints: dict[str, int] = field(default_factory=dict)
    endpoint_recall: float = 0.0
    weighted_endpoint_recall: float = 0.0
    missed_endpoints: list[str] = field(default_factory=list)
    param_typing_total: int = 0
    param_typing_correct: int = 0
    param_typing_accuracy: float = 0.0
    from_response_bindings: int = 0
    from_response_failed_at_replay: int = 0

    @property
    def false_provenance_rate(self) -> float:
        if not self.from_response_bindings:
            return 0.0
        return self.from_response_failed_at_replay / self.from_response_bindings

    def to_dict(self) -> dict[str, Any]:
        d = dict(self.__dict__)
        d["false_provenance_rate"] = round(self.false_provenance_rate, 4)
        return d


def endpoint_recall(observed: Iterable[tuple[str, str]],
                    synthesized: Iterable[tuple[str, str]],
                    classify) -> SynthesisScore:
    """Of the documented endpoints the demonstrations touched, how many did
    synthesis turn into a callable tool?"""
    score = SynthesisScore()
    obs_counts: dict[str, int] = defaultdict(int)
    for method, path in observed:
        name, _cat = classify(method, path)
        obs_counts[name] += 1
    syn: set[str] = set()
    for method, path in synthesized:
        name, _cat = classify(method, path)
        syn.add(name)
    score.observed_endpoints = dict(sorted(obs_counts.items()))
    score.synthesized_endpoints = {n: obs_counts.get(n, 0) for n in sorted(syn)}
    covered = [n for n in obs_counts if n in syn]
    score.endpoint_recall = len(covered) / len(obs_counts) if obs_counts else 0.0
    total_calls = sum(obs_counts.values())
    score.weighted_endpoint_recall = (
        sum(obs_counts[n] for n in covered) / total_calls if total_calls else 0.0)
    score.missed_endpoints = sorted(n for n in obs_counts if n not in syn)
    return score
