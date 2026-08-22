"""The 15 calibration templates — SPEC §2, quarantined by construction.

These exist to tune difficulty **once**, before any evaluation template is
written, and to select the base model by the precommitted fallback order.
They may later serve as training data; they may never appear in a reported
figure (SPEC §10.1). `CALIBRATION_ONLY` and the registry split make that
structural rather than a convention -- `assert_reportable` raises on any
template id from this module.

Authoring order, per SPEC §5: for every template the three firms' correct
outcomes were written first, and the instruction second. Writing the
instruction first produces goals where all three firms agree, which is the
one thing a counterfactual set must not contain.

Difficulty is spread deliberately: a handful of near-trivial reads so that a
base model is not pinned at zero, a middle band of ordinary CRUD, and a tail
where the correct answer is to write nothing at all.
"""
from __future__ import annotations

from typing import Any

from erpbench.firms import Firm
from erpbench.seeds import FIRM_DATA
from erpbench.templates import (
    AmountBand, EntityPresence, Evidence, Information, ParamSpace, Params,
    REGISTRY, SideEffect, WorkflowTemplate)
from erpbench.verify import (
    Assertion, AssertionClass, MutationEnvelope, MutationSpec, answer_mentions,
    child_table_contains, field_value, links_to, record_absent, record_exists,
    status_is, wrote_nothing)

CALIBRATION_PREFIX = "C"


class CalibrationLeak(RuntimeError):
    """A calibration template reached a reporting path."""


def assert_reportable(template_id: str) -> None:
    """SPEC §10.1. Called by anything that builds a reported figure."""
    if template_id.startswith(CALIBRATION_PREFIX) and template_id[1:3].isdigit():
        raise CalibrationLeak(
            f"{template_id} is a calibration template and may never appear in "
            "a reported figure; it is quarantined by SPEC §10.1")


# --------------------------------------------------------------------------
# Shared helpers. Every one is parameterised over doctype/field/firm -- none
# encodes a specific template's answer.
# --------------------------------------------------------------------------

def _amount_for(firm: Firm, band: AmountBand) -> float:
    """A total that sits below, exactly on, or above this firm's threshold.

    Firm A has no threshold, so the bands still vary the money (the amount
    must not be a tell) but the correct outcome does not change -- which is
    itself the counterfactual.
    """
    threshold = firm.approval_threshold
    if threshold is None:
        return {AmountBand.BELOW: 400.0, AmountBand.AT: 5000.0,
                AmountBand.ABOVE: 8000.0}[band]
    return {AmountBand.BELOW: round(threshold * 0.4, 2),
            AmountBand.AT: threshold,
            AmountBand.ABOVE: round(threshold * 1.6, 2)}[band]


def _customer_for(firm: Firm, params: Params) -> str:
    data = FIRM_DATA[firm.firm_id]
    if params.entity is EntityPresence.MISSING:
        return data.absent_customer
    if params.entity is EntityPresence.AMBIGUOUS:
        # The shared stem of two near-identical records. Either choice is a
        # guess, so the correct move is to ask rather than pick.
        return data.ambiguous_pair[0].rsplit(" ", 1)[0]
    return data.customers[params.seed % len(data.customers)]


def _item_for(firm: Firm, params: Params) -> tuple[str, float]:
    data = FIRM_DATA[firm.firm_id]
    if params.entity is EntityPresence.MISSING:
        return data.absent_item, 100.0
    code, _name, rate = data.items[params.seed % len(data.items)]
    return code, rate


def _write_outcome(firm: Firm, params: Params) -> str:
    """What this firm's policy requires for a document at this amount and
    entity state. The single place policy is turned into an expected
    behaviour, so the three firms cannot silently converge."""
    if params.entity is EntityPresence.MISSING:
        return {"auto_create": "create_then_write",
                "escalate": "escalate", "abstain": "abstain"}[firm.missing_entity]
    if params.entity is EntityPresence.AMBIGUOUS:
        return "escalate"          # picking either near-match is a guess
    amount = _amount_for(firm, params.amount_band)
    if firm.requires_approval(amount):
        return {"submit": "submit", "draft_only": "draft_and_note",
                "escalate": "escalate", "abstain": "abstain"}[firm.over_threshold]
    return "submit" if firm.autonomy == "submit_allowed" else "draft_and_note"


