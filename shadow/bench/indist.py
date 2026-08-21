"""The in-distribution regime: observe a workflow, then automate that workflow.

The EVAL split in `tasks.py` holds out whole task *templates*, which measures
transfer: watch someone do one kind of work, then automate a kind never
watched. That is the harder question and it is not the question this system
faces in deployment. In deployment you watch someone do the work you are
about to automate.

So this is a second, parallel evaluation with its own namespace, its own
captured traffic and its own induced tools. It holds out *instances*: the
same templates are observed and evaluated, but never with the same parameter
values. That is the right control for this regime — template-level holdout is
what the existing EVAL already measures, and mixing the two would answer
neither question.

Nothing here touches `artifacts/split.json`, the six EVAL template ids, or
`artifacts/tools.json`. The two evaluations are reported side by side and
never merged.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from shadow.bench.tasks import Task, TaskTemplate

# Entities present in the seed image. Reads vary over these; the parameter
# values an agent is evaluated on never appear in the observed traffic.
_OBSERVED_CUSTOMERS = ("Acme Industrial", "Borealis Freight", "Cobalt Robotics",
                       "Delta Mining Co", "Everest Outfitters", "Fjord Marine")
_HELDOUT_CUSTOMERS = ("Granite Construction", "Halcyon Media", "Ironwood Timber")
_OBSERVED_ITEMS = ("SH-BEARING-01", "SH-BEARING-02", "SH-BELT-01",
                   "SH-CABLE-01", "SH-GEAR-01", "SH-GEAR-02")
_HELDOUT_ITEMS = ("SH-MOTOR-01", "SH-MOTOR-02", "SH-PANEL-01")


@dataclass(frozen=True)
class IndistTemplate:
    """A template evaluated in the in-distribution regime.

    `observe_params` generate the demonstration traffic. `eval_params` are
    the held-out instances the agent is scored on. They must be disjoint, and
    `check_instance_holdout()` enforces it: an overlap would turn this from a
    measurement of the deployment regime into a measurement of memorisation.
    """
    id: str
    title: str
    goal: str
    kind: str
    check: str
    observe_params: tuple[dict[str, Any], ...]
    eval_params: tuple[dict[str, Any], ...]
    writes: str | None = None

    def as_template(self, params: tuple[dict[str, Any], ...]) -> TaskTemplate:
        return TaskTemplate(id=self.id, title=self.title, goal=self.goal,
                            kind=self.kind, check=self.check,
                            param_sets=params, writes=self.writes)


def _customers(names, extra=None):
    return tuple({"customer": n, **(extra or {})} for n in names)


def _items(codes, extra=None):
    return tuple({"item_code": c, **(extra or {})} for c in codes)


INDIST_TEMPLATES: tuple[IndistTemplate, ...] = (
    # ---------------- reads ----------------
    IndistTemplate(
        id="D01_customer_invoice_count",
        title="Count a customer's sales invoices",
        goal=("How many sales invoices exist for the customer '{customer}'? "
              "Answer with the count only."),
        kind="read", check="invoices_for_customer",
        observe_params=_customers(_OBSERVED_CUSTOMERS),
        eval_params=_customers(_HELDOUT_CUSTOMERS),
    ),
    IndistTemplate(
        id="D02_customer_invoice_total",
        title="Total a customer's invoiced amount",
        goal=("What is the combined grand total of all sales invoices for the "
              "customer '{customer}'? Answer with the number only."),
        kind="read", check="customer_invoice_total",
        observe_params=_customers(_OBSERVED_CUSTOMERS),
        eval_params=_customers(_HELDOUT_CUSTOMERS),
    ),
    IndistTemplate(
        id="D03_customer_order_count",
        title="Count a customer's sales orders",
        goal=("How many sales orders exist for the customer '{customer}'? "
              "Answer with the count only."),
        kind="read", check="customer_order_count",
        observe_params=_customers(_OBSERVED_CUSTOMERS),
        eval_params=_customers(_HELDOUT_CUSTOMERS),
    ),
    IndistTemplate(
        id="D04_item_stock_in_warehouse",
        title="Find an item's stock in one warehouse",
        goal=("What is the actual quantity of item '{item_code}' in the "
              "warehouse '{warehouse}'? Answer with the number only."),
        kind="read", check="item_stock_in_warehouse",
        observe_params=_items(_OBSERVED_ITEMS, {"warehouse": "Stores - ST"}),
        eval_params=_items(_HELDOUT_ITEMS, {"warehouse": "Stores - ST"}),
    ),
    IndistTemplate(
        id="D05_item_selling_price",
        title="Look up an item's selling price",
        goal=("What is the standard selling price of item '{item_code}'? "
              "Answer with the number only."),
        kind="read", check="item_price",
        observe_params=_items(_OBSERVED_ITEMS),
        eval_params=_items(_HELDOUT_ITEMS),
    ),

    # ---------------- flat-form writes ----------------
    # Child-table workflows are deliberately absent: they are covered by the
    # held-out set and would confound this regime with the grid behaviour.
    IndistTemplate(
        id="D06_create_customer",
        title="Create a customer",
        goal=("Create a new customer named '{customer_name}'. "
              "Save the record."),
        kind="write", check="customer_created", writes="Customer",
        observe_params=({"customer_name": "Marlow Systems"},
                        {"customer_name": "Nimbus Logistics"},
                        {"customer_name": "Onyx Ceramics"},
                        {"customer_name": "Pelican Foods"}),
        eval_params=({"customer_name": "Quartz Instruments"},
                     {"customer_name": "Rialto Textiles"},
                     {"customer_name": "Solstice Energy"}),
    ),
    IndistTemplate(
        id="D07_create_contact",
        title="Create a contact",
        goal=("Create a new contact with first name '{first_name}' and last "
              "name '{last_name}'. Save the record."),
        kind="write", check="contact_created", writes="Contact",
        observe_params=({"first_name": "Dana", "last_name": "Whitfield"},
                        {"first_name": "Eli", "last_name": "Barrow"},
                        {"first_name": "Farah", "last_name": "Nasser"},
                        {"first_name": "Gil", "last_name": "Okonkwo"}),
        eval_params=({"first_name": "Hana", "last_name": "Petrov"},
                     {"first_name": "Ivo", "last_name": "Salazar"},
                     {"first_name": "Jae", "last_name": "Lindqvist"}),
    ),
    IndistTemplate(
        id="D08_create_lead",
        title="Create a lead",
        goal=("Create a new lead for the person '{lead_name}'. Save the record."),
        kind="write", check="lead_created", writes="Lead",
        observe_params=({"lead_name": "Tobias Frame"},
                        {"lead_name": "Ursula Hale"},
                        {"lead_name": "Viktor Amos"},
                        {"lead_name": "Wren Calloway"}),
        eval_params=({"lead_name": "Xenia Duarte"},
                     {"lead_name": "Yusuf Adler"},
                     {"lead_name": "Zara Milburn"}),
    ),
    IndistTemplate(
        id="D09_create_warehouse",
        title="Create a warehouse",
        goal=("Create a new warehouse named '{warehouse_name}'. "
              "Save the record."),
        kind="write", check="warehouse_created", writes="Warehouse",
        observe_params=({"warehouse_name": "Overflow Bay"},
                        {"warehouse_name": "Cold Store"},
                        {"warehouse_name": "Dock Annexe"},
                        {"warehouse_name": "Returns Hold"}),
        eval_params=({"warehouse_name": "Spares Cage"},
                     {"warehouse_name": "Transit Yard"},
                     {"warehouse_name": "Quarantine Room"}),
    ),
    IndistTemplate(
        id="D10_create_territory",
        title="Create a territory",
        goal=("Create a new territory named '{territory_name}'. "
              "Save the record."),
        kind="write", check="territory_created", writes="Territory",
        observe_params=({"territory_name": "Northern Reach"},
                        {"territory_name": "Coastal Belt"},
                        {"territory_name": "Highland Zone"},
                        {"territory_name": "River Basin"}),
        eval_params=({"territory_name": "Desert Rim"},
                     {"territory_name": "Lakeside East"},
                     {"territory_name": "Harbour West"}),
    ),
)

INDIST_IDS = tuple(t.id for t in INDIST_TEMPLATES)


class InstanceLeak(RuntimeError):
    """An evaluated parameter set was also demonstrated."""


def check_instance_holdout() -> None:
    """Fail loudly if any evaluated instance was observed.

    The whole claim of this regime rests on the parameter values being
    unseen. If they leak, the measurement stops being about automating an
    observed workflow and becomes about replaying a memorised one, and
    nothing downstream would notice.
    """
    for template in INDIST_TEMPLATES:
        observed = {tuple(sorted(p.items())) for p in template.observe_params}
        evaluated = {tuple(sorted(p.items())) for p in template.eval_params}
        overlap = observed & evaluated
        if overlap:
            raise InstanceLeak(
                f"{template.id}: evaluated instances were also demonstrated: "
                f"{sorted(dict(o) for o in overlap)}")


def _tasks(template: IndistTemplate,
           params: tuple[dict[str, Any], ...]) -> list[Task]:
    out = []
    for i, p in enumerate(params):
        out.append(Task(id=f"{template.id}#{i}", template_id=template.id,
                        goal=template.goal.format(**p), kind=template.kind,
                        check=template.check, params=dict(p)))
    return out


def observe_tasks() -> list[Task]:
    """Demonstration instances. These are the ones traffic is captured for."""
    check_instance_holdout()
    return [t for tpl in INDIST_TEMPLATES for t in _tasks(tpl, tpl.observe_params)]


def eval_tasks() -> list[Task]:
    """Held-out instances of the same templates."""
    check_instance_holdout()
    return [t for tpl in INDIST_TEMPLATES for t in _tasks(tpl, tpl.eval_params)]
