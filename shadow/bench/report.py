"""Aggregate everything into metrics.json, charts, and a printable summary."""
from __future__ import annotations

import json
import statistics
from pathlib import Path
from typing import Any

from shadow.capture.schema import read_catalog, read_episodes, read_records
from shadow.config import Config, get_config
from shadow.distill.filter import filter_records
from shadow.distill.endpoints import chrome_endpoints, is_noise
from shadow.distill.induce import trim_episode
from shadow.distill.provenance import ProvenanceEngine
from shadow.bench.charts import render_all
from shadow.bench.metrics import (
    Comparison, _direction, compare, endpoint_recall, load_results,
    score_condition,
)

from oracle.api_surface import classify_endpoint
from oracle.client import OracleClient
from oracle.typing_truth import score_param_typing


def observed_endpoints(cfg: Config) -> list[tuple[str, str]]:
    """(method, path) for every API call the demonstrations made."""
    records = read_records(cfg.path("capture"))
    kept, _stats = filter_records(records)
    return [(r.method, r.path) for r in kept if not is_noise(r)]


def load_bearing_endpoints(cfg: Config) -> list[tuple[str, str]]:
    """(method, path) for the calls that actually carried the tasks.

    Endpoint recall over *every* observed call is misleading: most of them
    are page chrome that a tool is right to ignore. This is the denominator
    that matters — the calls the trim kept.
    """
    episodes = read_episodes(cfg.path("episodes"))
    if not episodes:
        return []
    engine = ProvenanceEngine(cfg)
    chrome = chrome_endpoints(episodes, cfg.induce.chrome_df)
    out: list[tuple[str, str]] = []
    for ep in episodes:
        trimmed = trim_episode(ep, engine.infer(ep, chrome), chrome)
        if trimmed is not None:
            out.extend((r.method, r.path) for r in trimmed.records)
    return out


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
    synthesized = synthesized_endpoints(catalog)
    score = endpoint_recall(load_bearing_endpoints(cfg), synthesized,
                            classify_endpoint)
    all_calls = endpoint_recall(observed_endpoints(cfg), synthesized,
                                classify_endpoint)
    verify_path = cfg.path("artifacts") / "verify_report.json"
    if verify_path.exists():
        reports = json.loads(verify_path.read_text())
        score.from_response_bindings = sum(t.n_from_response_bindings
                                           for t in catalog.tools)
        score.from_response_failed_at_replay = sum(
            1 for r in reports if r.get("binding_failure"))
    out = score.to_dict()
    out["recall_over_all_observed_calls"] = {
        "endpoint_recall": round(all_calls.endpoint_recall, 4),
        "weighted_endpoint_recall": round(all_calls.weighted_endpoint_recall, 4),
        "observed_endpoints": all_calls.observed_endpoints,
        "missed_endpoints": all_calls.missed_endpoints,
    }
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