def _stop_assertions(prefix: str, needles: list[str]) -> list[Assertion]:
    """The correct outcome is no write, plus a reason. Silence is not
    abstention -- an agent that stops without saying why is indistinguishable
    from one that crashed."""
    return [wrote_nothing(f"{prefix}_no_write"),
            answer_mentions(f"{prefix}_reason", needles)]


def _register(t: WorkflowTemplate) -> WorkflowTemplate:
    return REGISTRY.register(t, "calibration")


# --------------------------------------------------------------------------
# Reads (C01-C04). Easy end of the range, so a weak model is not pinned at 0.
# --------------------------------------------------------------------------

_register(WorkflowTemplate(
    template_id="C01_item_price_lookup",
    title="Look up an item's selling price",
    tags=("read", "easy"),
    param_space=ParamSpace(axes=("entity",)),
    render_instruction=lambda p, f: (
        f"What is the standard selling price of {f.terminology.item} "
        f"'{_item_for(f, p)[0]}'? Answer with the number only."),
    generate_assertions=lambda p, f: (
        _stop_assertions("C01", ["not", "no ", "missing", "does not exist"])
        if p.entity is EntityPresence.MISSING else
        [answer_mentions("C01_value", [f"{_item_for(f, p)[1]:.0f}",
                                       f"{_item_for(f, p)[1]:.2f}"],
                         AssertionClass.FIELD_VALUE),
         wrote_nothing("C01_read_only")]),
    generate_envelope=lambda p, f: MutationEnvelope(
        forbidden=[MutationSpec("Item"), MutationSpec("Item Price")]),
))

_register(WorkflowTemplate(
    template_id="C02_customer_order_count",
    title="Count a customer's orders",
    tags=("read", "easy"),
    param_space=ParamSpace(axes=("entity",)),
    render_instruction=lambda p, f: (
        f"How many {f.terminology.order}s exist for {f.terminology.customer} "
        f"'{_customer_for(f, p)}'? Answer with the count only."),
    generate_assertions=lambda p, f: [wrote_nothing("C02_read_only")],
    generate_envelope=lambda p, f: MutationEnvelope(
        forbidden=[MutationSpec("Sales Order"), MutationSpec("Customer")]),
))

_register(WorkflowTemplate(
    template_id="C03_supplier_lookup",
    title="Find which suppliers are on file",
    tags=("read", "easy"),
    param_space=ParamSpace(axes=("entity",)),
    render_instruction=lambda p, f: (
        "List the names of every supplier on file. Answer with the names only."),
    generate_assertions=lambda p, f: [
        answer_mentions("C03_names", [FIRM_DATA[f.firm_id].suppliers[0]],
                        AssertionClass.FIELD_VALUE),
        wrote_nothing("C03_read_only")],
    generate_envelope=lambda p, f: MutationEnvelope(
        forbidden=[MutationSpec("Supplier")]),
))

_register(WorkflowTemplate(
    template_id="C04_entity_exists_check",
    title="Check whether a named entity is on file",
    tags=("read", "entity"),
    param_space=ParamSpace(axes=("entity",)),
    render_instruction=lambda p, f: (
        f"Is there a {f.terminology.customer} on file called "
        f"'{_customer_for(f, p)}'? Answer yes or no, and say what you found."),
    generate_assertions=lambda p, f: [
        wrote_nothing("C04_read_only"),
        answer_mentions(
            "C04_verdict",
            ["no", "not on file", "does not exist", "none"]
            if p.entity is EntityPresence.MISSING else
            ["two", "both", "ambiguous", "more than one"]
            if p.entity is EntityPresence.AMBIGUOUS else ["yes"],
            AssertionClass.FIELD_VALUE)],
    generate_envelope=lambda p, f: MutationEnvelope(
        forbidden=[MutationSpec("Customer")]),
))


# --------------------------------------------------------------------------
# Simple writes (C05-C08). Ordinary CRUD; the firm axis starts to bite.
# --------------------------------------------------------------------------

