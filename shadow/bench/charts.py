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
    """Chart 2 — cost per successful task, A vs B."""
    a = score_condition(rows, "A_browser", cfg)
    b = score_condition(rows, "B_tools", cfg)
    fig, ax = plt.subplots(figsize=(5.2, 4.0), dpi=160)
    values = [a.usd_per_successful_task, b.usd_per_successful_task]
    labels = ["A: browser only", "B: tools + fallback"]
    bars = ax.bar(labels, values, color=[PALETTE["A"], PALETTE["B"]],
                  width=0.55, zorder=3)
    for bar, value in zip(bars, values):
        ax.text(bar.get_x() + bar.get_width() / 2, value,
                f"${value:.5f}", ha="center", va="bottom", fontsize=9)
    ax.set_ylabel("USD per successful task")
    ax.set_title("Cost per successful task on held-out templates")
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


def render_all(rows: list[dict[str, Any]], cfg: Config | None = None) -> list[Path]:
    cfg = cfg or get_config()
    out_dir = cfg.path("charts")
    made = []
    curve = coverage_curve(cfg.path("artifacts") / "coverage_sweep.json",
                           out_dir / "coverage_vs_sessions.png")
    if curve:
        made.append(curve)
    made.append(cost_per_success(rows, out_dir / "cost_per_successful_task.png", cfg))
    made.append(latency_distribution(rows, out_dir / "latency_distribution.png"))
    return made