def router_behaviour(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """What the router picked on each held-out template, and what happened.

    This is where the coverage taxonomy comes from: a tool that was never
    selected, a tool selected with unusable arguments, and a tool that
    worked are three different failures with three different fixes.
    """
    out: dict[str, Any] = {}
    for row in rows:
        if row["condition"] != "B_tools":
            continue
        entry = out.setdefault(row["template_id"], {
            "runs": 0, "tool_selected": {}, "outcomes": {}, "first_error": None})
        entry["runs"] += 1
        for step in row.get("steps", []):
            action = step.get("action", {})
            if action.get("action") != "tool":
                continue
            name = action.get("name", "?")
            entry["tool_selected"][name] = entry["tool_selected"].get(name, 0) + 1
            served = step.get("served_by", "")
            entry["outcomes"][served] = entry["outcomes"].get(served, 0) + 1
            if served == "tool_failed" and entry["first_error"] is None:
                entry["first_error"] = step.get("detail", "")[:220]
            break
        if not any(s.get("action", {}).get("action") == "tool"
                   for s in row.get("steps", [])):
            entry["outcomes"]["no_tool_selected"] = (
                entry["outcomes"].get("no_tool_selected", 0) + 1)
    return out


def cross_model(cfg: Config) -> dict[str, Any]:
    """The same A/B comparison repeated on another model.

    The absolute cost of a run says as much about the price list as about
    the tools. The ratio between conditions is the part that should survive
    a change of model, so it is measured on a second one rather than argued.
    """
    out: dict[str, Any] = {}
    for path in sorted(cfg.path("artifacts").glob("results_model_*.jsonl")):
        rows = load_results(path)
        if not rows:
            continue
        model = path.stem[len("results_model_"):]
        a = score_condition(rows, "A_browser", cfg)
        b = score_condition(rows, "B_tools", cfg)
        pair = Comparison(a=a, b=b)
        out[model] = {
            "source": path.name,
            "simulated_policy": a.simulated_policy or b.simulated_policy,
            "A_browser": a.to_dict(), "B_tools": b.to_dict(),
            "cost_ratio_per_successful_task": round(pair.cost_ratio, 3),
            "cost_ratio_per_successful_task_uncached": round(
                pair.cost_ratio_uncached, 3),
            "p95_latency_ratio": round(pair.p95_ratio, 3),
        }
    return out


# Composite, Frappe-aware actions the scripted recipe could use and the
# model's documented action space cannot express.
COMPOSITE_ACTIONS = {"grid", "field", "link", "save", "wait"}


def baseline_action_space(cfg: Config) -> dict[str, Any]:
    """The scripted baseline against the model, per held-out template.

    The first revision asserted that the scripted UI recipe was a *stronger*
    baseline than an LLM driving the same pages. That was an argument. With
    both arms on disk it is a measurement, and the interesting part is where
    the gap is: not spread evenly, but concentrated on the templates whose
    recipe used a composite action the model's action space cannot express.
    """
    archive = cfg.path("artifacts") / "results_offline.jsonl"
    if not archive.exists():
        return {}
    scripted = [r for r in load_results(archive) if r["condition"] == "A_browser"]
    model_rows = [r for r in load_results(cfg.path("results"))
                  if r["condition"] == "A_browser"]
    if not scripted or not model_rows:
        return {}

    def agg(rows: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "runs": len(rows),
            "success_rate": round(sum(bool(r["success"]) for r in rows) / len(rows), 3),
            "mean_steps": round(statistics.fmean(float(r["n_steps"]) for r in rows), 1),
            "mean_wall_s": round(statistics.fmean(float(r["wall_s"]) for r in rows), 1),
        }

    out: dict[str, Any] = {}
    for template_id in sorted({r["template_id"] for r in model_rows}):
        srows = [r for r in scripted if r["template_id"] == template_id]
        mrows = [r for r in model_rows if r["template_id"] == template_id]
        if not srows or not mrows:
            continue
        used = {str(step.get("action", {}).get("action", "")) for r in srows
                for step in r.get("steps", [])}
        composite = sorted(used & COMPOSITE_ACTIONS - {"wait"})
        out[template_id] = {
            "scripted": agg(srows),
            "model": agg(mrows),
            "composite_actions_the_recipe_used": composite,
            # The model has navigate/click/type/scroll/read only, so a recipe
            # that needed `grid` was doing something the model cannot say.
            "recipe_needed_grid": "grid" in used,
        }
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
            "task_coverage_on_eval": round(comparison.b.task_coverage, 4),
            "cost_ratio_per_successful_task": round(comparison.cost_ratio, 3),
            "cost_ratio_per_successful_task_uncached": round(
                comparison.cost_ratio_uncached, 3),
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
        "router_behaviour": router_behaviour(rows),
    }
    readonly_path = cfg.path("artifacts") / "results_readonly.jsonl"
    if readonly_path.exists():
        readonly = score_condition(load_results(readonly_path), "B_tools", cfg)
        report["read_only_ablation"] = readonly.to_dict()

    k_sweep = cfg.path("artifacts") / "k_sweep.json"
    if k_sweep.exists():
        report["k_sweep"] = json.loads(k_sweep.read_text())

    sweep = cfg.path("artifacts") / "coverage_sweep.json"
    if sweep.exists():
        report["coverage_sweep"] = json.loads(sweep.read_text())
    attain = cfg.path("artifacts") / "attainable_coverage.json"
    if attain.exists():
        report["attainable_coverage"] = json.loads(attain.read_text())
    collateral = cfg.path("artifacts") / "collateral_writes.json"
    if collateral.exists():
        report["collateral_writes"] = json.loads(collateral.read_text())
    models = cross_model(cfg)
    if models:
        report["cross_model"] = models
    baseline = baseline_action_space(cfg)
    if baseline:
        report["baseline_action_space"] = baseline
    return report