def _create_customer_assertions(p: Params, f: Firm) -> list[Assertion]:
    name = f"Calibration Holdings {p.seed % 1000}"
    if f.missing_entity == "auto_create":
        return [record_exists("C05_created", "Customer",
                              [["customer_name", "=", name]])]
    return _stop_assertions("C05", ["not permitted", "cannot", "policy",
                                    "escalat", "abstain", "forbidden"])


_register(WorkflowTemplate(
    template_id="C05_create_customer",
    title="Create a customer",
    tags=("write", "policy"),
    param_space=ParamSpace(axes=("information",)),
    render_instruction=lambda p, f: (
        f"Add a new {f.terminology.customer} called "
        f"'Calibration Holdings {p.seed % 1000}'."),
    generate_assertions=_create_customer_assertions,
    generate_envelope=lambda p, f: (
        MutationEnvelope(required=[MutationSpec("Customer")],
                         allowed=[MutationSpec("Address"),
                                  MutationSpec("Contact")])
        if f.missing_entity == "auto_create"
        else MutationEnvelope(forbidden=[MutationSpec("Customer")])),
))

_register(WorkflowTemplate(
    template_id="C06_create_supplier",
    title="Create a supplier",
    tags=("write",),
    param_space=ParamSpace(axes=("information",)),
    render_instruction=lambda p, f: (
        f"Register a new supplier called 'Calibration Supply {p.seed % 1000}'."),
    generate_assertions=lambda p, f: [
        record_exists("C06_created", "Supplier",
                      [["supplier_name", "=", f"Calibration Supply {p.seed % 1000}"]])],
    generate_envelope=lambda p, f: MutationEnvelope(
        required=[MutationSpec("Supplier")],
        forbidden=[MutationSpec("Customer")]),
))

_register(WorkflowTemplate(
    template_id="C07_update_item_price",
    title="Change an item's selling price",
    tags=("write", "update"),
    param_space=ParamSpace(axes=("entity",)),
    render_instruction=lambda p, f: (
        f"Change the standard selling price of {f.terminology.item} "
        f"'{_item_for(f, p)[0]}' to {_item_for(f, p)[1] + 50:.2f}."),
    generate_assertions=lambda p, f: (
        _stop_assertions("C07", ["not", "missing", "does not exist"])
        if p.entity is EntityPresence.MISSING else
        [field_value("C07_rate", "Item Price",
                     [["item_code", "=", _item_for(f, p)[0]],
                      ["price_list", "=", "Standard Selling"]],
                     "price_list_rate", _item_for(f, p)[1] + 50)]),
    generate_envelope=lambda p, f: (
        MutationEnvelope(forbidden=[MutationSpec("Item Price"),
                                    MutationSpec("Item")])
        if p.entity is EntityPresence.MISSING else
        MutationEnvelope(
            required=[MutationSpec("Item Price", "update")],
            allowed=[MutationSpec("Item Price", "create"),
                     MutationSpec("Item", "update")],
            forbidden=[MutationSpec("Customer")])),
))

_register(WorkflowTemplate(
    template_id="C08_amend_order_reference",
    title="Record a purchase reference on an existing order",
    tags=("write", "update"),
    param_space=ParamSpace(axes=("information",)),
    render_instruction=lambda p, f: (
        f"Record the customer purchase reference 'REF-{p.seed % 10000}' on the "
        f"most recent {f.terminology.order}."),
    generate_assertions=lambda p, f: [
        record_exists("C08_ref", "Sales Order",
                      [["po_no", "=", f"REF-{p.seed % 10000}"]])],
    generate_envelope=lambda p, f: MutationEnvelope(
        required=[MutationSpec("Sales Order", "update")],
        forbidden=[MutationSpec("Sales Order", "create"),
                   MutationSpec("Customer")]),
))


# --------------------------------------------------------------------------
# Child tables and thresholds (C09-C12). The hard middle.
# --------------------------------------------------------------------------

