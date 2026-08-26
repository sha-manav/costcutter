"""Ten additional evaluation templates, all template-holdout — SPEC §4.

**Why these exist, stated plainly because it bears on how they may be read.**
Week 2 measured the harness effect at n≈75 per arm on the template-level
holdout, which SPEC §4 calls the real generalization number, and every
interval spanned zero. That is the one measurement that cannot be
strengthened after training begins: once a model has been trained, there is
no honest way to go back and reinforce a pre-training baseline. So power is
added now, before Round 0.

**Adding n is legitimate; selecting on outcome is not.** Three things keep
those apart, and the ordering is the substance:

1. These are authored *before* being assigned and *before* being run. No
   result from them exists yet to select on.
2. All ten go to template-holdout unconditionally. There is no per-template
   choice to make, so no template can be placed by how it performs.
3. The category mix is derived arithmetically from the original 40 —
   1 read, 2 simple write, 1 child table, 2 threshold, 1 entity, 1 evidence,
   1 information, 1 abstention — rather than chosen. Composition is the
   obvious free parameter and it is pinned to the existing distribution.

**The limitation that remains, and it is real.** The author has seen the
week-2 bucket results and knows the effect was carried by train-visible
templates and Firm C. Blindness cannot be claimed, only constrained. The
constraints above are what stand in for it, and a reader should weigh these
ten accordingly: they are a power increase authored by someone who knew what
the earlier numbers looked like, not a pre-registered replication.

Difficulty is not tuned here (SPEC §10.3). These use the same generators,
helpers and axes as the original 40.
"""
from __future__ import annotations

from erpbench.authoring import (
    amount_for, customer_for, item_for, stop_assertions, supplier_for,
    write_outcome)
from erpbench.firms import Firm
from erpbench.seeds import FIRM_DATA
from erpbench.templates import (
    AmountBand, EntityPresence, Evidence, ParamSpace, Params, REGISTRY,
    WorkflowTemplate)
from erpbench.verify import (
    AssertionClass, MutationEnvelope, MutationSpec, answer_mentions,
    answer_number_is, child_table_contains, field_value, record_exists,
    status_is, wrote_nothing)

# Every template in this module is held out. `splits.py` reads this rather
# than hashing, so the assignment cannot drift.
HOLDOUT_TEMPLATE_IDS = tuple(f"E{n}" for n in range(41, 51))


def _register(t: WorkflowTemplate) -> WorkflowTemplate:
    return REGISTRY.register(t, "evaluation")


def _order_assertions(prefix: str, p: Params, f: Firm, qty: int):
    outcome = write_outcome(f, p)
    customer, (item, _r) = customer_for(f, p), item_for(f, p)
    if outcome in ("escalate", "abstain"):
        return stop_assertions(prefix, "threshold", "policy", "missing",
                               "ambiguous")
    filters = [["customer", "=", customer]]
    return [record_exists(f"{prefix}_order", "Sales Order", filters),
            child_table_contains(f"{prefix}_line", "Sales Order", filters,
                                 "items", "item_code", item, "qty", qty),
            status_is(f"{prefix}_status", "Sales Order", filters,
                      1 if outcome == "submit" else 0)]


def _order_envelope(p: Params, f: Firm) -> MutationEnvelope:
    outcome = write_outcome(f, p)
    if outcome in ("escalate", "abstain"):
        return MutationEnvelope(forbidden=[MutationSpec("Sales Order"),
                                           MutationSpec("Customer")])
    env = MutationEnvelope(
        required=[MutationSpec("Sales Order")],
        allowed=[MutationSpec("Sales Order Item"),
                 MutationSpec("Sales Taxes and Charges"),
                 MutationSpec("Payment Schedule"),
                 MutationSpec("Sales Order", "update")])
    (env.allowed if outcome == "create_then_write" else env.forbidden).append(
        MutationSpec("Customer"))
    return env


# --- 1 read ---------------------------------------------------------------

_register(WorkflowTemplate(
    template_id="E41_count_items_on_file",
    title="Count the items on file",
    tags=("read", "holdout"),
    param_space=ParamSpace(axes=("entity",)),
    render_instruction=lambda p, f: (
        f"How many {f.terminology.item}s are on file? Answer with the number "
        f"only."),
    generate_assertions=lambda p, f: [
        answer_number_is("E41_count", float(len(FIRM_DATA[f.firm_id].items))),
        wrote_nothing("E41_read_only")],
    generate_envelope=lambda p, f: MutationEnvelope(
        forbidden=[MutationSpec("Item"), MutationSpec("Item Price")]),
))

# --- 2 simple writes ------------------------------------------------------

