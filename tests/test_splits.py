"""Holdout splits — SPEC §4, §10.6, §10.10.

Frozen before any training data exists. A split chosen after trajectories are
generated can be picked, consciously or not, to flatter the result, and
nothing in the numbers would reveal it.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import erpbench.calibration            # noqa: F401
import erpbench.evaluation             # noqa: F401
from erpbench import splits
from erpbench.firms import FIRMS
from erpbench.templates import REGISTRY, seeds_for

FROZEN = Path(__file__).resolve().parent.parent / "artifacts" / "splits_frozen.json"


def test_the_frozen_split_matches_the_code():
    """If these diverge, some rows were bucketed under an assignment that no
    longer exists, and the holdout numbers describe nothing in particular."""
    assert FROZEN.exists(), "splits were never frozen; run python -m erpbench.splits"
    frozen = json.loads(FROZEN.read_text())
    assert frozen["fingerprint"] == splits.fingerprint(), (
        "the split assignment changed after freezing. If this was deliberate "
        "it invalidates every holdout number already reported; if not, revert "
        "it rather than re-freezing.")


def test_assignment_is_deterministic_and_not_hand_picked():
    assert splits.template_holdout() == splits.template_holdout()
    # Derived from a hash of the id, so no template can be moved by editing a
    # list -- only by changing the salt, which the fingerprint test catches.
    import inspect
    assert "sha256" in inspect.getsource(splits._score)


def test_firm_c_outranks_every_other_bucket():
    """SPEC §10.6: Firm C is evaluated once and must never be absorbed into
    another bucket, whatever the template or trial."""
    for t in REGISTRY.evaluation:
        for trial in (0, 1, 2):
            assert splits.split_of(t.template_id, "C", trial) == "firm_c"


def test_holdout_and_train_visible_are_disjoint_and_complete():
    m = splits.manifest()
    held, seen = set(m["template_holdout"]), set(m["train_visible"])
    assert not (held & seen)
    assert held | seen == {t.template_id for t in REGISTRY.evaluation}


def test_the_template_holdout_is_large_enough_to_report():
    """Widened after week 2. Ten templates were added specifically because
    13 gave intervals that all spanned zero at n~78 per arm — the bound is
    now a floor on power, not a cap on it."""
    held = splits.template_holdout()
    assert len(held) >= 13, f"only {len(held)} templates held out"


def _genuine_counterfactuals() -> set[str]:
    out = set()
    for t in REGISTRY.evaluation:
        seed = seeds_for(t.template_id, "A", 1)[0]
        d = {fid: ({s.doctype for s in t.instantiate(seed, f).envelope.required},
                   {s.doctype for s in t.instantiate(seed, f).envelope.forbidden})
             for fid, f in FIRMS.items()}
        for a in FIRMS:
            for b in FIRMS:
                if a < b and ((d[a][0] & d[b][1]) or (d[b][0] & d[a][1])):
                    out.add(t.template_id)
    return out


def test_the_holdout_is_counterfactual_rich():
    """The template-level holdout is the generalization number. If it were
    all CRUD, it would measure whether the model learned ERPNext rather than
    whether it learned to follow a firm's policy — which is the claim."""
    cf = _genuine_counterfactuals()
    held = set(splits.template_holdout())
    assert len(cf & held) >= 5, (
        f"only {len(cf & held)} counterfactuals held out of {len(held)}")
    assert len(cf & held) / len(held) >= len(cf - held) / (40 - len(held)), \
        "the holdout is less counterfactual-dense than the training split"


def test_instance_holdout_reserves_later_trials():
    assert splits.is_instance_holdout(2)
    assert not splits.is_instance_holdout(0)
    seen = REGISTRY.evaluation[0].template_id
    if seen not in set(splits.template_holdout()):
        assert splits.split_of(seen, "A", 0) == "train_visible"
        assert splits.split_of(seen, "A", 2) == "instance_holdout"


def test_every_row_carries_its_bucket():
    """Stamped at production time, not inferred afterwards from a split file
    that may since have moved."""
    import inspect

    from erpbench import gate

    src = inspect.getsource(gate.run_one)
    assert "split_bucket" in src and "split_fingerprint" in src


def test_the_added_holdout_templates_are_all_held_out():
    """E41-E50 exist only to add power to the template-level holdout. They
    are forced rather than hashed: there is no per-template choice, so none
    can be placed by how it performs. Ordering is what makes adding n
    legitimate — they were authored and assigned before being run."""
    import erpbench.evaluation_extra                       # noqa: F401

    held = set(splits.template_holdout())
    for n in range(41, 51):
        tid = next(t.template_id for t in REGISTRY.evaluation
                   if t.template_id.startswith(f"E{n}_"))
        assert tid in held, f"{tid} was added for the holdout and is not in it"


def test_adding_templates_did_not_reshuffle_the_original_assignment():
    """If the added templates had entered the hash pool they would have
    displaced some of the original 13, and week 2's holdout numbers would no
    longer describe the bucket they were measured on."""
    original_13 = {
        "E07_rename_customer_reference", "E15_invoice_with_lines",
        "E16_quotation_with_lines", "E19_order_above_threshold",
        "E22_submit_existing_draft", "E24_order_for_absent_item",
        "E28_invoice_needs_delivery_note", "E30_invoice_stale_evidence",
        "E31_delivery_note_for_order", "E36_report_unpaid_invoices",
        "E38_cancel_submitted_document", "E39_bulk_price_increase",
        "E40_payment_without_instruction"}
    assert original_13 <= set(splits.template_holdout()), \
        "the week-2 holdout assignment moved when templates were added"
