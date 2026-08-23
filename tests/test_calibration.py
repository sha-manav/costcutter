"""The calibration split: quarantined, and genuinely counterfactual."""
from __future__ import annotations

import pytest

from erpbench.calibration import (
    CALIBRATION_TEMPLATES, CalibrationLeak, _write_outcome, assert_reportable)
from erpbench.firms import FIRMS
from erpbench.templates import (
    AmountBand, EntityPresence, Params, REGISTRY, seeds_for)


def _signature(instance) -> tuple:
    """What the firm actually requires of this instance.

    Assertion *classes* are not enough: "submit" and "leave in draft" are
    both a status assertion, so comparing classes makes two firms with
    opposite requirements look identical. The expected value has to be in
    the signature or the counterfactual check passes vacuously -- which it
    did, on the first attempt.
    """
    return (
        tuple(sorted(s.describe() for s in instance.envelope.required)),
        tuple(sorted(s.describe() for s in instance.envelope.forbidden)),
        tuple(sorted(f"{a.cls.value}:{a.assertion_id}:{sorted(a.expects.items(), key=str)}"
                     for a in instance.assertions)),
    )


def test_exactly_fifteen_calibration_templates():
    assert len(CALIBRATION_TEMPLATES) == 15


def test_calibration_templates_are_refused_by_reporting():
    """SPEC §10.1: quarantined structurally, not by convention."""
    for t in CALIBRATION_TEMPLATES:
        with pytest.raises(CalibrationLeak):
            assert_reportable(t.template_id)
    assert_reportable("E01_some_evaluation_template")


def test_calibration_templates_are_not_in_the_evaluation_split():
    ids = {t.template_id for t in REGISTRY.evaluation}
    assert not ids & {t.template_id for t in CALIBRATION_TEMPLATES}


def test_policy_actually_diverges_across_firms():
    """The whole study rests on the three firms disagreeing. If they agree,
    the counterfactual set measures nothing."""
    diverging = 0
    for entity in EntityPresence:
        for band in (AmountBand.BELOW, AmountBand.AT, AmountBand.ABOVE):
            p = Params(seed=1, entity=entity, amount_band=band)
            outcomes = {f.firm_id: _write_outcome(f, p) for f in FIRMS.values()}
            if len(set(outcomes.values())) > 1:
                diverging += 1
    assert diverging >= 6, f"only {diverging} situations diverge across firms"


def test_the_same_instruction_gets_different_required_outcomes():
    """SPEC §5's counterfactual: identical instruction, divergent correct
    outcome by firm, on at least a third of the set."""
    diverged = []
    for t in CALIBRATION_TEMPLATES:
        seed = seeds_for(t.template_id, "counterfactual", 1)[0]
        sigs = {fid: _signature(t.instantiate(seed, f))
                for fid, f in FIRMS.items()}
        if len(set(sigs.values())) > 1:
            diverged.append(t.template_id)
    assert len(diverged) >= 5, (
        f"only {len(diverged)} of {len(CALIBRATION_TEMPLATES)} templates "
        f"diverge across firms: {diverged}")


def test_every_template_instantiates_for_every_firm():
    """A generator that raises corrupts every row that used it (SPEC §12.6),
    so it must not raise on any firm or any point of its own space."""
    for t in CALIBRATION_TEMPLATES:
        for firm in FIRMS.values():
            for seed in seeds_for(t.template_id, firm.firm_id, 6):
                inst = t.instantiate(seed, firm)
                assert inst.instruction.strip()
                assert inst.assertions, f"{t.template_id} generated no assertions"


def test_instructions_use_each_firms_own_vocabulary():
    """A model must map the firm's words onto doctypes; leaking the standard
    term would hand it the mapping."""
    firm_b = FIRMS["B"]
    hits = 0
    for t in CALIBRATION_TEMPLATES:
        seed = seeds_for(t.template_id, "B", 1)[0]
        text = t.instantiate(seed, firm_b).instruction.lower()
        if "member" in text or "assessment" in text or "unit" in text:
            hits += 1
    assert hits >= 5, f"only {hits} templates use Firm B's terminology"


def test_abstention_templates_require_a_stated_reason():
    """Silence is not abstention: an agent that stops without saying why is
    indistinguishable from one that crashed."""
    from erpbench.verify import AssertionClass

    firm_c = FIRMS["C"]
    for tid in ("C12_order_ambiguous_customer", "C14_contradictory_instruction"):
        t = REGISTRY.get(tid)
        inst = t.instantiate(seeds_for(tid, "C", 1)[0], firm_c)
        classes = {a.cls for a in inst.assertions}
        assert AssertionClass.ABSTENTION in classes
        assert len(inst.assertions) >= 2, (
            f"{tid} checks that nothing was written but not that a reason "
            "was given")


# --------------------------------------------------------------------------
# C02 must measure the thing its instruction asks for
# --------------------------------------------------------------------------

def test_c02_does_not_pass_a_model_that_refuses_to_answer():
    """Its only check was `wrote_nothing`, so abstaining passed a question
    that asked for a count — declining to answer writes nothing. Every other
    read template checks the answer; this one could not measure its own
    instruction."""
    from erpbench.firms import get_firm
    from erpbench.templates import REGISTRY
    from erpbench.verify import verify
    from erpbench.adapter import Diff

    class _Empty:
        def query(self, *a, **kw): return []
        def read(self, *a, **kw): return {}

    template = REGISTRY.get("C02_customer_order_count")
    inst = template.instantiate(7, get_firm("A"))
    refused = verify(_Empty(), inst.assertions, inst.envelope, Diff(),
                     "I cannot answer that.", abstained=True)
    assert refused.success is False, "refusing to answer must not pass C02"

    answered = verify(_Empty(), inst.assertions, inst.envelope, Diff(), "0")
    assert answered.success is True, "the correct count must pass"


def test_a_count_assertion_is_not_fooled_by_a_substring():
    """"0" is inside "10", so substring matching would accept exactly the
    wrong answer to a count question."""
    from erpbench.adapter import Diff
    from erpbench.verify import answer_number_is

    a = answer_number_is("t", 0, synonyms=("none", "zero"))
    assert a.evaluate(None, Diff(), "0").passed is True
    assert a.evaluate(None, Diff(), "none").passed is True
    assert a.evaluate(None, Diff(), "There are 10 orders.").passed is False
    assert a.evaluate(None, Diff(), "20").passed is False
