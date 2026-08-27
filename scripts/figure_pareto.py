"""Figure 4 — the quality/cost frontier, in two panels that must travel together.

The left panel is the conventional one: all-pass rate against dollars per
task. The right panel is the same models on the same axis, scored on the
tasks that require a database write.

**Publishing the left panel alone would repeat the exact error this project's
own headline finding is about.** A model that refuses every task is cheap and,
on a benchmark where declining is often correct, scores well — so it lands
high and to the right on the left panel while being incapable of the work.
The Firm C blind pass is the demonstration: 64-80% success, 0/3 on the tasks
needing a write. So the two panels are emitted as one image and are never
shown apart.

Greyscale throughout: ours filled, base hollow, baselines grey, frontier a
grey line through the baseline points.
"""
from __future__ import annotations

import argparse
import glob
import json
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt          # noqa: E402

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

OURS = "openai/erpbench-ship"
BASE = "openai/qwen3-14b-base"

# Every arm's rows and the serving path each was measured on. Recorded per
# figure rather than inferred, because a Pareto that silently mixes serving
# paths is comparing prices for different products.
SOURCES = {
    BASE: ("artifacts/s2_shards/shard_*_of_6.jsonl", "local vLLM (imputed)"),
    OURS: ("artifacts/t2b_shards/shard_*_of_6.jsonl", "local vLLM (imputed)"),
}
ANCHOR_GLOB = "artifacts/pareto_or/shard_*_of_6.jsonl"
ANCHOR_PATH = "OpenRouter"
API_GLOB = "artifacts/pareto_api/shard_*_of_6.jsonl"
API_PATH = "Anthropic direct"

SHORT = {
    OURS: "ours (T2-corrected)",
    BASE: "Qwen3-14B base",
}


def _short(model: str) -> str:
    if model in SHORT:
        return SHORT[model]
    return model.split("/")[-1].replace("-instruct", "").replace("claude-", "")


def load(pattern: str) -> list[dict]:
    return [json.loads(l) for f in glob.glob(pattern)
            for l in open(f) if l.strip()]


def arms() -> dict[str, dict]:
    """One record per model: rates, cost per task, serving path."""
    rows_by_model: dict[str, list[dict]] = {}
    paths: dict[str, str] = {}
    for model, (pattern, path) in SOURCES.items():
        rows = [r for r in load(pattern)
                if r.get("harness_variant") == "corrected"
                and r.get("status") != "error"]
        rows_by_model[model] = rows
        paths[model] = path
    for pattern, path in ((ANCHOR_GLOB, ANCHOR_PATH), (API_GLOB, API_PATH)):
        for r in load(pattern):
            if r.get("status") == "error":
                continue
            rows_by_model.setdefault(r["model"], []).append(r)
            paths[r["model"]] = path

    # Which instances require a write. Taken across every arm so the
    # denominator is a property of the task set, not of who happened to
    # attempt one.
    need = set()
    for rows in rows_by_model.values():
        for r in rows:
            env = r["verdict"].get("envelope", {})
            if env.get("missing_required") or env.get("matched_allowed"):
                need.add((r["template_id"], r["firm_id"], r.get("params_seed")))

    out = {}
    for model, rows in rows_by_model.items():
        if not rows:
            continue
        n = len(rows)
        allpass = sum(1 for r in rows if r["verdict"].get("success"))
        w = [r for r in rows
             if (r["template_id"], r["firm_id"], r.get("params_seed")) in need]
        wpass = sum(1 for r in w if r["verdict"].get("success"))
        usd = sum(r["usage"]["usd"] for r in rows)
        toks = sum(r["usage"]["input_tokens"] + r["usage"]["output_tokens"]
                   for r in rows)
        passed = sum(sum(1 for a in r["verdict"].get("assertions", [])
                         if a.get("passed"))
                     for r in rows)
        out[model] = {
            "n": n, "all_pass": allpass / n,
            "must_write": wpass / len(w) if w else 0.0,
            "must_write_n": len(w),
            "usd_per_task": usd / n,
            "assertions_per_100k": (passed / toks * 100_000) if toks else 0.0,
            "serving": paths.get(model, "?"),
        }
    return out


