"""Produce demonstration traffic on OBSERVE templates.

This stands in for "watch a person use the app": the same UI flows a user
would perform, driven through Playwright with the capture proxy in front.

It refuses EVAL template ids. That refusal is the mechanism that keeps the
headline number honest, so it raises rather than skips.
"""
from __future__ import annotations

import argparse
import json
import random
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

from shadow.browser import browser_page, login
from shadow.capture.proxy import capture_proxy
from shadow.config import Config, get_config
from shadow.bench.tasks import (
    HeldOutViolation, Task, assert_observe_only, load_split, observe_tasks,
)
from shadow.route.actions import perform
from shadow.route.observation import snapshot
from shadow.route.recipes import RECIPES

# Parameter pools used to vary demonstrations between sessions. Varying the
# values is what lets induction tell a parameter apart from a constant.
VARIATIONS: dict[str, dict[str, list[Any]]] = {
    "T01_customer_outstanding": {"customer": [
        "Acme Industrial", "Borealis Freight", "Everest Outfitters",
        "Cobalt Robotics", "Delta Mining Co", "Halcyon Media",
        "Granite Construction", "Fjord Marine"]},
    "T02_overdue_invoices": {},
    "T04_customer_open_orders": {"customer": [
        "Cobalt Robotics", "Delta Mining Co", "Halcyon Media",
        "Acme Industrial", "Ironwood Timber", "Juniper Analytics"]},
    "T05_item_price": {"item_code": [
        "SH-GEAR-01", "SH-VALVE-01", "SH-PUMP-01", "SH-BEARING-01",
        "SH-MOTOR-01", "SH-BELT-01", "SH-SENSOR-01"]},
    "T07_items_below_price": {"max_rate": [100, 50, 200, 75, 150, 300]},
    "T08_create_customer": {"customer_name": [
        "Vantage Rail", "Willow Ceramics", "Xenon Displays", "Yarrow Textiles",
        "Zephyr Turbines", "Anvil Machining", "Basalt Quarry", "Cirrus Avionics",
        "Dunes Solar", "Emberline Foods"]},
    "T11_update_item_price": {
        "item_code": ["SH-BEARING-02", "SH-SHAFT-01", "SH-SENSOR-02",
                      "SH-GEAR-02", "SH-CABLE-01", "SH-PANEL-01"],
        "new_rate": [71.25, 235.00, 151.50, 92.40, 24.10, 333.00]},
    "T13_update_customer_group": {
        "customer": ["Lumen Optics", "Kestrel Aviation", "Ironwood Timber",
                     "Juniper Analytics", "Halcyon Media", "Fjord Marine"],
        "customer_group": ["Individual", "Commercial"]},
    "T15_create_contact": {
        "first_name": ["Priya", "Tomas", "Ada", "Nils", "Rosa", "Kwame"],
        "last_name": ["Raman", "Lindqvist", "Okonkwo", "Berg", "Alvarez", "Mensah"]},
    "T16_create_lead": {
        "company_name": ["Halcyon Robotics", "Pinewood Freight",
                         "Quartz Analytics", "Vellum Press", "Ridgeline Tools"],
        "first_name": ["Dana", "Elias", "Mei", "Owen", "Sofia"]},
    "T17_create_warehouse": {
        "warehouse_name": ["Overflow Bay", "Cold Store", "Transit Dock",
                           "Mezzanine", "Yard Two"]},
    "T18_create_item_group": {
        "item_group_name": ["Bearings", "Actuators", "Fasteners",
                            "Enclosures", "Sensors"]},
    "T19_create_supplier_group": {
        "supplier_group_name": ["Castings", "Fabrication", "Logistics Partners",
                                "Electronics", "Tooling"]},
    "T20_create_territory": {
        "territory_name": ["Nordics", "Iberia", "Great Lakes", "Benelux",
                           "Cascadia"]},
}


@dataclass
class SessionEntry:
    """Side-car manifest. Written outside the capture stream so that no
    template label ever reaches the synthesis pipeline."""

    session: int
    template_id: str
    params: dict[str, Any]
    ts_start: float
    ts_end: float
    ok: bool
    error: str | None = None


