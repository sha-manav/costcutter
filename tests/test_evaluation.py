"""The 40 evaluation templates — SPEC §4, §5, §10.

These are the reported measurement. Everything here is a property that, if it
broke silently, would produce a full results file describing something other
than what the study claims to measure.
"""
from __future__ import annotations

import json

import pytest

import erpbench.calibration            # noqa: F401  (populates the registry)
import erpbench.evaluation             # noqa: F401
from erpbench.firms import FIRMS
from erpbench.templates import REGISTRY, seeds_for


def _instances(template, trials: int = 3):
    for fid, firm in FIRMS.items():
        for seed in seeds_for(template.template_id, fid, trials):
            yield fid, firm, template.instantiate(seed, firm)


def test_there_are_forty_evaluation_templates():
    assert len(REGISTRY.evaluation) == 40


def test_every_template_instantiates_for_every_firm():
    """A template that raises on one firm silently removes that firm from the
    comparison, and the row would be scored `error` and excluded rather than
    reported as a broken template."""
    for t in REGISTRY.evaluation:
        for fid, _firm, inst in _instances(t):
            assert inst.instruction.strip(), f"{t.template_id}/{fid}: no instruction"
            assert inst.assertions, f"{t.template_id}/{fid}: no assertions"


def test_evaluation_and_calibration_never_overlap():
    """SPEC §10.1. A calibration template appearing in the evaluation split
    would put quarantined difficulty-tuning data into a reported figure."""
    ev = {t.template_id for t in REGISTRY.evaluation}
    cal = {t.template_id for t in REGISTRY.calibration}
    assert not (ev & cal)


def test_evaluation_templates_are_reportable():
    """The mirror of the calibration quarantine: these must NOT raise."""
    from erpbench.calibration import assert_reportable

    for t in REGISTRY.evaluation:
        assert_reportable(t.template_id)


def _disposition(inst):
    return ({s.doctype for s in inst.envelope.required},
            {s.doctype for s in inst.envelope.forbidden})


def _genuine_counterfactuals():
    out = []
    for t in REGISTRY.evaluation:
        seed = seeds_for(t.template_id, "A", 1)[0]
        d = {fid: _disposition(t.instantiate(seed, f)) for fid, f in FIRMS.items()}
        for a in FIRMS:
            for b in FIRMS:
                if a < b and ((d[a][0] & d[b][1]) or (d[b][0] & d[a][1])):
                    out.append(t.template_id)
                    break
            else:
                continue
            break
    return out


def test_at_least_fifteen_genuine_counterfactuals():
    """SPEC §5 and §11: never below 15, and the de-scoping order may not cut
    into them.

    "Genuine" means a write that is *required* for one firm and *forbidden*
    for another — a real divergence in the correct outcome. Templates whose
    assertions differ only because the firms hold different customer names
    are not counterfactuals, and counting them would overstate the set.
    """
    genuine = _genuine_counterfactuals()
    assert len(genuine) >= 15, (
        f"only {len(genuine)} genuine counterfactuals: {genuine}")


def test_the_counterfactual_set_has_no_margin_and_that_is_recorded():
    """Currently exactly 15. Dropping any one of them breaches a floor SPEC
    §11 says may never be crossed, so a template cannot be quietly removed."""
    assert len(_genuine_counterfactuals()) == 15, (
        "the counterfactual count changed; if it fell, SPEC §11's floor is "
        "breached, and if it rose the margin note in evaluation.py is stale")


def test_write_nothing_cases_are_oversampled():
    """SPEC §4 asks for cases whose correct outcome is escalation or no write
    at all to be oversampled; uniform sampling spends the corpus on the easy
    corner."""
    stop = 0
    for t in REGISTRY.evaluation:
        seed = seeds_for(t.template_id, "C", 1)[0]
        inst = t.instantiate(seed, FIRMS["C"])
        if any(a.assertion_id.endswith(("_no_write", "_read_only"))
               for a in inst.assertions):
            stop += 1
    assert stop >= 15, f"only {stop}/40 no-write cases on the strictest firm"


def test_child_table_work_is_represented():
    tagged = [t for t in REGISTRY.evaluation if "child_table" in t.tags]
    assert len(tagged) >= 6, f"only {len(tagged)} child-table templates"


def test_no_template_names_a_firm_or_a_threshold_in_its_instruction():
    """Instructions are rendered in the firm's own vocabulary, but must never
    state the policy — deciding the outcome from the policy is the task."""
    banned = ("firm a", "firm b", "firm c", "northwind", "alder mutual",
              "calder & rowe", "threshold is", "policy says", "you may not")
    for t in REGISTRY.evaluation:
        for fid, _firm, inst in _instances(t, trials=1):
            low = inst.instruction.lower()
            hits = [b for b in banned if b in low]
            assert not hits, f"{t.template_id}/{fid} leaks {hits}"


def test_assertions_are_generated_before_any_model_runs():
    """SPEC §10.9: generated from parameters, never edited to rescue a run.
    Determinism is what makes that checkable — same seed, same assertions."""
    for t in REGISTRY.evaluation:
        seed = seeds_for(t.template_id, "B", 1)[0]
        a = t.instantiate(seed, FIRMS["B"])
        b = t.instantiate(seed, FIRMS["B"])
        assert a.instruction == b.instruction
        assert [x.assertion_id for x in a.assertions] == \
               [x.assertion_id for x in b.assertions]
        assert json.dumps([x.expects for x in a.assertions], sort_keys=True,
                          default=str) == \
               json.dumps([x.expects for x in b.assertions], sort_keys=True,
                          default=str)
