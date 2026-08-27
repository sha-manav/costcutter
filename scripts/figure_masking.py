"""Figure 2 — aggregate success rises while the capability it measures dies.

Panel (a) is the curriculum ladder: four checkpoints of one model on identical
rows, success climbing 21.5 → 28.2 while tasks requiring a database write go
6 → 6 → 0 → 0.

Panel (b) is the same effect reproducing on Firm C — frozen in week 1, never
seen by any trained checkpoint, never used in method selection. It is the
stronger panel: the ladder could be dismissed as an artefact of a task set the
authors built and watched, and Firm C could not be, because nothing was tuned
against it and it was opened once.

The counterfactual is the point of the figure. Reporting only the success
column would have published 80% transfer to an unseen firm for a model that
completes none of the three Firm C tasks that require a write.
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

LADDER = [
    ("base", "artifacts/s2_shards/shard_*_of_6.jsonl"),
    ("T1", "artifacts/t1_shards/shard_*_of_6.jsonl"),
    ("T2", "artifacts/t2b_shards/shard_*_of_6.jsonl"),
    ("T3", "artifacts/t3b_shards/shard_*_of_6.jsonl"),
]
FIRM_C = [(n, f"artifacts/firmc_{n}/shard_*_of_6.jsonl") for n in (0, 1, 3, 8)]


def load(pattern):
    return [json.loads(l) for f in glob.glob(pattern) for l in open(f)
            if l.strip()]


def _clean(rows):
    return [r for r in rows if r.get("status") != "error"
            and r.get("harness_variant") == "corrected"]


def needs_write(*row_sets):
    """Which instances require a write, taken across every arm at once.

    Derived per-arm this would be a denominator that depends on what each
    model happened to attempt: a checkpoint that never writes contributes no
    `matched_allowed`, so its own must-write set comes out smaller and its
    rate is computed against a friendlier task set than its neighbour's.
    Whether an instance requires a write is a property of the instance.
    """
    need = set()
    for rows in row_sets:
        for r in rows:
            env = r["verdict"].get("envelope", {})
            if env.get("missing_required") or env.get("matched_allowed"):
                need.add((r["template_id"], r["firm_id"], r.get("params_seed")))
    return need


def split(rows, need):
    rows = _clean(rows)
    w = [r for r in rows
         if (r["template_id"], r["firm_id"], r.get("params_seed")) in need]
    nw = [r for r in rows
          if (r["template_id"], r["firm_id"], r.get("params_seed")) not in need]
    ok = lambda xs: sum(1 for r in xs if r["verdict"].get("success"))  # noqa: E731
    return {"n": len(rows), "all": ok(rows), "w": ok(w), "wn": len(w),
            "nw": ok(nw), "nwn": len(nw)}


def _panel(ax, labels, stats, xlabel, title, write_label):
    xs = range(len(labels))
    ax.plot(xs, [100 * s["all"] / s["n"] for s in stats], color="black",
            marker="o", ms=7, lw=1.8, label="all-pass rate (the headline)")
    ax.plot(xs, [100 * s["w"] / max(s["wn"], 1) for s in stats], color="0.45",
            marker="s", ms=6, lw=1.4, ls="--",
            label=write_label)
    for i, s in enumerate(stats):
        ax.annotate(f"{s['w']}/{s['wn']}",
                    (i, 100 * s["w"] / max(s["wn"], 1)), xytext=(0, -14),
                    textcoords="offset points", ha="center", fontsize=7,
                    color="0.3")
    ax.set_xticks(list(xs))
    ax.set_xticklabels(labels)
    ax.set_xlabel(xlabel)
    ax.set_ylabel("success  (%)")
    ax.set_title(title, fontsize=9.5, loc="left")
    ax.set_ylim(0, 100)
    ax.grid(alpha=0.25, lw=0.5)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path,
                    default=REPO / "artifacts" / "charts" / "fig2_masking.png")
    args = ap.parse_args()

    fig, axes = plt.subplots(1, 2, figsize=(11.2, 4.5))

    ladder_rows = [_clean(load(p)) for _, p in LADDER]
    ladder_need = needs_write(*ladder_rows)
    ladder = [split(rs, ladder_need) for rs in ladder_rows]
    _panel(axes[0], [l for l, _ in LADDER], ladder, "curriculum stage",
           "a. Four checkpoints, identical rows",
           "tasks requiring a write")
    axes[0].legend(fontsize=7.5, loc="upper left")

    firmc_rows = [_clean(load(p)) for _, p in FIRM_C]
    firmc_need = needs_write(*firmc_rows)
    firmc = [split(rs, firmc_need) for rs in firmc_rows]
    _panel(axes[1], [str(n) for n, _ in FIRM_C], firmc,
           "operator corrections",
           "b. Firm C — frozen, opened once, never trained on",
           "tasks requiring a write")
    subs = 0
    for _, p in FIRM_C:
        for r in load(p):
            for a in r.get("actions") or []:
                v = a.get("action")
                v = v.get("action") if isinstance(v, dict) else v
                if v == "submit" and a.get("outcome") == "success":
                    subs += 1
    nw_total = firmc[0]["nwn"] + firmc[0]["wn"]
    axes[1].annotate(
        f"{firmc[0]['nwn']} of {nw_total} Firm C instances are correctly\n"
        f"answered by writing nothing — and C forbids submitting\n"
        f"entirely: {subs} successful submits across all 156 rows.",
        (0.03, 0.05), xycoords="axes fraction", fontsize=7.2, color="0.25")

    fig.suptitle("Success rate rises; the ability it is taken to measure goes "
                 "to zero. Reporting only the black line would have\npublished "
                 "80% transfer to an unseen firm for a model that completes "
                 "none of its writes.", fontsize=9, y=1.04)
    fig.tight_layout()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(args.out, dpi=200, bbox_inches="tight")
    print(f"  wrote {args.out}")
    for (lab, _), s in zip(LADDER, ladder):
        print(f"  ladder {lab:5} all {100*s['all']/s['n']:5.1f}%  "
              f"must-write {s['w']}/{s['wn']}")
    for (n, _), s in zip(FIRM_C, firmc):
        print(f"  firmC  c={n:<3} all {100*s['all']/s['n']:5.1f}%  "
              f"must-write {s['w']}/{s['wn']}  write-nothing {s['nw']}/{s['nwn']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