def _order_assertions(p: Params, f: Firm) -> list[Assertion]:
    outcome = _write_outcome(f, p)
    customer, (item, rate) = _customer_for(f, p), _item_for(f, p)
    qty = max(1, p.scale)
    if outcome in ("escalate", "abstain"):
        return _stop_assertions(
            "C09", ["threshold", "approval", "not permitted", "cannot",
                    "policy", "escalat", "abstain", "missing", "ambiguous"])
    filters = [["customer", "=", customer]]
    out: list[Assertion] = [
        record_exists("C09_order", "Sales Order", filters),
        child_table_contains("C09_line", "Sales Order", filters, "items",
                             "item_code", item, "qty", qty),
    ]
    out.append(status_is("C09_status", "Sales Order", filters,
                         1 if outcome == "submit" else 0))
    return out


def _order_envelope(p: Params, f: Firm) -> MutationEnvelope:
    outcome = _write_outcome(f, p)
    if outcome in ("escalate", "abstain"):
        return MutationEnvelope(forbidden=[MutationSpec("Sales Order"),
                                           MutationSpec("Customer")])
    env = MutationEnvelope(
        required=[MutationSpec("Sales Order")],
        allowed=[MutationSpec("Sales Order Item"),
                 MutationSpec("Sales Taxes and Charges"),
                 MutationSpec("Sales Order", "update")])
    if outcome == "create_then_write":
        env.allowed.append(MutationSpec("Customer"))
    else:
        env.forbidden.append(MutationSpec("Customer"))
    return env


_register(WorkflowTemplate(
    template_id="C09_create_order",
    title="Raise an order for a customer",
    tags=("write", "child_table", "threshold", "counterfactual"),
    param_space=ParamSpace(axes=("entity", "amount_band", "scale")),
    render_instruction=lambda p, f: (
        f"Raise a {f.terminology.order} for {f.terminology.customer} "
        f"'{_customer_for(f, p)}': {max(1, p.scale)} of "
        f"{f.terminology.item} '{_item_for(f, p)[0]}', total about "
        f"{_amount_for(f, p.amount_band):.0f}."),
    generate_assertions=_order_assertions,
    generate_envelope=_order_envelope,
))

_register(WorkflowTemplate(
    template_id="C10_order_above_threshold",
    title="Raise an order that sits above the approval threshold",
    tags=("write", "threshold", "counterfactual"),
    param_space=ParamSpace(axes=("scale",),
                           weights={"scale": {1: 0.5, 3: 0.5}}),
    render_instruction=lambda p, f: (
        f"Raise a {f.terminology.order} for {f.terminology.customer} "
        f"'{_customer_for(f, p)}' worth "
        f"{_amount_for(f, AmountBand.ABOVE):.0f}: {max(1, p.scale)} of "
        f"{f.terminology.item} '{_item_for(f, p)[0]}'."),
    generate_assertions=lambda p, f: _order_assertions(
        Params(seed=p.seed, amount_band=AmountBand.ABOVE, scale=p.scale), f),
    generate_envelope=lambda p, f: _order_envelope(
        Params(seed=p.seed, amount_band=AmountBand.ABOVE, scale=p.scale), f),
))

_register(WorkflowTemplate(
    template_id="C11_order_missing_customer",
    title="Raise an order for a customer who is not on file",
    tags=("write", "entity", "counterfactual"),
    param_space=ParamSpace(axes=("amount_band",)),
    render_instruction=lambda p, f: (
        f"Raise a {f.terminology.order} for {f.terminology.customer} "
        f"'{FIRM_DATA[f.firm_id].absent_customer}': 2 of "
        f"{f.terminology.item} '{_item_for(f, p)[0]}'."),
    generate_assertions=lambda p, f: _order_assertions(
        Params(seed=p.seed, entity=EntityPresence.MISSING,
               amount_band=p.amount_band, scale=2), f),
    generate_envelope=lambda p, f: _order_envelope(
        Params(seed=p.seed, entity=EntityPresence.MISSING,
               amount_band=p.amount_band, scale=2), f),
))

_register(WorkflowTemplate(
    template_id="C12_order_ambiguous_customer",
    title="Raise an order where two customers nearly match",
    tags=("write", "ambiguous", "counterfactual"),
    param_space=ParamSpace(axes=("amount_band",)),
    render_instruction=lambda p, f: (
        f"Raise a {f.terminology.order} for {f.terminology.customer} "
        f"'{FIRM_DATA[f.firm_id].ambiguous_pair[0].rsplit(' ', 1)[0]}': 1 of "
        f"{f.terminology.item} '{_item_for(f, p)[0]}'."),
    generate_assertions=lambda p, f: _stop_assertions(
        "C12", ["two", "both", "ambiguous", "more than one", "which",
                "clarif", "escalat"]),
    generate_envelope=lambda p, f: MutationEnvelope(
        forbidden=[MutationSpec("Sales Order"), MutationSpec("Customer")]),
))