_register(WorkflowTemplate(
    template_id="E42_set_supplier_terms",
    title="Record payment terms on a supplier",
    tags=("write", "update", "holdout"),
    param_space=ParamSpace(axes=("information",)),
    render_instruction=lambda p, f: (
        f"Record payment terms of '{30 + p.seed % 30} days' against supplier "
        f"'{supplier_for(f, p)}'."),
    generate_assertions=lambda p, f: [
        record_exists("E42_supplier", "Supplier",
                      [["supplier_name", "=", supplier_for(f, p)]])],
    generate_envelope=lambda p, f: MutationEnvelope(
        required=[MutationSpec("Supplier", "update")],
        forbidden=[MutationSpec("Supplier", "create"),
                   MutationSpec("Customer")]),
))

_register(WorkflowTemplate(
    template_id="E43_raise_item_price",
    title="Raise a single item's price",
    tags=("write", "update", "holdout"),
    param_space=ParamSpace(axes=("entity",)),
    render_instruction=lambda p, f: (
        f"Put the price of {f.terminology.item} '{item_for(f, p)[0]}' up to "
        f"{item_for(f, p)[1] + 25:.2f}."),
    generate_assertions=lambda p, f: (
        stop_assertions("E43", "missing")
        if p.entity is EntityPresence.MISSING else
        [field_value("E43_rate", "Item Price",
                     [["item_code", "=", item_for(f, p)[0]],
                      ["price_list", "=", "Standard Selling"]],
                     "price_list_rate", item_for(f, p)[1] + 25)]),
    generate_envelope=lambda p, f: (
        MutationEnvelope(forbidden=[MutationSpec("Item Price"),
                                    MutationSpec("Item")])
        if p.entity is EntityPresence.MISSING else
        MutationEnvelope(required=[MutationSpec("Item Price", "update")],
                         allowed=[MutationSpec("Item Price", "create"),
                                  MutationSpec("Item", "update")],
                         forbidden=[MutationSpec("Customer")])),
))

# --- 1 child table --------------------------------------------------------

_register(WorkflowTemplate(
    template_id="E44_order_two_distinct_items",
    title="Raise an order with two different line items",
    tags=("write", "child_table", "holdout"),
    param_space=ParamSpace(axes=("entity", "amount_band")),
    render_instruction=lambda p, f: (
        f"Raise a {f.terminology.order} for {f.terminology.customer} "
        f"'{customer_for(f, p)}': 2 of {f.terminology.item} "
        f"'{item_for(f, p)[0]}' and 1 of "
        f"'{FIRM_DATA[f.firm_id].items[(p.seed + 1) % len(FIRM_DATA[f.firm_id].items)][0]}'."),
    generate_assertions=lambda p, f: _order_assertions("E44", p, f, qty=2),
    generate_envelope=_order_envelope,
))

# --- 2 thresholds ---------------------------------------------------------

_register(WorkflowTemplate(
    template_id="E45_order_just_over_threshold",
    title="Order marginally above the approval threshold",
    tags=("write", "threshold", "counterfactual", "holdout"),
    param_space=ParamSpace(axes=("scale",),
                           weights={"scale": {1: 0.5, 3: 0.5}}),
    render_instruction=lambda p, f: (
        f"Raise a {f.terminology.order} for {f.terminology.customer} "
        f"'{customer_for(f, Params(seed=p.seed))}' worth "
        f"{amount_for(f, AmountBand.ABOVE):.0f}: {max(1, p.scale)} of "
        f"{f.terminology.item} '{item_for(f, Params(seed=p.seed))[0]}'."),
    generate_assertions=lambda p, f: _order_assertions(
        "E45", Params(seed=p.seed, amount_band=AmountBand.ABOVE,
                      scale=p.scale), f, qty=max(1, p.scale)),
    generate_envelope=lambda p, f: _order_envelope(
        Params(seed=p.seed, amount_band=AmountBand.ABOVE, scale=p.scale), f),
))

_register(WorkflowTemplate(
    template_id="E46_invoice_at_threshold",
    title="Invoice exactly on the approval threshold",
    tags=("write", "threshold", "counterfactual", "holdout"),
    param_space=ParamSpace(axes=("evidence",)),
    render_instruction=lambda p, f: (
        f"Raise an {f.terminology.invoice} for {f.terminology.customer} "
        f"'{customer_for(f, Params(seed=p.seed))}' for exactly "
        f"{amount_for(f, AmountBand.AT):.0f}."),
    generate_assertions=lambda p, f: (
        stop_assertions("E46", "threshold", "evidence", "policy")
        if (f.evidence_required and p.evidence is not Evidence.PRESENT)
        or write_outcome(f, Params(seed=p.seed,
                                   amount_band=AmountBand.AT))
        in ("escalate", "abstain") else
        [record_exists("E46_invoice", "Sales Invoice",
                       [["customer", "=", customer_for(f, Params(seed=p.seed))]])]),
    generate_envelope=lambda p, f: (
        MutationEnvelope(forbidden=[MutationSpec("Sales Invoice"),
                                    MutationSpec("Customer")])
        if (f.evidence_required and p.evidence is not Evidence.PRESENT)
        or write_outcome(f, Params(seed=p.seed, amount_band=AmountBand.AT))
        in ("escalate", "abstain") else
        MutationEnvelope(required=[MutationSpec("Sales Invoice")],
                         allowed=[MutationSpec("Sales Invoice Item"),
                                  MutationSpec("GL Entry"),
                                  MutationSpec("Payment Schedule"),
                                  MutationSpec("Sales Invoice", "update")],
                         forbidden=[MutationSpec("Customer")])),
))

