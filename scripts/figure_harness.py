"""Figure 1 — the harness effect, by firm, with intervals.

The corrected harness documents reusable primitives, returns typed errors and
keeps observations concise. The naive one reproduces three specific defects:
undocumented actions that still execute, saves that cannot fail, and verbose
observations. Both variants execute through one shared code path, so a naive
failure is never a capability the corrected harness quietly added.

Grouped by firm because the firms have genuinely different correct answers,
and an effect that only appears at the permissive firm would be a different
claim from one that holds across all three.
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

from erpbench.gate import diff_interval, wilson     # noqa: E402

# The pre-registered thirteen, base Qwen3-14B, both harness variants on one
# serving path. This is the arm Figure 1 rests on.
SOURCE = "artifacts/s2_shards/shard_*_of_6.jsonl"


def load() -> list[dict]:
    return [json.loads(l) for f in glob.glob(SOURCE)
            for l in open(f) if l.strip() and json.loads(l).get("status") != "error"]


def rate(rows, variant, firm=None):
    sel = [r for r in rows if r.get("harness_variant") == variant
           and (firm is None or r.get("firm_id") == firm)]
    if not sel:
        return 0, 0
    return sum(1 for r in sel if r["verdict"].get("success")), len(sel)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path,
                    default=REPO / "artifacts" / "charts" / "fig1_harness.png")
    args = ap.parse_args()

    rows = load()
    firms = sorted({r["firm_id"] for r in rows})
    groups = [("all firms", None)] + [(f"firm {f}", f) for f in firms]

    fig, axes = plt.subplots(1, 2, figsize=(10.6, 4.3),
                             gridspec_kw={"width_ratios": [1.35, 1]})

    ax = axes[0]
    width, xs = 0.36, range(len(groups))
    for i, (_label, firm) in enumerate(groups):
        sn, nn = rate(rows, "naive", firm)
        sc, nc = rate(rows, "corrected", firm)
        for off, (s, n, style) in ((-width / 2, (sn, nn, "hollow")),
                                   (+width / 2, (sc, nc, "filled"))):
            if not n:
                continue
            p = 100 * s / n
            lo, hi = wilson(s, n)
            kw = (dict(color="black") if style == "filled"
                  else dict(facecolor="none", edgecolor="black", lw=1.3))
            ax.bar(i + off, p, width=width, **kw)
            ax.errorbar(i + off, p, yerr=[[p - 100 * lo], [100 * hi - p]],
                        fmt="none", ecolor="0.35", capsize=3, lw=1.0)
            ax.annotate(f"{p:.0f}", (i + off, p), xytext=(0, 9),
                        textcoords="offset points", ha="center", fontsize=7.5)
    ax.set_xticks(list(xs))
    ax.set_xticklabels([g[0] for g in groups], fontsize=9)
    ax.set_ylabel("all assertions pass, no unexpected mutation  (%)")
    ax.set_title("a. Naive (hollow) against corrected (filled), 95% Wilson",
                 fontsize=9.5, loc="left")
    ax.set_ylim(0, 55)
    ax.grid(alpha=0.25, lw=0.5, axis="y")

    ax = axes[1]
    labels, points, los, his = [], [], [], []
    for label, firm in groups:
        sn, nn = rate(rows, "naive", firm)
        sc, nc = rate(rows, "corrected", firm)
        if not (nn and nc):
            continue
        d = 100 * (sc / nc - sn / nn)
        lo, hi = diff_interval(sn, nn, sc, nc)
        labels.append(f"{label}\n(n={nn}+{nc})")
        points.append(d)
        los.append(d - 100 * lo)
        his.append(100 * hi - d)
    y = range(len(labels))
    ax.errorbar(points, list(y), xerr=[los, his], fmt="o", color="black",
                ecolor="0.35", capsize=4, ms=6, lw=1.2)
    ax.axvline(0, color="0.55", lw=1.0, ls=(0, (4, 3)))
    ax.set_yticks(list(y))
    ax.set_yticklabels(labels, fontsize=8)
    ax.invert_yaxis()
    ax.set_xlabel("corrected − naive  (percentage points, 95% Newcombe)")
    ax.set_title("b. The difference, with zero marked", fontsize=9.5, loc="left")
    ax.grid(alpha=0.25, lw=0.5, axis="x")

    fig.suptitle("Harness design, model held fixed. Both variants run through "
                 "one shared execution path,\nso a naive failure is never a "
                 "capability the corrected harness quietly added.",
                 fontsize=9, y=1.03)
    fig.tight_layout()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(args.out, dpi=200, bbox_inches="tight")
    print(f"  wrote {args.out}")
    for label, firm in groups:
        sn, nn = rate(rows, "naive", firm)
        sc, nc = rate(rows, "corrected", firm)
        if not (nn and nc):
            continue
        lo, hi = diff_interval(sn, nn, sc, nc)
        print(f"  {label:12} naive {sn:3}/{nn:<3} corrected {sc:3}/{nc:<3}  "
              f"{100*(sc/nc-sn/nn):+5.1f}%  [{100*lo:+5.1f}, {100*hi:+5.1f}]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
