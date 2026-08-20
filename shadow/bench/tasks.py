"""Task registry and the OBSERVE / EVAL template split.

The split is over TEMPLATES, not instances. Observing "create a sales order"
and then evaluating on "create a sales order" measures memorisation, so the
partition happens here, once, before any traffic is captured, and is
persisted. `bench/generate_traffic.py` refuses EVAL template ids outright.
"""
from __future__ import annotations

import json
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from shadow.config import Config, get_config


class HeldOutViolation(RuntimeError):
    """Raised when demonstration traffic is requested for an EVAL template."""


@dataclass(frozen=True)
class TaskTemplate:
    id: str
    title: str
    goal: str                       # str.format template shown to the agent
    kind: str                       # "read" | "write"
    check: str                      # name of the oracle check
    param_sets: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class Task:
    id: str
    template_id: str
    goal: str
    kind: str
    check: str
    params: dict[str, Any] = field(default_factory=dict)


TEMPLATES: tuple[TaskTemplate, ...] = (
    # ---------------- reads ----------------
    TaskTemplate(
        id="T01_customer_outstanding",
        title="Look up a customer's outstanding balance",
        goal=("What is the total outstanding (unpaid) amount across all sales "
              "invoices for the customer '{customer}'? Answer with the number only."),
        kind="read", check="customer_outstanding",
        param_sets=({"customer": "Acme Industrial"},
                    {"customer": "Borealis Freight"},
                    {"customer": "Everest Outfitters"}),
    ),
    TaskTemplate(
        id="T02_overdue_invoices",
        title="List overdue invoices",
        goal=("How many submitted sales invoices are overdue (due date before "
              "today and still unpaid)? Answer with the count only."),
        kind="read", check="overdue_invoice_count",
        param_sets=({}, {}, {}),
    ),
    TaskTemplate(
        id="T03_stock_on_hand",
        title="Find stock on hand for an item",
        goal=("What is the total actual quantity in stock for item '{item_code}' "
              "across all warehouses? Answer with the number only."),
        kind="read", check="stock_on_hand",
        param_sets=({"item_code": "SH-BEARING-01"},
                    {"item_code": "SH-MOTOR-01"},
                    {"item_code": "SH-CABLE-01"}),
    ),
    TaskTemplate(
        id="T04_customer_open_orders",
        title="Count a customer's sales orders",
        goal=("How many sales orders exist for the customer '{customer}'? "
              "Answer with the count only."),
        kind="read", check="customer_order_count",
        param_sets=({"customer": "Cobalt Robotics"},
                    {"customer": "Delta Mining Co"},
                    {"customer": "Halcyon Media"}),
    ),
    TaskTemplate(
        id="T05_item_price",
        title="Look up an item's selling price",
        goal=("What is the Standard Selling price list rate for item "
              "'{item_code}'? Answer with the number only."),
        kind="read", check="item_price",
        param_sets=({"item_code": "SH-GEAR-01"},
                    {"item_code": "SH-VALVE-01"},
                    {"item_code": "SH-PUMP-01"}),
    ),
    TaskTemplate(
        id="T06_latest_order_total",
        title="Total of a customer's most recent sales order",
        goal=("What is the grand total of the most recent sales order for "
              "customer '{customer}'? Answer with the number only."),
        kind="read", check="latest_order_total",
        param_sets=({"customer": "Acme Industrial"},
                    {"customer": "Fjord Marine"},
                    {"customer": "Ironwood Timber"}),
    ),
    TaskTemplate(
        id="T07_items_below_price",
        title="Count items under a price threshold",
        goal=("How many items have a Standard Selling price list rate below "
              "{max_rate}? Answer with the count only."),
        kind="read", check="items_below_price",
        param_sets=({"max_rate": 100}, {"max_rate": 50}, {"max_rate": 200}),
    ),
    # ---------------- writes ----------------
    TaskTemplate(
        id="T08_create_customer",
        title="Create a customer",
        goal=("Create a new customer named '{customer_name}' with customer group "
              "'Commercial' and territory 'All Territories'."),
        kind="write", check="customer_created",
        param_sets=({"customer_name": "Meridian Logistics"},
                    {"customer_name": "Northgate Foods"},
                    {"customer_name": "Orchid Chemicals"}),
    ),
    TaskTemplate(
        id="T09_create_sales_order",
        title="Create a sales order",
        goal=("Create a sales order for customer '{customer}' with {qty} units of "
              "item '{item_code}'. Leave it as a draft."),
        kind="write", check="sales_order_created",
        param_sets=({"customer": "Kestrel Aviation", "item_code": "SH-GEAR-02", "qty": 5},
                    {"customer": "Juniper Analytics", "item_code": "SH-SENSOR-01", "qty": 12},
                    {"customer": "Granite Construction", "item_code": "SH-BELT-01", "qty": 7}),
    ),
    TaskTemplate(
        id="T10_create_supplier",
        title="Create a supplier",
        goal=("Create a new supplier named '{supplier_name}' with supplier group "
              "'Services'."),
        kind="write", check="supplier_created",
        param_sets=({"supplier_name": "Sable Tooling"},
                    {"supplier_name": "Terra Castings"},
                    {"supplier_name": "Umbra Coatings"}),
    ),
    TaskTemplate(
        id="T11_update_item_price",
        title="Update an item's selling price",
        goal=("Change the Standard Selling price list rate for item '{item_code}' "
              "to {new_rate}."),
        kind="write", check="item_price_updated",
        param_sets=({"item_code": "SH-BEARING-02", "new_rate": 71.25},
                    {"item_code": "SH-SHAFT-01", "new_rate": 235.00},
                    {"item_code": "SH-SENSOR-02", "new_rate": 151.50}),
    ),
    TaskTemplate(
        id="T12_create_item",
        title="Create an item",
        goal=("Create a new item with item code '{item_code}', item name "
              "'{item_name}', item group 'Products' and stock UOM 'Nos'."),
        kind="write", check="item_created",
        param_sets=({"item_code": "SH-CLAMP-01", "item_name": "Toggle Clamp 200N"},
                    {"item_code": "SH-ROTOR-01", "item_name": "Rotor Assembly 90mm"},
                    {"item_code": "SH-FILTER-01", "item_name": "Inline Filter 40um"}),
    ),
    TaskTemplate(
        id="T13_update_customer_group",
        title="Reclassify a customer",
        goal=("Change the customer group of customer '{customer}' to "
              "'{customer_group}'."),
        kind="write", check="customer_group_updated",
        param_sets=({"customer": "Lumen Optics", "customer_group": "Individual"},
                    {"customer": "Kestrel Aviation", "customer_group": "Individual"},
                    {"customer": "Ironwood Timber", "customer_group": "Individual"}),
    ),
    TaskTemplate(
        id="T14_create_sales_invoice",
        title="Create a sales invoice",
        goal=("Create a sales invoice for customer '{customer}' with {qty} units "
              "of item '{item_code}'. Leave it as a draft."),
        kind="write", check="sales_invoice_created",
        param_sets=({"customer": "Everest Outfitters", "item_code": "SH-MOTOR-02", "qty": 2},
                    {"customer": "Cobalt Robotics", "item_code": "SH-PANEL-01", "qty": 3},
                    {"customer": "Fjord Marine", "item_code": "SH-CABLE-01", "qty": 25}),
    ),
)