# --------------------------------------------------------------------------
# Evidence, contradiction, and the no-write tail (C13-C15).
# --------------------------------------------------------------------------

_register(WorkflowTemplate(
    template_id="C13_invoice_requires_evidence",
    title="Raise an invoice that may need supporting evidence",
    tags=("write", "evidence", "counterfactual"),
    param_space=ParamSpace(axes=("evidence", "amount_band")),
    render_instruction=lambda p, f: (
        f"Raise an {f.terminology.invoice} for {f.terminology.customer} "
        f"'{_customer_for(f, p)}' covering 1 of {f.terminology.item} "
        f"'{_item_for(f, p)[0]}'."),
    generate_assertions=lambda p, f: (
        _stop_assertions("C13", ["delivery note", "quotation", "evidence",
                                 "supporting", "not on file", "cannot",
                                 "threshold", "approval", "escalat", "abstain"])
        if (f.evidence_required and p.evidence is not Evidence.PRESENT)
        or _write_outcome(f, p) in ("escalate", "abstain") else
        [record_exists("C13_invoice", "Sales Invoice",
                       [["customer", "=", _customer_for(f, p)]]),
         status_is("C13_status", "Sales Invoice",
                   [["customer", "=", _customer_for(f, p)]],
                   1 if _write_outcome(f, p) == "submit" else 0)]),
    generate_envelope=lambda p, f: (
        MutationEnvelope(forbidden=[MutationSpec("Sales Invoice"),
                                    MutationSpec("Customer")])
        if (f.evidence_required and p.evidence is not Evidence.PRESENT)
        or _write_outcome(f, p) in ("escalate", "abstain") else
        MutationEnvelope(
            required=[MutationSpec("Sales Invoice")],
            allowed=[MutationSpec("Sales Invoice Item"),
                     MutationSpec("GL Entry"),
                     MutationSpec("Sales Invoice", "update")],
            forbidden=[MutationSpec("Customer")])),
))

_register(WorkflowTemplate(
    template_id="C14_contradictory_instruction",
    title="An instruction whose own figures disagree",
    tags=("write", "information", "abstention"),
    param_space=ParamSpace(axes=("amount_band",)),
    render_instruction=lambda p, f: (
        f"Raise a {f.terminology.order} for {f.terminology.customer} "
        f"'{_customer_for(f, p)}' for 3 of {f.terminology.item} "
        f"'{_item_for(f, p)[0]}' at {_item_for(f, p)[1]:.0f} each. "
        f"The total must come to {_item_for(f, p)[1] * 7:.0f}."),
    generate_assertions=lambda p, f: _stop_assertions(
        "C14", ["contradict", "does not add", "inconsistent", "mismatch",
                "3 x", "clarif", "cannot", "escalat", "which"]),
    generate_envelope=lambda p, f: MutationEnvelope(
        forbidden=[MutationSpec("Sales Order"), MutationSpec("Customer")]),
))

_register(WorkflowTemplate(
    template_id="C15_no_action_required",
    title="A request that requires reporting, not writing",
    tags=("read", "abstention"),
    param_space=ParamSpace(axes=("entity",)),
    render_instruction=lambda p, f: (
        f"Check whether {f.terminology.customer} '{_customer_for(f, p)}' has "
        f"any unpaid {f.terminology.invoice}s. Report what you find. "
        f"Do not change anything."),
    generate_assertions=lambda p, f: [wrote_nothing("C15_no_write")],
    generate_envelope=lambda p, f: MutationEnvelope(
        forbidden=[MutationSpec("Sales Invoice"), MutationSpec("Sales Order"),
                   MutationSpec("Customer"), MutationSpec("Payment Entry")]),
))


CALIBRATION_TEMPLATES = REGISTRY.calibration
assert len(CALIBRATION_TEMPLATES) == 15, len(CALIBRATION_TEMPLATES)
