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


def test_firm_c_seed_image_matches_its_manifest():
    """The database, not just the entity names.

    The content fingerprint above covers the policy and the entity set. It
    does not cover the SQL image, and FIRM_C_FROZEN.md claimed it did. The
    image was in fact rebuilt once -- 8,297,696 to 7,856,385 bytes -- when the
    project moved to a containerised ERPNext, and nothing failed. Semantics
    were unchanged and all three firms moved together, so it was a change of
    substrate; but it passed silently, which is the part worth fixing.
    """
    manifest = json.loads((FIRM_SEEDS / "firm_C.json").read_text())
    image = FIRM_SEEDS / "firm_C.sql"
    if not image.exists():          # not built on this machine
        return
    assert image.stat().st_size == manifest["seed_bytes"], (
        f"firm_C.sql is {image.stat().st_size} bytes but its manifest records "
        f"{manifest['seed_bytes']}. Rebuilding the seed is a change to the "
        "blind set: acknowledge it in FIRM_C_FROZEN.md rather than letting the "
        "manifest and the image drift apart.")


# The single blind pass, run 2026-08-27. Trained-checkpoint Firm C rows are
# permitted here and nowhere else; the test below is what makes "nowhere else"
# checkable rather than asserted.
BLIND_PASS_DIRS = ("firmc_0", "firmc_1", "firmc_3", "firmc_8")
# ...and the merged files the pool driver writes alongside them.
BLIND_PASS_FILES = tuple(f"{d}_run.jsonl" for d in BLIND_PASS_DIRS)


def test_no_trained_checkpoint_has_seen_firm_c_outside_the_blind_pass():
    """The property the transfer claim actually rests on.

    Firm C carries base-model rows on purpose -- transfer is measured against
    a baseline on C. What must never happen is a *trained* checkpoint touching
    it anywhere other than the one recorded blind pass. Before that pass ran
    this asserted zero; it now asserts zero outside `BLIND_PASS_DIRS`, which
    is the same guarantee with the one sanctioned exception named explicitly
    rather than the test deleted.
    """
    import glob
    import pathlib

    root = pathlib.Path(__file__).resolve().parent.parent / "artifacts"
    offenders = set()
    for path in glob.glob(str(root / "**" / "*.jsonl"), recursive=True):
        if "quarantine" in path:
            continue
        if any(f"/{d}/" in path for d in BLIND_PASS_DIRS):
            continue
        if pathlib.Path(path).name in BLIND_PASS_FILES:
            continue
        try:
            with open(path) as fh:
                for line in fh:
                    if not line.strip():
                        continue
                    row = json.loads(line)
                    if row.get("firm_id") == "C":
                        model = str(row.get("model") or "")
                        if "erpbench" in model:
                            offenders.add((model, path))
        except (ValueError, OSError):
            continue
    assert not offenders, (
        f"trained checkpoints have Firm C rows outside the blind pass: "
        f"{sorted(offenders)[:3]}")


def test_the_blind_pass_ran_once_on_one_checkpoint():
    """A second checkpoint evaluated on C would silently make it a tuned set."""
    import glob
    import pathlib

    root = pathlib.Path(__file__).resolve().parent.parent / "artifacts"
    models = set()
    paths = [q for d in BLIND_PASS_DIRS for q in glob.glob(str(root / d / "*.jsonl"))]
    for path in paths:
        if True:
            with open(path) as fh:
                for line in fh:
                    if line.strip():
                        row = json.loads(line)
                        if row.get("firm_id") == "C":
                            models.add(str(row.get("model")))
    if not models:                      # blind pass not run on this machine
        return
    assert len(models) == 1, (
        f"Firm C has been evaluated with more than one checkpoint {models}; "
        "the blind pass is single by construction and a second run makes it "
        "a selection set")
