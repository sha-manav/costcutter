"""Helpers shared by every template module — SPEC §4 and §5.

These were written for the calibration split and are general by construction:
each is parameterised over the firm and the drawn parameters, and none
encodes a particular template's answer. That is the same rule the corrected
harness lives under, and it matters more here — a helper that quietly favours
one outcome would bias every template that used it.

`_write_outcome` is the single place a firm's policy becomes an expected
behaviour. Keeping it in one function is what stops the three firms silently
converging as templates are added: if two firms ever produce the same outcome
for the same parameters, it is visible here rather than spread across forty
files.
"""
from __future__ import annotations

from typing import Any

from erpbench.firms import Firm
from erpbench.seeds import FIRM_DATA
from erpbench.templates import AmountBand, EntityPresence, Params
from erpbench.verify import Assertion, answer_mentions, wrote_nothing


def amount_for(firm: Firm, band: AmountBand) -> float:
    """A total below, exactly on, or above this firm's threshold.

    Firm A has no threshold, so the bands still vary the money -- the amount
    must not itself be a tell -- but the correct outcome does not change,
    which is the counterfactual.
    """
    threshold = firm.approval_threshold
    if threshold is None:
        return {AmountBand.BELOW: 400.0, AmountBand.AT: 5000.0,
                AmountBand.ABOVE: 8000.0}[band]
    return {AmountBand.BELOW: round(threshold * 0.4, 2),
            AmountBand.AT: threshold,
            AmountBand.ABOVE: round(threshold * 1.6, 2)}[band]


def customer_for(firm: Firm, params: Params) -> str:
    data = FIRM_DATA[firm.firm_id]
    if params.entity is EntityPresence.MISSING:
        return data.absent_customer
    if params.entity is EntityPresence.AMBIGUOUS:
        # The shared stem of two near-identical records: either choice is a
        # guess, so the correct move is to ask rather than pick.
        return data.ambiguous_pair[0].rsplit(" ", 1)[0]
    return data.customers[params.seed % len(data.customers)]


def item_for(firm: Firm, params: Params) -> tuple[str, float]:
    data = FIRM_DATA[firm.firm_id]
    if params.entity is EntityPresence.MISSING:
        return data.absent_item, 100.0
    code, _name, rate = data.items[params.seed % len(data.items)]
    return code, rate


def supplier_for(firm: Firm, params: Params) -> str:
    data = FIRM_DATA[firm.firm_id]
    return data.suppliers[params.seed % len(data.suppliers)]


def write_outcome(firm: Firm, params: Params) -> str:
    """What this firm's policy requires at this amount and entity state.

    The single place policy becomes expected behaviour, so the three firms
    cannot silently converge as templates are added.
    """
    if params.entity is EntityPresence.MISSING:
        return {"auto_create": "create_then_write",
                "escalate": "escalate", "abstain": "abstain"}[firm.missing_entity]
    if params.entity is EntityPresence.AMBIGUOUS:
        return "escalate"          # picking either near-match is a guess
    amount = amount_for(firm, params.amount_band)
    if firm.requires_approval(amount):
        return {"submit": "submit", "draft_only": "draft_and_note",
                "escalate": "escalate", "abstain": "abstain"}[firm.over_threshold]
    return "submit" if firm.autonomy == "submit_allowed" else "draft_and_note"


# Phrasings that count as having said why. Deliberately broad: the assertion
# is about the substance being reported, not about wording, and a model
# marked wrong for being fluent is a measurement error.
STOP_REASONS: dict[str, list[str]] = {
    "missing": ["not on file", "does not exist", "no such", "missing",
                "not found", "cannot find"],
    "ambiguous": ["two", "both", "ambiguous", "more than one", "which",
                  "clarif", "multiple"],
    "threshold": ["threshold", "approval", "approve", "exceeds", "over the",
                  "above the", "escalat", "partner"],
    "policy": ["not permitted", "cannot", "policy", "forbidden", "not allowed",
               "escalat", "abstain", "decline"],
    "evidence": ["delivery note", "quotation", "evidence", "supporting",
                 "not on file", "cannot", "no ", "without"],
    "contradiction": ["contradict", "does not add", "inconsistent", "mismatch",
                      "clarif", "cannot", "which", "conflict"],
}


def stop_assertions(prefix: str, *kinds: str) -> list[Assertion]:
    """The correct outcome is no write, plus a reason.

    Silence is not abstention: an agent that stops without saying why is
    indistinguishable from one that crashed, so both halves are asserted.
    """
    needles: list[str] = []
    for kind in kinds:
        needles += STOP_REASONS[kind]
    return [wrote_nothing(f"{prefix}_no_write"),
            answer_mentions(f"{prefix}_reason", needles)]
