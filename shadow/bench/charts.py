"""Charts, generated from results.jsonl only."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from shadow.bench.metrics import score_condition, usd  # noqa: E402
from shadow.config import Config, get_config  # noqa: E402

PALETTE = {"A": "#B4654A", "B": "#3E7CB1", "grid": "#D8D8D8", "text": "#2B2B2B"}


def _style(ax) -> None:
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.grid(axis="y", color=PALETTE["grid"], linewidth=0.8, zorder=0)
    ax.set_axisbelow(True)


def coverage_curve(sweep_path: Path, out_path: Path) -> Path | None:
    """Chart 1 — coverage on EVAL vs number of observed OBSERVE sessions."""
    if not sweep_path.exists():
        return None
    points = json.loads(sweep_path.read_text())
    if not points:
        return None
    xs = [p["sessions"] for p in points]
    fig, ax = plt.subplots(figsize=(6.4, 4.0), dpi=160)
    ax.plot(xs, [p["coverage"] * 100 for p in points], "-o",
            color=PALETTE["B"], label="EVAL actions served by tools")
    if "attainable_coverage" in points[0]:
        ax.plot(xs, [p["attainable_coverage"] * 100 for p in points], "--s",
                color=PALETTE["A"], label="EVAL tasks a synthesized tool can serve")
    ax.set_xlabel("observed demonstration sessions")
    ax.set_ylabel("coverage (%)")
    ax.set_title("Coverage on held-out templates vs observation volume")
    ax.set_ylim(0, 100)
    ax.set_xticks(xs)
    ax.legend(frameon=False, fontsize=8)
    _style(ax)
    fig.tight_layout()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path)
    plt.close(fig)
    return out_path


def cost_per_success(rows: list[dict[str, Any]], out_path: Path,
                     cfg: Config) -> Path:
    """Chart 2 — cost per successful task, A vs B, priced both ways.

    The second pair reprices cache reads at the full input rate. Caching is
    structurally kinder to condition B — its prefix is fixed, A re-sends a
    page snapshot that changes every step — so showing only the billed
    number would hand B an advantage the reader cannot see.
    """
    a = score_condition(rows, "A_browser", cfg)
    b = score_condition(rows, "B_tools", cfg)
    fig, ax = plt.subplots(figsize=(6.4, 4.0), dpi=160)
    groups = ["as billed\n(cache discounted)", "cache repriced\nat full input rate"]
    a_vals = [a.usd_per_successful_task, a.usd_per_successful_task_uncached]
    b_vals = [b.usd_per_successful_task, b.usd_per_successful_task_uncached]
    # An infinite cost (a condition that never succeeded) cannot be drawn;
    # it is annotated instead so the bar is not silently read as zero.
    def finite(v: float) -> float:
        return 0.0 if v in (float("inf"), float("-inf")) else v

    x = range(len(groups))
    width = 0.36
    bars_a = ax.bar([i - width / 2 for i in x], [finite(v) for v in a_vals],
                    width, label="A: browser only", color=PALETTE["A"], zorder=3)
    bars_b = ax.bar([i + width / 2 for i in x], [finite(v) for v in b_vals],
                    width, label="B: tools + fallback", color=PALETTE["B"], zorder=3)
    for bars, vals in ((bars_a, a_vals), (bars_b, b_vals)):
        for bar, value in zip(bars, vals):
            text = "no success" if value == float("inf") else f"${value:.5f}"
            ax.text(bar.get_x() + bar.get_width() / 2, finite(value), text,
                    ha="center", va="bottom", fontsize=8)
    ax.set_xticks(list(x))
    ax.set_xticklabels(groups, fontsize=9)
    ax.set_ylabel("USD per successful task")
    ax.set_title("Cost per successful task on held-out templates")
    ax.legend(frameon=False, fontsize=8)
    _style(ax)
    fig.tight_layout()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path)
    plt.close(fig)
    return out_path


def latency_distribution(rows: list[dict[str, Any]], out_path: Path) -> Path:
    """Chart 3 — latency distribution, A vs B."""
    a = [float(r["wall_s"]) for r in rows if r["condition"] == "A_browser"]
    b = [float(r["wall_s"]) for r in rows if r["condition"] == "B_tools"]
    fig, ax = plt.subplots(figsize=(6.4, 4.0), dpi=160)
    parts = ax.boxplot([a or [0], b or [0]],
                       tick_labels=["A: browser", "B: tools"],
                       patch_artist=True, widths=0.45, showfliers=True, zorder=3)
    for patch, colour in zip(parts["boxes"], [PALETTE["A"], PALETTE["B"]]):
        patch.set_facecolor(colour)
        patch.set_alpha(0.75)
    for median in parts["medians"]:
        median.set_color(PALETTE["text"])
    ax.set_ylabel("wall-clock seconds per task")
    ax.set_title("Task latency distribution on held-out templates")
    _style(ax)
    fig.tight_layout()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path)
    plt.close(fig)
    return out_path


def catalog_tax(sweep_path: Path, out_path: Path) -> Path | None:
    """Chart 4 — cost and coverage against how many tools reach the prompt.

    Separates what a tool costs to *offer* from what it saves when *used*.
    """
    if not sweep_path.exists():
        return None
    points = json.loads(sweep_path.read_text())
    if not points:
        return None
    xs = [p["k"] for p in points]
    fig, ax = plt.subplots(figsize=(6.4, 4.0), dpi=160)
    ax.plot(xs, [p["usd_per_successful_task"] for p in points], "-o",
            color=PALETTE["A"], label="USD per successful task")
    ax.set_xlabel("tools retrieved into the prompt (k)")
    ax.set_ylabel("USD per successful task")
    ax.set_xticks(xs)
    _style(ax)

    right = ax.twinx()
    right.plot(xs, [p["task_coverage"] * 100 for p in points], "--s",
               color=PALETTE["B"], label="tasks finished on tools alone")
    right.set_ylabel("task coverage (%)")
    right.set_ylim(0, 100)
    right.spines["top"].set_visible(False)

    handles = ax.get_lines() + right.get_lines()
    ax.legend(handles, [h.get_label() for h in handles], frameon=False,
              fontsize=8, loc="center right")
    ax.set_title("The catalog tax: cost and coverage vs tools offered")
    fig.tight_layout()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path)
    plt.close(fig)
    return out_path


def render_all(rows: list[dict[str, Any]], cfg: Config | None = None) -> list[Path]:
    cfg = cfg or get_config()
    out_dir = cfg.path("charts")
    made = []
    curve = coverage_curve(cfg.path("artifacts") / "coverage_sweep.json",
                           out_dir / "coverage_vs_sessions.png")
    if curve:
        made.append(curve)
    tax = catalog_tax(cfg.path("artifacts") / "k_sweep.json",
                      out_dir / "catalog_tax_vs_k.png")
    if tax:
        made.append(tax)
    made.append(cost_per_success(rows, out_dir / "cost_per_successful_task.png", cfg))
    made.append(latency_distribution(rows, out_dir / "latency_distribution.png"))
    return made
