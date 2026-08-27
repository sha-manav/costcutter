"""Figure 6 — assertions passed per 100k inference tokens.

Work per token rather than work per dollar: the same ranking with pricing
taken out, so a model that looks good only because its vendor is cheap this
quarter separates from one that is actually efficient.

Sorted bars, ours filled, base hollow, baselines grey.

**Read this one with the same caution as the all-pass Pareto.** Assertions
passed per token rewards brevity, and our checkpoint is brief because it
refuses: 1.47 actions per task against the base model's 2.12. Some of the
assertions it passes are passed *by not writing*, on instances where writing
nothing is correct. So its lead here is partly efficiency and partly the same
refusal artefact that inflates its all-pass rate. The must-write panel of
Figure 4 is the check on both.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt          # noqa: E402

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))
from figure_pareto import arms, OURS, BASE, _short   # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path,
                    default=REPO / "artifacts" / "charts" / "fig6_intelligence.png")
    args = ap.parse_args()

    data = arms()
    order = sorted(data.items(), key=lambda kv: kv[1]["assertions_per_100k"])
    labels = [_short(m) for m, _ in order]
    vals = [v["assertions_per_100k"] for _, v in order]

    fig, ax = plt.subplots(figsize=(7.6, 4.6))
    for i, (model, v) in enumerate(order):
        if model == OURS:
            ax.barh(i, v["assertions_per_100k"], color="black", height=0.66)
        elif model == BASE:
            ax.barh(i, v["assertions_per_100k"], facecolor="none",
                    edgecolor="black", lw=1.3, height=0.66)
        else:
            ax.barh(i, v["assertions_per_100k"], color="0.62", height=0.66)
        ax.annotate(f"{v['assertions_per_100k']:.0f}",
                    (v["assertions_per_100k"], i), xytext=(4, 0),
                    textcoords="offset points", va="center", fontsize=7.5)
    ax.set_yticks(range(len(labels)))
    ax.set_yticklabels(labels, fontsize=8)
    ax.set_xlabel("assertions passed per 100k inference tokens")
    ax.set_title("Work per token, pricing removed", fontsize=10, loc="left")
    ax.annotate("rewards brevity: a model that refuses early emits few tokens\n"
                "and still passes the assertions that require no write",
                (0.98, 0.04), xycoords="axes fraction", fontsize=7,
                style="italic", color="0.35", ha="right")
    ax.grid(alpha=0.25, lw=0.5, axis="x")
    ax.set_xlim(0, max(vals) * 1.16)
    fig.tight_layout()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(args.out, dpi=200, bbox_inches="tight")
    print(f"  wrote {args.out}")
    for m, v in reversed(order):
        print(f"  {_short(m):26} {v['assertions_per_100k']:7.1f}  (n={v['n']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
