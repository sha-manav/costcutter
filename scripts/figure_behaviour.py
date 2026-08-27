"""Figure 5 — behaviour change, base against the shipped checkpoint.

The six instrumented metrics, recorded per rollout rather than reconstructed
afterwards. This is the instrumentation that caught the masking effect, so
the figure doubles as the argument for recording it.

Diverging bars: right of the axis is the shipped checkpoint doing more of
something, left is less. Two of these movements are improvements, two are
neutral, and two are the collapse -- shown together rather than selected.
"""
from __future__ import annotations

import argparse, glob, json, sys
from collections import Counter
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt          # noqa: E402

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

BASE = "artifacts/s2_shards/shard_*_of_6.jsonl"
SHIP = "artifacts/t2b_shards/shard_*_of_6.jsonl"

METRICS = [
    ("first action is a refusal", "refuse", "%"),
    ("consulted policy before writing", "policy", "%"),
    ("recovered from a failed action", "recovery", "%"),
    ("repeated an ineffective call", "repeat", "%"),
    ("committed a forbidden write", "forbidden", "%"),
    ("actions per task", "steps", ""),
]


def _name(a):
    v = a.get("action")
    return (v.get("action") if isinstance(v, dict) else v) or "?"


def stats(pattern):
    rows = [json.loads(l) for f in glob.glob(pattern) for l in open(f)
            if l.strip()]
    rows = [r for r in rows if r.get("status") != "error"
            and r.get("harness_variant") == "corrected"]
    n = len(rows)
    b = lambda k: sum(float(r.get("behaviour", {}).get(k, 0) or 0) for r in rows) / n  # noqa: E731
    first = Counter(_name(r["actions"][0]) if r["actions"] else "none" for r in rows)
    return {
        "refuse": 100 * (first["escalate"] + first["abstain"]) / n,
        "policy": 100 * b("policy_consulted_before_first_mutation"),
        "recovery": 100 * b("recovery_events"),
        "repeat": 100 * b("repeated_ineffective_calls"),
        "forbidden": 100 * b("forbidden_writes_committed"),
        "steps": sum(len(r["actions"]) for r in rows) / n,
        "n": n,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path,
                    default=REPO / "artifacts" / "charts" / "fig5_behaviour.png")
    args = ap.parse_args()
    a, b = stats(BASE), stats(SHIP)

    fig, ax = plt.subplots(figsize=(8.4, 4.4))
    ys = range(len(METRICS))
    for i, (label, key, unit) in enumerate(METRICS):
        ax.barh(i - 0.19, a[key], height=0.34, facecolor="none",
                edgecolor="black", lw=1.3)
        ax.barh(i + 0.19, b[key], height=0.34, color="black")
        for val, off in ((a[key], -0.19), (b[key], 0.19)):
            ax.annotate(f"{val:.1f}{unit}", (val, i + off), xytext=(4, 0),
                        textcoords="offset points", va="center", fontsize=7)
    ax.set_yticks(list(ys))
    ax.set_yticklabels([m[0] for m in METRICS], fontsize=8.5)
    ax.invert_yaxis()
    ax.set_xlabel("percent of rollouts, except the last row which is a count")
    ax.set_title(f"Hollow = base Qwen3-14B, filled = shipped checkpoint  "
                 f"(n={a['n']} each)", fontsize=9.5, loc="left")
    ax.grid(alpha=0.25, lw=0.5, axis="x")
    ax.annotate("axis truncated: 'actions per task' is a count on the same "
                "axis as percentages,\nplotted for compactness and labelled "
                "rather than rescaled",
                (0.99, 0.02), xycoords="axes fraction", ha="right",
                fontsize=6.5, style="italic", color="0.4")
    fig.suptitle("The six per-rollout metrics. Training removed the forbidden "
                 "writes by removing the writes:\nrefusal up, actions per task "
                 "down, policy reads to zero.", fontsize=9, y=1.02)
    fig.tight_layout()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(args.out, dpi=200, bbox_inches="tight")
    print(f"  wrote {args.out}")
    for label, key, unit in METRICS:
        print(f"  {label:34} base {a[key]:6.2f}   ship {b[key]:6.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
