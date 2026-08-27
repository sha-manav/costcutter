"""Figure 3 — eight sentences of context beat four fine-tuning runs.

The left panel is the result: first-action refusal against the number of
operator corrections in the prompt, with the four trained checkpoints as
horizontal reference lines. Corrections take refusal from 85% to 26%. Every
training run moved the same number the *wrong* way, monotonically, across
roughly a thousand corrective examples.

The right panel is the qualification, and it carries equal weight. must-write
does not improve at any correction level — it sits at 0-3 of 17 throughout.
**Corrections buy engagement, not competence:** the model stops declining and
starts attempting, and the attempts are wrong. Reporting the left panel alone
would be a claim that context solves the problem, which it does not.
"""
from __future__ import annotations

import argparse
import glob
import json
import sys
from collections import Counter
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt          # noqa: E402

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

LEVELS = (0, 1, 3, 8)

# The trained checkpoints, in curriculum order, with their first-action
# refusal rate on the same harness and task set (firms A+B, n=312 each).
CHECKPOINTS = [
    ("base Qwen3-14B", "artifacts/s2_shards/shard_*_of_6.jsonl"),
    ("T1", "artifacts/t1_shards/shard_*_of_6.jsonl"),
    ("T2 corrected", "artifacts/t2b_shards/shard_*_of_6.jsonl"),
    ("T3 corrected", "artifacts/t3b_shards/shard_*_of_6.jsonl"),
    ("single-stage", "artifacts/single_shards/shard_*_of_6.jsonl"),
]


def _name(action: dict) -> str:
    v = action.get("action")
    if isinstance(v, dict):
        v = v.get("action")
    return v if isinstance(v, str) else "?"


def _stats(rows: list[dict]) -> dict:
    rows = [r for r in rows if r.get("status") != "error"]
    if not rows:
        return {}
    n = len(rows)
    first = Counter(_name(r["actions"][0]) if r["actions"] else "none"
                    for r in rows)
    need = set()
    for r in rows:
        env = r["verdict"].get("envelope", {})
        if env.get("missing_required") or env.get("matched_allowed"):
            need.add((r["template_id"], r["firm_id"], r.get("params_seed")))
    w = [r for r in rows
         if (r["template_id"], r["firm_id"], r.get("params_seed")) in need]
    return {
        "n": n,
        "refuse": (first["escalate"] + first["abstain"]) / n,
        "steps": sum(len(r["actions"]) for r in rows) / n,
        "must_write": (sum(1 for r in w if r["verdict"].get("success"))
                       / len(w)) if w else 0.0,
        "must_write_n": len(w),
    }


def load(pattern: str) -> list[dict]:
    return [json.loads(l) for f in glob.glob(pattern)
            for l in open(f) if l.strip()]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path,
                    default=REPO / "artifacts" / "charts" / "fig3_adaptation.png")
    args = ap.parse_args()

    sweep = {}
    for n in LEVELS:
        rows = load(f"artifacts/adaptB_{n}/shard_*_of_6.jsonl")
        rows = [r for r in rows if r.get("harness_variant") == "corrected"]
        s = _stats(rows)
        if s:
            sweep[n] = s
    if not sweep:
        print("no adaptation rows", file=sys.stderr)
        return 1

    trained = {}
    for label, pattern in CHECKPOINTS:
        rows = [r for r in load(pattern)
                if r.get("harness_variant") == "corrected"]
        s = _stats(rows)
        if s:
            trained[label] = s

    xs = sorted(sweep)
    fig, axes = plt.subplots(1, 2, figsize=(11.0, 4.4))

    ax = axes[0]
    # Group checkpoints that land on the same rate, or their labels overprint
    # -- T3 corrected and the single-stage run are both 88.5%.
    grouped: dict[int, list[str]] = {}
    for label, s in trained.items():
        grouped.setdefault(round(100 * s["refuse"]), []).append(label)
    for rate, labels in sorted(grouped.items()):
        ax.axhline(rate, color="0.62", lw=0.9, ls=(0, (4, 3)), zorder=1)
        ax.annotate(f"{' = '.join(labels)}  {rate}%",
                    (0, rate), xytext=(3, 2.5), textcoords="offset points",
                    fontsize=6.5, color="0.3", va="bottom", ha="left")
    ax.plot(xs, [100 * sweep[x]["refuse"] for x in xs], color="black",
            marker="o", ms=7, lw=1.6, zorder=4, label="corrections (Firm B)")
    ax.set_xticks(xs)
    ax.set_xlabel("operator corrections in the prompt")
    ax.set_ylabel("refuses on its first action  (%)")
    ax.set_title("a. Context moves the refusal prior; training did not",
                 fontsize=10, loc="left")
    ax.set_ylim(0, 100)
    ax.grid(alpha=0.25, lw=0.5)
    ax.legend(fontsize=7, loc="center left")

    ax2 = ax.twinx()
    ax2.plot(xs, [sweep[x]["steps"] for x in xs], color="0.45", marker="^",
             ms=5, lw=1.1, ls=":", zorder=3)
    ax2.set_ylabel("mean actions per task  (dotted)", color="0.35", fontsize=9,
                   labelpad=8)
    ax2.tick_params(axis="y", colors="0.35")
    ax2.set_ylim(0, max(4.0, max(sweep[x]["steps"] for x in xs) * 1.3))

    ax = axes[1]
    denom = sweep[xs[0]]["must_write_n"]
    ax.bar([str(x) for x in xs],
           [100 * sweep[x]["must_write"] for x in xs],
           color="0.25", width=0.55)
    ax.set_xlabel("operator corrections in the prompt")
    ax.set_ylabel(f"tasks requiring a write, completed  (%, n={denom})")
    ax.set_title("b. …and buys no competence", fontsize=10, loc="left")
    ax.set_ylim(0, 100)
    ax.grid(alpha=0.25, lw=0.5, axis="y")
    for i, x in enumerate(xs):
        k = round(sweep[x]["must_write"] * sweep[x]["must_write_n"])
        ax.annotate(f"{k}/{sweep[x]['must_write_n']}", (i, 100 * sweep[x]["must_write"]),
                    xytext=(0, 4), textcoords="offset points",
                    ha="center", fontsize=8)

    fig.suptitle("Eight sentences cut first-action refusal from 85% to 26%. "
                 "A thousand corrective training examples moved it the other "
                 "way.\nThe model stops declining and starts attempting — and "
                 "the attempts are still wrong.", fontsize=9, y=1.04)
    fig.tight_layout()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(args.out, dpi=200, bbox_inches="tight")
    print(f"  wrote {args.out}")
    for x in xs:
        s = sweep[x]
        print(f"  corrections={x}: refuse {100*s['refuse']:5.1f}%  "
              f"steps {s['steps']:.2f}  must-write "
              f"{round(s['must_write']*s['must_write_n']):.0f}/{s['must_write_n']}")
    for label, s in trained.items():
        print(f"  {label:16} refuse {100*s['refuse']:5.1f}%  steps {s['steps']:.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
