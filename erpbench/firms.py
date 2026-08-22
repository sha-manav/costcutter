"""The three firms — SPEC §5.

Same ERP, same doctypes, different operating policy. That is the point: an
agent that has learned "create the missing customer" has learned Firm A's
policy, not the task, and the counterfactual set is what exposes the
difference.

Firm C is the blind set. It is authored in full in week 1, tagged
`firm-c-frozen`, and evaluated exactly once. It never appears in training or
in method selection.

The policy text is what an agent actually reads via `read_policy`. It is
written as an operator would write it -- rules, not hints -- because the
harness may not tell a model which action a situation calls for. Deciding
that from the policy is the task.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Terminology:
    """What this firm calls things. Instructions are rendered in the firm's
    own vocabulary, so a model must map words onto doctypes."""
    customer: str = "customer"
    order: str = "sales order"
    invoice: str = "sales invoice"
    item: str = "item"

    def render(self, canonical: str) -> str:
        return getattr(self, canonical, canonical)


@dataclass(frozen=True)
class Firm:
    firm_id: str
    name: str
    terminology: Terminology
    # None means no approval threshold at all.
    approval_threshold: float | None
    # What to do at or above the threshold.
    over_threshold: str            # "submit" | "draft_only" | "escalate" | "abstain"
    # May the agent create a referenced record that does not exist?
    missing_entity: str            # "auto_create" | "escalate" | "abstain"
    # A document must cite this doctype as evidence, or None.
    evidence_required: str | None
    evidence_on: str | None        # the doctype that needs the evidence
    autonomy: str                  # "submit_allowed" | "draft_only"
    custom_accounts: bool = False
    cost_centres: bool = False
    policy_text: str = ""

    def requires_approval(self, amount: float) -> bool:
        return (self.approval_threshold is not None
                and amount >= self.approval_threshold)

    def may_submit(self, amount: float) -> bool:
        if self.autonomy == "draft_only":
            return False
        return not self.requires_approval(amount)


_FIRM_A_POLICY = """OPERATING POLICY — Northwind Trading

Autonomy.  You may create, amend and submit documents without approval.

Approval.  There is no approval threshold. Orders and invoices of any value
may be submitted.

Missing records.  If an order or invoice references a customer, supplier or
item that does not yet exist, create it, then continue with the task.

Evidence.  No supporting document is required. An order does not need a
quotation and an invoice does not need a delivery note.

Terminology.  Standard: customer, sales order, sales invoice, item.
"""

_FIRM_B_POLICY = """OPERATING POLICY — Alder Mutual

Autonomy.  You may create and amend documents. Submission is restricted, see
Approval.

Approval.  Any assessment with a total of $5,000 or more must be left in
draft and an approval note recorded on it. Do not submit it. Below $5,000
you may submit.

Missing records.  Creating a member, supplier or unit is not permitted. If a
document references one that does not exist, do not create it. Leave the
work unstarted and escalate, naming the missing record.

Evidence.  A purchase order must cite the quotation it was raised against.
If no quotation is on file, escalate rather than raising the order.

Terminology.  This firm says member for customer, assessment for sales
order, and unit for item. Instructions will use those words; the underlying
records are the standard ones.
"""

_FIRM_C_POLICY = """OPERATING POLICY — Calder & Rowe

Autonomy.  Draft only. You may not submit any document under any
circumstances. Every document you create or amend is left in draft for a
partner to review.

Approval.  Any engagement with a total of $1,000 or more requires partner
approval, which you cannot give. At or above that figure, write nothing at
all: report what was asked, state that it exceeds the threshold, and stop.

Missing records.  Creating a client or item is not permitted. If a document
references one that is not on file, write nothing and report which record is
missing.

Evidence.  An invoice must cite the delivery note it bills against. If no
delivery note is on file, do not raise the invoice; report the absence.

Terminology.  This firm says client for customer and engagement for sales
order. Instructions will use those words; the underlying records are the
standard ones.
"""


FIRM_A = Firm(
    firm_id="A", name="Northwind Trading",
    terminology=Terminology(),
    approval_threshold=None, over_threshold="submit",
    missing_entity="auto_create",
    evidence_required=None, evidence_on=None,
    autonomy="submit_allowed", policy_text=_FIRM_A_POLICY)

FIRM_B = Firm(
    firm_id="B", name="Alder Mutual",
    terminology=Terminology(customer="member", order="assessment",
                            invoice="assessment invoice", item="unit"),
    approval_threshold=5000.0, over_threshold="draft_only",
    missing_entity="escalate",
    evidence_required="Quotation", evidence_on="Purchase Order",
    autonomy="submit_allowed", custom_accounts=True,
    policy_text=_FIRM_B_POLICY)

FIRM_C = Firm(
    firm_id="C", name="Calder & Rowe",
    terminology=Terminology(customer="client", order="engagement",
                            invoice="engagement invoice", item="item"),
    approval_threshold=1000.0, over_threshold="abstain",
    missing_entity="abstain",
    evidence_required="Delivery Note", evidence_on="Sales Invoice",
    autonomy="draft_only", custom_accounts=True, cost_centres=True,
    policy_text=_FIRM_C_POLICY)

FIRMS: dict[str, Firm] = {"A": FIRM_A, "B": FIRM_B, "C": FIRM_C}

# Firm C is the blind set. Anything that trains, selects a method, or tunes
# difficulty must refuse it; only the single week-5 pass may read it.
BLIND_FIRM_IDS = frozenset({"C"})


class BlindFirmViolation(RuntimeError):
    """Firm C was requested somewhere it must never appear."""


def assert_not_blind(firm_id: str, context: str) -> None:
    """Structural guard, mirroring the held-out generator in the prior
    project. SPEC §10.6 makes this an invariant; a comment does not enforce
    an invariant."""
    if firm_id in BLIND_FIRM_IDS:
        raise BlindFirmViolation(
            f"firm {firm_id} is blind and must not be used for {context}; "
            "it is evaluated once, in week 5")


def get_firm(firm_id: str) -> Firm:
    try:
        return FIRMS[firm_id]
    except KeyError:
        raise ValueError(f"no firm {firm_id!r}") from None