def all_tasks() -> list[Task]:
    tasks: list[Task] = []
    for tmpl in TEMPLATES:
        for i, params in enumerate(tmpl.param_sets):
            tasks.append(Task(
                id=f"{tmpl.id}#{i}",
                template_id=tmpl.id,
                goal=tmpl.goal.format(**params),
                kind=tmpl.kind,
                check=tmpl.check,
                params=dict(params),
            ))
    return tasks


def template(template_id: str) -> TaskTemplate:
    for t in TEMPLATES:
        if t.id == template_id:
            return t
    raise KeyError(template_id)


# --------------------------------------------------------------------------
# Split
# --------------------------------------------------------------------------

@dataclass
class Split:
    observe: list[str]
    eval: list[str]
    seed: int

    def to_dict(self) -> dict[str, Any]:
        return {"observe": self.observe, "eval": self.eval, "seed": self.seed}


def make_split(cfg: Config | None = None) -> Split:
    cfg = cfg or get_config()
    ids = sorted(t.id for t in TEMPLATES)
    rng = random.Random(cfg.bench.split_seed)
    rng.shuffle(ids)
    n_obs = round(len(ids) * cfg.bench.observe_fraction)
    observe = sorted(ids[:n_obs])
    held = sorted(ids[n_obs:])
    return Split(observe=observe, eval=held, seed=cfg.bench.split_seed)


def load_split(cfg: Config | None = None, create: bool = True) -> Split:
    """Load the persisted split, creating it once if absent.

    Never regenerated: a split that moves invalidates every number computed
    against an earlier one.
    """
    cfg = cfg or get_config()
    path = cfg.path("split")
    if path.exists():
        data = json.loads(path.read_text())
        return Split(observe=data["observe"], eval=data["eval"], seed=data["seed"])
    if not create:
        raise FileNotFoundError(f"no split at {path}")
    split = make_split(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(split.to_dict(), indent=2))
    return split


def observe_tasks(cfg: Config | None = None) -> list[Task]:
    split = load_split(cfg)
    return [t for t in all_tasks() if t.template_id in split.observe]


def eval_tasks(cfg: Config | None = None) -> list[Task]:
    split = load_split(cfg)
    return [t for t in all_tasks() if t.template_id in split.eval]


def assert_observe_only(template_id: str, cfg: Config | None = None) -> None:
    """Guard for the traffic generator. Loud by design."""
    split = load_split(cfg)
    if template_id in split.eval:
        raise HeldOutViolation(
            f"{template_id} is a held-out EVAL template; demonstration traffic "
            "may only be generated for OBSERVE templates. Refusing.")
    if template_id not in split.observe:
        raise HeldOutViolation(f"{template_id} is not a known template id")