# Parameters that must be unique across demonstrations: creating the same
# customer twice fails, and a failed demonstration teaches nothing.
UNIQUE_PARAMS: dict[str, str] = {
    "T08_create_customer": "customer_name",
    "T15_create_contact": "last_name",
    "T16_create_lead": "company_name",
    "T17_create_warehouse": "warehouse_name",
    "T18_create_item_group": "item_group_name",
    "T19_create_supplier_group": "supplier_group_name",
    "T20_create_territory": "territory_name",
    # In-distribution writes. Without a per-session nonce the second session
    # would try to create a record that already exists and the demonstration
    # would fail rather than produce traffic.
    "D06_create_customer": "customer_name",
    "D07_create_contact": "last_name",
    "D08_create_lead": "lead_name",
    "D09_create_warehouse": "warehouse_name",
    "D10_create_territory": "territory_name",
}


def vary(template_id: str, base: dict[str, Any], rng: random.Random,
         nonce: int = 0) -> dict[str, Any]:
    pool = VARIATIONS.get(template_id, {})
    params = dict(base)
    for key, values in pool.items():
        params[key] = rng.choice(values)
    unique_key = UNIQUE_PARAMS.get(template_id)
    if unique_key and unique_key in params:
        params[unique_key] = f"{params[unique_key]} {nonce:03d}"
    return params


def run_recipe(page: Any, template_id: str, params: dict[str, Any],
               cfg: Config) -> None:
    """Drive one demonstration directly (no model in the loop)."""
    recipe = RECIPES[template_id](params)
    obs = snapshot(page)
    action = next(recipe)
    for _ in range(cfg.bench.max_steps):
        outcome = perform(page, obs, action, cfg)
        if outcome.observation is not None:
            obs = outcome.observation
        if outcome.done:
            return
        try:
            action = recipe.send(obs)
        except StopIteration:
            return


def generate(sessions: int, cfg: Config | None = None, seed: int = 0,
             out_path: Path | None = None, manifest_path: Path | None = None,
             idle_gap_s: float | None = None,
             tasks: list[Task] | None = None,
             guard: Callable[[str], None] | None = None) -> list[SessionEntry]:
    """Drive demonstrations and capture the traffic.

    `tasks` and `guard` let the in-distribution regime reuse this driver with
    its own task set and its own holdout guard. The default remains the
    held-out split with `assert_observe_only`, so the structural refusal to
    demonstrate an EVAL template is unchanged.
    """
    cfg = cfg or get_config()
    out_path = out_path or cfg.path("capture")
    manifest_path = manifest_path or (cfg.path("artifacts") / "observe_manifest.json")
    # Sessions must be separated by more than the segmentation idle gap, or
    # two demonstrations would fuse into one episode.
    gap = (idle_gap_s if idle_gap_s is not None else cfg.segment.idle_gap_s) + 3.0

    if tasks is None:
        tasks = observe_tasks(cfg)
    guard = guard or (lambda tid: assert_observe_only(tid, cfg))
    for task in tasks:
        guard(task.template_id)                      # loud, structural guard

    rng = random.Random(seed)
    entries: list[SessionEntry] = []
    with capture_proxy(cfg, out_path=out_path) as proxy:
        with browser_page(cfg, proxy_url=proxy.url) as page:
            login(page, cfg)
            for session in range(sessions):
                ordered = list(tasks)
                rng.shuffle(ordered)
                for task in ordered:
                    params = vary(task.template_id, task.params, rng,
                                  nonce=len(entries))
                    t0 = time.time()
                    error = None
                    try:
                        run_recipe(page, task.template_id, params, cfg)
                    except Exception as exc:
                        error = f"{type(exc).__name__}: {exc}"
                    entries.append(SessionEntry(
                        session=session, template_id=task.template_id,
                        params=params, ts_start=t0, ts_end=time.time(),
                        ok=error is None, error=error))
                    print(f"[observe] s{session} {task.template_id} "
                          f"{'ok' if error is None else error[:80]}", flush=True)
                    # Idle gap so the segmenter can find the boundary.
                    time.sleep(gap)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps([asdict(e) for e in entries], indent=2))
    return entries


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sessions", type=int, default=3)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--template", help="generate a single template (must be OBSERVE)")
    args = ap.parse_args()
    cfg = get_config()
    if args.template:
        assert_observe_only(args.template, cfg)
    entries = generate(args.sessions, cfg, seed=args.seed)
    ok = sum(1 for e in entries if e.ok)
    print(f"{ok}/{len(entries)} demonstrations completed; "
          f"capture at {cfg.path('capture')}")
    return 0 if ok == len(entries) else 1


if __name__ == "__main__":
    raise SystemExit(main())