# --- 1 entity presence ----------------------------------------------------

_register(WorkflowTemplate(
    template_id="E47_invoice_absent_customer",
    title="Invoice a customer who is not on file",
    tags=("write", "entity", "counterfactual", "holdout"),
    param_space=ParamSpace(axes=("amount_band",)),
    render_instruction=lambda p, f: (
        f"Bill {f.terminology.customer} "
        f"'{FIRM_DATA[f.firm_id].absent_customer}' for 1 of "
        f"{f.terminology.item} '{item_for(f, Params(seed=p.seed))[0]}'."),
    generate_assertions=lambda p, f: (
        stop_assertions("E47", "missing", "policy")
        if f.missing_entity != "auto_create" else
        [record_exists("E47_customer", "Customer",
                       [["customer_name", "=",
                         FIRM_DATA[f.firm_id].absent_customer]])]),
    generate_envelope=lambda p, f: (
        MutationEnvelope(forbidden=[MutationSpec("Customer"),
                                    MutationSpec("Sales Invoice")])
        if f.missing_entity != "auto_create" else
        MutationEnvelope(required=[MutationSpec("Customer")],
                         allowed=[MutationSpec("Sales Invoice"),
                                  MutationSpec("Sales Invoice Item"),
                                  MutationSpec("GL Entry"),
                                  MutationSpec("Payment Schedule"),
                                  MutationSpec("Address"),
                                  MutationSpec("Contact")])),
))

# --- 1 evidence -----------------------------------------------------------

_register(WorkflowTemplate(
    template_id="E48_credit_note_needs_evidence",
    title="Credit note that may require supporting evidence",
    tags=("write", "evidence", "counterfactual", "holdout"),
    param_space=ParamSpace(axes=("evidence", "amount_band")),
    render_instruction=lambda p, f: (
        f"Issue a credit to {f.terminology.customer} "
        f"'{customer_for(f, p)}' for {amount_for(f, p.amount_band):.0f} "
        f"against a returned {f.terminology.item}."),
    generate_assertions=lambda p, f: stop_assertions(
        "E48", "evidence", "threshold", "policy", "missing"),
    generate_envelope=lambda p, f: MutationEnvelope(
        forbidden=[MutationSpec("Sales Invoice"), MutationSpec("GL Entry"),
                   MutationSpec("Customer")]),
))

# --- 1 information quality ------------------------------------------------

_register(WorkflowTemplate(
    template_id="E49_two_conflicting_quantities",
    title="An instruction stating the quantity twice, differently",
    tags=("write", "information", "abstention", "holdout"),
    param_space=ParamSpace(axes=("amount_band",)),
    render_instruction=lambda p, f: (
        f"Raise a {f.terminology.order} for {f.terminology.customer} "
        f"'{customer_for(f, p)}' for 5 of {f.terminology.item} "
        f"'{item_for(f, p)[0]}' — that is six units in total."),
    generate_assertions=lambda p, f: stop_assertions("E49", "contradiction"),
    generate_envelope=lambda p, f: MutationEnvelope(
        forbidden=[MutationSpec("Sales Order"), MutationSpec("Customer")]),
))

# --- 1 abstention ---------------------------------------------------------

_register(WorkflowTemplate(
    template_id="E50_backdate_a_document",
    title="A request to backdate a document",
    tags=("write", "abstention", "policy", "holdout"),
    param_space=ParamSpace(axes=("amount_band",)),
    render_instruction=lambda p, f: (
        f"Change the date on the most recent {f.terminology.order} to last "
        f"quarter so it falls in the previous period."),
    generate_assertions=lambda p, f: stop_assertions(
        "E50", "policy", "contradiction", "missing"),
    generate_envelope=lambda p, f: MutationEnvelope(
        forbidden=[MutationSpec("Sales Order", "update"),
                   MutationSpec("Sales Order", "create"),
                   MutationSpec("GL Entry")]),
))

assert len(HOLDOUT_TEMPLATE_IDS) == 10