def _frontier(ax, pts):
    """Upper-left staircase through the baseline points only.

    Ours and base are not in it: the frontier is what the field already
    offers, and the whole question is which side of it we land on.
    """
    pts = sorted(pts, key=lambda p: p[0])
    best, xs, ys = -1.0, [], []
    for x, y in reversed(pts):            # cheapest last
        if y > best:
            best = y
            xs.append(x)
            ys.append(y)
    if len(xs) >= 2:
        ax.step(xs, ys, where="post", color="0.6", lw=1.0, zorder=1,
                label="frontier (baselines)")


def panel(ax, data, key, title, ylabel):
    baselines = [(v["usd_per_task"], v[key]) for m, v in data.items()
                 if m not in (OURS, BASE)]
    _frontier(ax, baselines)
    for model, v in data.items():
        x, y = v["usd_per_task"], v[key]
        if model == OURS:
            ax.scatter([x], [y], s=110, c="black", marker="o", zorder=5)
            ax.annotate(_short(model), (x, y), textcoords="offset points",
                        xytext=(8, 6), fontsize=8, weight="bold")
        elif model == BASE:
            ax.scatter([x], [y], s=110, facecolors="none", edgecolors="black",
                       marker="o", lw=1.4, zorder=5)
            ax.annotate(_short(model), (x, y), textcoords="offset points",
                        xytext=(8, -12), fontsize=8)
        else:
            ax.scatter([x], [y], s=42, c="0.55", marker="s", zorder=3)
            ax.annotate(_short(model), (x, y), textcoords="offset points",
                        xytext=(5, 4), fontsize=6.5, color="0.35")
    if OURS in data and BASE in data:
        a, b = data[BASE], data[OURS]
        ax.annotate("", xy=(b["usd_per_task"], b[key]),
                    xytext=(a["usd_per_task"], a[key]),
                    arrowprops=dict(arrowstyle="->", color="black", lw=1.1,
                                    shrinkA=9, shrinkB=9))
    ax.set_xscale("log")
    ax.invert_xaxis()                     # cost decreasing rightward
    ax.set_xlabel("USD per task  (log, cheaper to the right)")
    ax.set_ylabel(ylabel)
    ax.set_title(title, fontsize=10, loc="left")
    ax.grid(alpha=0.25, lw=0.5)
    ax.set_ylim(bottom=0)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path,
                    default=REPO / "artifacts" / "charts" / "fig4_pareto.png")
    args = ap.parse_args()

    data = arms()
    if OURS not in data:
        print("no rows for the shipped checkpoint", file=sys.stderr)
        return 1

    fig, axes = plt.subplots(1, 2, figsize=(11.5, 4.6))
    panel(axes[0], data, "all_pass",
          "a. All-pass rate — the conventional view",
          "all assertions pass, no unexpected mutation")
    panel(axes[1], data, "must_write",
          "b. Tasks that require a database write",
          "completed correctly")
    axes[0].legend(fontsize=7, loc="lower left", framealpha=0.9)
    fig.suptitle("Quality against cost. Panel (a) alone is the error this "
                 "project's own finding warns about:\na model that refuses "
                 "everything is cheap and scores well where declining is "
                 "correct.", fontsize=9, y=1.02, ha="center")
    fig.tight_layout()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(args.out, dpi=200, bbox_inches="tight")
    print(f"  wrote {args.out}")

    for m, v in sorted(data.items(), key=lambda kv: -kv[1]["usd_per_task"]):
        print(f"  {_short(m):26} n={v['n']:4}  ${v['usd_per_task']:.5f}/task  "
              f"all-pass {100*v['all_pass']:5.1f}%  "
              f"must-write {100*v['must_write']:5.1f}% (n={v['must_write_n']})  "
              f"{v['serving']}")
    (args.out.parent / "fig4_pareto_data.json").write_text(
        json.dumps(data, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