def markdown_tables(report: dict[str, Any]) -> str:
    """Numeric tables for FINDINGS.md, so no number is hand-copied."""
    a = report["conditions"]["A_browser"]
    b = report["conditions"]["B_tools"]
    head = report["headline"]
    gt = report["synthesis_ground_truth"]
    typing = gt.get("param_typing", {})

    def row(label: str, key: str, fmt: str = "{:.4f}") -> str:
        return (f"| {label} | {fmt.format(a[key])} | {fmt.format(b[key])} |")

    lines = [
        "### Conditions on the held-out (EVAL) split", "",
        "| metric | A: browser only | B: tools + fallback |",
        "| --- | --- | --- |",
        f"| runs | {a['n_runs']} | {b['n_runs']} |",
        f"| tasks | {a['n_tasks']} | {b['n_tasks']} |",
        row("success rate", "success_rate", "{:.0%}"),
        row("pass^k across trials", "pass_hat_k", "{:.0%}"),
        row("USD per attempted task", "usd_per_task", "${:.5f}"),
        row("USD per successful task", "usd_per_successful_task", "${:.5f}"),
        row("USD per successful task, cache at full rate",
            "usd_per_successful_task_uncached", "${:.5f}"),
        row("p50 latency (s)", "p50_latency_s", "{:.1f}"),
        row("p95 latency (s)", "p95_latency_s", "{:.1f}"),
        row("mean steps", "mean_steps", "{:.1f}"),
        row("actions served by synthesized tools", "coverage", "{:.0%}"),
        row("tasks finished on tools alone", "task_coverage", "{:.0%}"),
        f"| tool calls that failed | {a['failed_tool_actions']} | "
        f"{b['failed_tool_actions']} |",
        f"| input tokens (uncached) | {a['input_tokens']:,} | "
        f"{b['input_tokens']:,} |",
        f"| input tokens read from cache | {a['cached_input_tokens']:,} | "
        f"{b['cached_input_tokens']:,} |",
        f"| tokens written to cache | {a['cache_write_tokens']:,} | "
        f"{b['cache_write_tokens']:,} |",
        f"| output tokens | {a['output_tokens']:,} | {b['output_tokens']:,} |",
        "",
        "Cost is reported twice. Prompt caching is not neutral between these "
        "two conditions: the tool agent's prefix is fixed and cacheable, while "
        "the browser agent re-sends a page snapshot that changes every step "
        "and cannot be cached. The discount therefore flows to condition B by "
        "construction, so the second row reprices cache reads at the full "
        "input rate to show the comparison without it. Cache writes are billed "
        "at 1.25x the input rate in both rows.",
        "",
        f"Cost per successful task: **{_direction(head['cost_ratio_per_successful_task'], 'cheaper', 'more expensive')}** "
        f"with caching priced as billed, "
        f"**{_direction(head['cost_ratio_per_successful_task_uncached'], 'cheaper', 'more expensive')}** "
        f"with cache reads repriced at the full input rate. "
        f"p95 latency: **{_direction(head['p95_latency_ratio'], 'faster', 'slower')}**. "
        f"p50 latency: **{_direction(head['p50_latency_ratio'], 'faster', 'slower')}**.",
        "",
        "### Synthesis ground truth", "",
        "| measure | value |",
        "| --- | --- |",
        f"| documented endpoints in load-bearing calls | {len(gt['observed_endpoints'])} |",
        f"| endpoint recall over load-bearing calls (unweighted) | {gt['endpoint_recall']:.0%} |",
        f"| endpoint recall over load-bearing calls (weighted) | {gt['weighted_endpoint_recall']:.0%} |",
        f"| endpoint recall over every observed API call (unweighted) | "
        f"{gt['recall_over_all_observed_calls']['endpoint_recall']:.0%} |",
        f"| parameter typing accuracy | "
        f"{typing.get('accuracy', 0):.0%} ({typing.get('correct', 0)}/{typing.get('scored', 0)} scored) |",
        f"| from_response bindings induced | {gt['from_response_bindings']} |",
        f"| tools failing on an unresolved binding at replay | "
        f"{gt['from_response_failed_at_replay']} |",
        f"| provenance false-positive rate at replay | {gt['false_provenance_rate']:.0%} |",
        "",
        "### Induced tools", "",
        "| tool | support | class | verified | steps | parameters |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for tool in gt["tools"]:
        params = ", ".join(f"`{p}`" for p in tool["params"]) or "—"
        lines.append(f"| `{tool['name']}` | {tool['support']} | "
                     f"{tool['mutation_class']} | "
                     f"{'yes' if tool['verified'] else 'no'} | {tool['steps']} | "
                     f"{params} |")

    per_template = b.get("per_template", {})
    if per_template:
        lines += ["", "### Per held-out template (condition B)", "",
                  "| template | success | action coverage | task coverage | "
                  "USD/task | steps |",
                  "| --- | --- | --- | --- | --- | --- |"]
        for name, row_data in per_template.items():
            lines.append(
                f"| {name} | {row_data['success_rate']:.0%} | "
                f"{row_data['coverage']:.0%} | "
                f"{row_data.get('task_coverage', 0):.0%} | "
                f"${row_data['usd_per_task']:.5f} | "
                f"{row_data['mean_steps']:.1f} |")

    k_points = report.get("k_sweep")
    if k_points:
        lines += ["", "### The catalog tax: how many tools to put in the prompt", "",
                  "`k=0` is the browser baseline reached through the tool agent; "
                  "`k=8` is the whole-catalog behaviour. `no catalog` counts runs "
                  "where no tool cleared the score floor and none was sent.", "",
                  "| k | success | USD/successful task | p95 (s) | steps | "
                  "action coverage | task coverage | mean prompt chars | no catalog |",
                  "| --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
        for point in k_points:
            lines.append(
                f"| {point['k']} | {point['success_rate']:.0%} | "
                f"${point['usd_per_successful_task']:.5f} | "
                f"{point['p95_latency_s']:.1f} | {point['mean_steps']:.1f} | "
                f"{point['coverage']:.0%} | {point['task_coverage']:.0%} | "
                f"{point['mean_prompt_chars']:.0f} | "
                f"{point['runs_with_no_catalog']}/{point['runs']} |")

    readonly = report.get("read_only_ablation")
    if readonly:
        lines += ["", "### Ablation: condition B with writes gated off", "",
                  "The recommended configuration. Synthesized write tools are "
                  "not exposed, so every mutation goes through the browser "
                  "fallback and no tool can change the database.", "",
                  "| metric | B: writes enabled | B: read-only |",
                  "| --- | --- | --- |",
                  f"| success rate | {b['success_rate']:.0%} | "
                  f"{readonly['success_rate']:.0%} |",
                  f"| USD per successful task | ${b['usd_per_successful_task']:.5f} | "
                  f"${readonly['usd_per_successful_task']:.5f} |",
                  f"| p95 latency (s) | {b['p95_latency_s']:.1f} | "
                  f"{readonly['p95_latency_s']:.1f} |",
                  f"| mean steps | {b['mean_steps']:.1f} | "
                  f"{readonly['mean_steps']:.1f} |",
                  f"| tasks finished on tools alone | {b['task_coverage']:.0%} | "
                  f"{readonly['task_coverage']:.0%} |",
                  f"| tool calls that failed | {b['failed_tool_actions']} | "
                  f"{readonly['failed_tool_actions']} |"]

    router = report.get("router_behaviour") or {}
    if router:
        lines += ["", "### What the router did on each held-out template", "",
                  "| template | tool selected | outcome | first error |",
                  "| --- | --- | --- | --- |"]
        for name, entry in sorted(router.items()):
            picked = ", ".join(f"`{k}`" for k in entry["tool_selected"]) or "—"
            outcomes = ", ".join(f"{k}×{v}" for k, v in entry["outcomes"].items())
            err = (entry["first_error"] or "").replace("|", "\\|")[:120] or "—"
            lines.append(f"| {name} | {picked} | {outcomes} | {err} |")

    attain = report.get("attainable_coverage")
    if attain:
        lines += ["", "### Attainable coverage (router-independent ceiling)", "",
                  f"{attain['attainable']}/{attain['tasks']} held-out tasks "
                  f"({attain['rate']:.0%}) can be completed by some verified "
                  "synthesized tool when the oracle supplies the arguments.", "",
                  "| template | attainable |", "| --- | --- |"]
        for name, rate in attain.get("by_template", {}).items():
            lines.append(f"| {name} | {rate:.0%} |")

    baseline = report.get("baseline_action_space") or {}
    if baseline:
        lines += ["", "### The scripted baseline was a stronger baseline", "",
                  "The first revision of this document ran condition A on a "
                  "scripted UI recipe and argued that this made the baseline "
                  "*harder* to beat than a model driving the same pages. With "
                  "both arms measured, that holds -- and the gap is not spread "
                  "evenly. It sits almost entirely on the templates whose "
                  "recipe used `grid`, a composite Frappe-aware action for "
                  "editing a child-table row that the model's action space "
                  "(navigate, click, type, scroll, read) cannot express at "
                  "all.", "",
                  "| template | recipe success | model success | recipe steps | "
                  "model steps | recipe wall | model wall | needed `grid` |",
                  "| --- | --- | --- | --- | --- | --- | --- | --- |"]
        for name, entry in baseline.items():
            sc, mo = entry["scripted"], entry["model"]
            lines.append(
                f"| {name} | {sc['success_rate']:.0%} | {mo['success_rate']:.0%} | "
                f"{sc['mean_steps']:.1f} | {mo['mean_steps']:.1f} | "
                f"{sc['mean_wall_s']:.1f}s | {mo['mean_wall_s']:.1f}s | "
                f"{'yes' if entry['recipe_needed_grid'] else 'no'} |")
        lines += ["", "On the flat-form templates the model needs a step or two "
                  "more than the recipe and finishes. On the grid templates it "
                  "does not finish at all: the row editor never becomes "
                  "clickable through a generic click, so the run spends most of "
                  "its step budget on 15-second timeouts and stops at the "
                  "ceiling. This is a limit of the action space, not of the "
                  "model's plan -- the traces show it navigating to the right "
                  "page and filling the flat fields correctly first.", ""]

    models = report.get("cross_model") or {}
    if models:
        lines += ["", "### The same comparison on a second model", "",
                  "Absolute cost tracks the price list. The ratio between the "
                  "two conditions is the claim, so it is repeated on a cheaper "
                  "model rather than assumed to carry.", "",
                  "| model | A success | B success | A $/success | B $/success | "
                  "ratio (as billed) | ratio (cache at full rate) | B task coverage |",
                  "| --- | --- | --- | --- | --- | --- | --- | --- |"]
        for name, entry in models.items():
            ma, mb = entry["A_browser"], entry["B_tools"]
            tag = " *(simulated)*" if entry["simulated_policy"] else ""
            lines.append(
                f"| `{name}`{tag} | {ma['success_rate']:.0%} | "
                f"{mb['success_rate']:.0%} | "
                f"${ma['usd_per_successful_task']:.5f} | "
                f"${mb['usd_per_successful_task']:.5f} | "
                f"{_direction(entry['cost_ratio_per_successful_task'], 'cheaper', 'more expensive')} | "
                f"{_direction(entry['cost_ratio_per_successful_task_uncached'], 'cheaper', 'more expensive')} | "
                f"{mb['task_coverage']:.0%} |")

    collateral = report.get("collateral_writes")
    if collateral:
        lines += ["", "### Collateral writes with the write tools exposed", "",
                  "The oracle checks a post-condition, so it is blind to "
                  "anything else a run touched. This diffs record counts "
                  "across every watched type around each run, so a tool that "
                  "created something nobody asked for is measured rather than "
                  "assumed absent.", "",
                  f"Model `{collateral.get('model', '?')}` "
                  f"({collateral.get('provider', '?')}): "
                  f"**{collateral['runs_with_collateral']}/{collateral['runs']}** "
                  f"runs wrote a record outside what the task entitled them "
                  f"to; **{collateral['passes_with_collateral']}** of those "
                  f"{'was' if collateral['passes_with_collateral'] == 1 else 'were'} "
                  f"still scored as a pass.", "",
                  "| task | scored | tools called | unintended records |",
                  "| --- | --- | --- | --- |"]
        for entry in collateral.get("results", []):
            tools = ", ".join(f"`{t}`" for t in entry["tools_called"]) or "—"
            extra = ", ".join(f"{k} +{v}" for k, v in entry["collateral"].items())
            lines.append(f"| {entry['task_id']} | "
                         f"{'pass' if entry['success'] else 'fail'} | {tools} | "
                         f"{extra or 'none'} |")

    sweep = report.get("coverage_sweep")
    if sweep:
        lines += ["", "### Coverage vs observation volume", "",
                  "The sweep verifies reads only, so its verified-tool count "
                  "excludes write tools that the main run verified against a "
                  "reset database.", "",
                  "| sessions | episodes | tools | verified (reads) | achieved coverage | attainable |",
                  "| --- | --- | --- | --- | --- | --- |"]
        for point in sweep:
            lines.append(
                f"| {point['sessions']} | {point.get('episodes', '—')} | "
                f"{point.get('tools', '—')} | {point.get('verified_tools', '—')} | "
                f"{point.get('coverage', 0):.0%} | "
                f"{point.get('attainable_coverage', 0):.0%} |")
    return "\n".join(lines)


def main() -> int:
    cfg = get_config()
    report = build_report(cfg)
    out = cfg.path("artifacts") / "metrics.json"
    out.write_text(json.dumps(report, indent=2, default=str))
    tables = cfg.path("artifacts") / "findings_tables.md"
    tables.write_text(markdown_tables(report))

    rows = load_results(cfg.path("results"))
    charts = render_all(rows, cfg) if rows else []

    comparison = compare(rows, cfg)
    print(comparison.render())
    gt = report["synthesis_ground_truth"]
    print(f"\nendpoint recall over load-bearing calls: "
          f"{gt['endpoint_recall']:.0%} unweighted, "
          f"{gt['weighted_endpoint_recall']:.0%} weighted by call volume")
    every = gt["recall_over_all_observed_calls"]
    print(f"endpoint recall over every observed API call: "
          f"{every['endpoint_recall']:.0%} unweighted, "
          f"{every['weighted_endpoint_recall']:.0%} weighted")
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
    print(f"tables:  {tables}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
