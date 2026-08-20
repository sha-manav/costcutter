"""Aggregate everything into metrics.json, charts, and a printable summary."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from shadow.capture.schema import read_catalog, read_episodes, read_records
from shadow.config import Config, get_config
from shadow.distill.filter import filter_records
from shadow.distill.induce import is_noise
from shadow.bench.charts import render_all
from shadow.bench.metrics import compare, endpoint_recall, load_results

from oracle.api_surface import classify_endpoint
from oracle.client import OracleClient
from oracle.typing_truth import score_param_typing


def observed_endpoints(cfg: Config) -> list[tuple[str, str]]:
    """(method, path) for every meaningful call the demonstrations made."""
    records = read_records(cfg.path("capture"))
    kept, _stats = filter_records(records)
    return [(r.method, r.path) for r in kept if not is_noise(r)]


def synthesized_endpoints(catalog) -> list[tuple[str, str]]:
    out = []
    for spec in catalog.tools:
        for step in spec.steps:
            # Placeholders are not part of the documented endpoint identity.
            path = step.path_template
            for pname in step.path_bindings:
                path = path.replace("{" + pname + "}", "x")
            out.append((step.method, path))
    return out


def synthesis_ground_truth(cfg: Config) -> dict[str, Any]:
    catalog = read_catalog(cfg.path("tools"))
    score = endpoint_recall(observed_endpoints(cfg), synthesized_endpoints(catalog),
                            classify_endpoint)
    verify_path = cfg.path("artifacts") / "verify_report.json"
    if verify_path.exists():
        reports = json.loads(verify_path.read_text())
        score.from_response_bindings = sum(t.n_from_response_bindings
                                           for t in catalog.tools)
        score.from_response_failed_at_replay = sum(
            1 for r in reports if r.get("binding_failure"))
    out = score.to_dict()
    try:
        oc = OracleClient(cfg)
        oc.login()
        out["param_typing"] = score_param_typing(oc, catalog)
        oc.close()
    except Exception as exc:
        out["param_typing"] = {"error": f"{type(exc).__name__}: {exc}"}
    out["tools"] = [{
        "name": t.name, "support": t.support, "mutation_class": t.mutation_class,
        "verified": t.verified, "steps": len(t.steps),
        "params": list(t.params_schema.get("properties", {})),
        "note": t.verify_note,
    } for t in catalog.tools]
    return out


def build_report(cfg: Config | None = None) -> dict[str, Any]:
    cfg = cfg or get_config()
    rows = load_results(cfg.path("results"))
    comparison = compare(rows, cfg)
    episodes = read_episodes(cfg.path("episodes"))
    catalog = read_catalog(cfg.path("tools"))

    report = {
        "conditions": {"A_browser": comparison.a.to_dict(),
                       "B_tools": comparison.b.to_dict()},
        "headline": {
            "coverage_on_eval": round(comparison.b.coverage, 4),
            "cost_ratio_per_successful_task": round(comparison.cost_ratio, 3),
            "p50_latency_ratio": round(comparison.p50_ratio, 3),
            "p95_latency_ratio": round(comparison.p95_ratio, 3),
            "success_a": round(comparison.a.success_rate, 4),
            "success_b": round(comparison.b.success_rate, 4),
            "policy_simulated": comparison.a.simulated_policy or comparison.b.simulated_policy,
        },
        "pipeline": {
            "episodes": len(episodes),
            "tools": len(catalog.tools),
            "verified_tools": sum(1 for t in catalog.tools if t.verified),
            "tools_with_support_3_plus": sum(1 for t in catalog.tools if t.support >= 3),
        },
        "synthesis_ground_truth": synthesis_ground_truth(cfg),
    }
    sweep = cfg.path("artifacts") / "coverage_sweep.json"
    if sweep.exists():
        report["coverage_sweep"] = json.loads(sweep.read_text())
    attain = cfg.path("artifacts") / "attainable_coverage.json"
    if attain.exists():
        report["attainable_coverage"] = json.loads(attain.read_text())
    return report


def main() -> int:
    cfg = get_config()
    report = build_report(cfg)
    out = cfg.path("artifacts") / "metrics.json"
    out.write_text(json.dumps(report, indent=2, default=str))

    rows = load_results(cfg.path("results"))
    charts = render_all(rows, cfg) if rows else []

    comparison = compare(rows, cfg)
    print(comparison.render())
    gt = report["synthesis_ground_truth"]
    print(f"\nendpoint recall (documented endpoints the demos touched): "
          f"{gt['endpoint_recall']:.0%} unweighted, "
          f"{gt['weighted_endpoint_recall']:.0%} weighted by call volume")
    typing = gt.get("param_typing", {})
    if "accuracy" in typing:
        print(f"parameter typing accuracy: {typing['accuracy']:.0%} "
              f"({typing['correct']}/{typing['scored']} scored, "
              f"{typing['unscorable']} unscorable)")
    print(f"provenance false-positive rate at replay: "
          f"{gt['false_provenance_rate']:.0%}")
    for path in charts:
        print(f"chart: {path}")
    print(f"metrics: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
