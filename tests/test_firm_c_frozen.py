"""Firm C is frozen. SPEC §10.6: evaluated once, absent from training and
method selection.

A tag records intent; this records the content. If either the policy or the
seeded world drifts, the single blind pass in week 5 is measuring a
different firm than the one that was frozen -- and nothing else in the
pipeline would notice.
"""
from __future__ import annotations

import hashlib
import json

from erpbench.firms import FIRM_C
from erpbench.seeds import FIRM_SEEDS

# Recorded at the freeze. Changing this constant to make the test pass is
# the specific thing it exists to prevent.
FIRM_C_FINGERPRINT = "31b4c8a90ab9754a7b9f3fafd0aad8a3fd9a3415"


def _fingerprint() -> str:
    manifest = json.loads((FIRM_SEEDS / "firm_C.json").read_text())
    world = {k: manifest[k] for k in
             ("customers", "suppliers", "items", "ambiguous_pair",
              "absent_customer", "absent_item")}
    return hashlib.sha1(json.dumps({
        "policy": FIRM_C.policy_text, "world": world,
        "threshold": FIRM_C.approval_threshold, "over": FIRM_C.over_threshold,
        "missing": FIRM_C.missing_entity, "evidence": FIRM_C.evidence_required,
        "autonomy": FIRM_C.autonomy}, sort_keys=True).encode()).hexdigest()


def test_firm_c_has_not_drifted():
    assert _fingerprint() == FIRM_C_FINGERPRINT, (
        "Firm C changed after being frozen. The blind pass would measure a "
        "different firm than the one tagged firm-c-frozen. Revert the change "
        "rather than updating this constant.")


def test_firm_c_world_is_disjoint_from_the_others():
    """A name shared with a training firm lets recall substitute for policy."""
    sets = {}
    for fid in ("A", "B", "C"):
        m = json.loads((FIRM_SEEDS / f"firm_{fid}.json").read_text())
        sets[fid] = (set(m["customers"]) | set(m["suppliers"])
                     | {i[0] for i in m["items"]})
    assert not (sets["C"] & sets["A"]), sets["C"] & sets["A"]
    assert not (sets["C"] & sets["B"]), sets["C"] & sets["B"]


def test_firm_c_is_fully_specified():
    """Frozen means complete: a gap filled in later is drift."""
    m = json.loads((FIRM_SEEDS / "firm_C.json").read_text())
    assert len(m["customers"]) >= 6 and len(m["suppliers"]) >= 3
    assert len(m["items"]) >= 4 and len(m["ambiguous_pair"]) == 2
    assert m["absent_customer"] and m["absent_item"]
    assert m["created"].get("Cost Center") == 2, "C books to cost centres"
    assert FIRM_C.autonomy == "draft_only"
    assert FIRM_C.approval_threshold == 1000.0
    assert FIRM_C.over_threshold == "abstain"
    assert FIRM_C.missing_entity == "abstain"
    assert FIRM_C.evidence_required == "Delivery Note"
