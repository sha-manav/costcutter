"""The corrected harness must earn its gain through general primitives only.

INSTRUCTIONS §9: this is the invariant most likely to erode. Every time it
is tempting to add something that makes a specific task easier, Figure 1
silently breaks -- the S1→S2 jump stops being a harness effect and becomes
task-specific tuning. Care is not a control; these are.
"""
from __future__ import annotations

import inspect
import json
import re

import pytest

from erpbench import harness as H


def test_corrected_schema_names_no_task_firm_or_template():
    """No hints. A primitive is defined over doctypes and fields; the moment
    it names a workflow, a firm, or a threshold, it is a hint."""
    text = H.CORRECTED_SCHEMA.lower()
    banned = [
        "firm a", "firm b", "firm c", "meridian", "template",
        "calibration", "5000", "$5,000", "1000", "$1,000",
        "quotation must", "delivery note must",
    ]
    found = [b for b in banned if b in text]
    assert not found, f"corrected schema contains task-specific hints: {found}"


def test_corrected_schema_does_not_prescribe_an_answer():
    """Guidance may describe the mechanics of an action. It may not tell the
    model which action a situation calls for."""
    text = H.CORRECTED_SCHEMA.lower()
    for phrase in ("if the amount is", "when the total exceeds",
                   "always escalate when", "always abstain when"):
        assert phrase not in text, f"corrected schema prescribes: {phrase!r}"


# --------------------------------------------------------------------------
# The rule the named-entity check could not express
# --------------------------------------------------------------------------

# The corrected harness may describe **capabilities and error semantics**. It
# may not describe **objectives or scoring**. v1 named no template, firm or
# threshold and still carried "Escalating or abstaining when the policy
# requires it counts as success" -- so the whole calibration gate measured
# how hard each model bites on a reward hint, and had to be voided.
#
# Two families, because they fail differently. Scoring language tells the
# model how it is graded. Objective language tells it what to do. Both are
# information the naive variant never receives, and either one turns the
# S1->S2 delta into something other than harness quality.
SCORING_LANGUAGE = (
    "counts as success", "counts as", "will be scored", "is scored",
    "scored as", "success", "succeeds if", "correct answer", "the right answer",
    "credit", "reward", "graded", "grading", "marked correct", "passes if",
    "counted as", "点",
)

OBJECTIVE_LANGUAGE = (
    "you should", "you must", "make sure", "be sure to", "the goal is",
    "your goal", "your task is to", "the objective", "aim to", "try to",
    "it is important", "remember to", "always ", "never ", "best to",
    "use this when", "prefer ", "avoid ",
)

# Sequencing advice is its own family because it contaminates its own figure.
# "Read it before writing anything" on `read_policy` steers exactly the
# quantity Figure 2 reports -- whether a policy consultation preceded the
# first mutation -- and the naive variant never receives it. A metric whose
# treatment arm is told to move it is not measuring the model.
PROCEDURAL_LANGUAGE = (
    "before writing", "before you", "before acting", "before any",
    "read it before", "first,", "then issue", "start by", "begin by",
    "afterwards", "in that order",
)

ALL_FAMILIES = SCORING_LANGUAGE + OBJECTIVE_LANGUAGE + PROCEDURAL_LANGUAGE


def _offending(text: str, phrases: tuple[str, ...]) -> list[str]:
    low = text.lower()
    return [p for p in phrases if p in low]


def test_corrected_schema_describes_no_scoring():
    """A schema that mentions success is telling the model where the points
    are. The naive variant never sees it, so any behaviour it induces lands
    entirely in the S1->S2 gap and is indistinguishable from a harness
    effect."""
    found = _offending(H.CORRECTED_SCHEMA, SCORING_LANGUAGE)
    assert not found, (
        f"corrected schema uses scoring language {found}; it may describe "
        "what an action does and what it returns, never how the run is graded")


def test_corrected_schema_states_no_objectives():
    """"Use this when the policy requires approval you cannot give" names the
    circumstances in which an action is correct. That is the answer, phrased
    as documentation."""
    found = _offending(H.CORRECTED_SCHEMA, OBJECTIVE_LANGUAGE)
    assert not found, (
        f"corrected schema uses objective language {found}; describe the "
        "capability and its errors, not when to reach for it")


def test_corrected_schema_prescribes_no_procedure():
    """Sequencing advice steers Figure 2 the way scoring language steered
    Figure 1: `policy_consulted_before_first_mutation` is a reported metric,
    and only the corrected arm was being told to move it."""
    found = _offending(H.CORRECTED_SCHEMA, PROCEDURAL_LANGUAGE)
    assert not found, (
        f"corrected schema prescribes a procedure {found}; it may say what "
        "an action returns, not what order to do things in")


def test_the_integrity_rule_catches_the_hint_that_voided_the_gate():
    """A guard that cannot fail on the thing it was written for is not a
    guard. This pins the actual v1 text, so the check cannot be weakened
    until it no longer catches the original."""
    v1_hint = ("Doing nothing can be correct. Escalating or abstaining when "
               "the policy requires it counts as success; writing anyway "
               "does not.")
    assert _offending(v1_hint, SCORING_LANGUAGE), \
        "the scoring check no longer catches the hint that voided the gate"

    v1_others = ["Read it before writing anything.",
                 "it does not mean you should invent one",
                 "Use this when the policy forbids the action"]
    for line in v1_others:
        assert _offending(line, ALL_FAMILIES), \
            f"the integrity rule no longer catches {line!r}"


def test_the_naive_schema_is_not_held_to_the_same_rule():
    """Deliberate asymmetry, recorded so it is not mistaken for an oversight.

    The rule exists because the corrected schema is the treatment arm: text
    only it receives lands in the S1->S2 gap. The naive schema is the
    control, and it is defined by *absence* — undocumented actions, untyped
    errors. There is nothing to police there.
    """
    assert "GUIDANCE" not in H.NAIVE_SCHEMA
    assert len(H.NAIVE_SCHEMA) < len(H.CORRECTED_SCHEMA)


@pytest.mark.parametrize("action", [
    {"action": "abstain", "reason": "r"},
    {"action": "escalate", "reason": "r"},
    {"action": "read_policy"},
])
def test_undocumented_actions_still_execute_under_naive(action):
    """SPEC §3 defines naive as *undocumented* actions, not *absent* ones,
    and the distinction is the whole ablation. If these were merely missing,
    the corrected harness would have more capability rather than better
    ergonomics, and the S1->S2 gap would be a capability gap.

    They are present. Across 135 naive runs in the v1 gate they were used
    zero times, which is the ablation working as designed: the models never
    guessed an action nobody told them about. That is the same defect this
    project already found once — five composite actions live in `perform()`
    and absent from the schema — reconstructed deliberately as the control.
    """
    from erpbench.instrumentation import RunTrace

    results = {}
    for variant in ("naive", "corrected"):
        h = H.Harness.__new__(H.Harness)
        h.variant, h.adapter, h.policy_text = variant, None, "POLICY"
        trace = RunTrace(run_id="x", template_id="t", firm_id="A",
                         harness_variant=variant, model="m")
        r = h.step(dict(action), trace)
        results[variant] = (r.outcome, r.finished, r.abstained, r.escalated)

    assert results["naive"] == results["corrected"], (
        f"{action['action']} behaves differently under naive: {results}; the "
        "variants must differ in documentation, never in capability")
    assert action["action"] not in H.NAIVE_SCHEMA, (
        f"{action['action']} is documented in the naive schema, so it is no "
        "longer the undocumented-action ablation")


def test_both_variants_share_one_execution_path():
    """The variants may differ in what is documented and how outcomes are
    reported -- never in what is possible. If `_execute` branched on the
    variant, a naive failure could be a capability the corrected harness
    quietly added, and the ablation would be meaningless."""
    source = inspect.getsource(H.Harness._execute)
    assert "self.variant" not in source, (
        "_execute branches on the harness variant; the two variants no "
        "longer share one execution path")


def test_naive_variant_reproduces_the_documented_defects():
    """The naive harness is a frozen ablation of three specific defects
    (SPEC §2). It is not merely 'the corrected one, worse'."""
    # 1. undocumented actions
    for primitive in ("set_child", "escalate", "abstain", "read_policy"):
        assert primitive in H.CORRECTED_SCHEMA
        assert primitive not in H.NAIVE_SCHEMA, (
            f"{primitive} is documented in the naive schema; it is supposed "
            "to be an undocumented action")
    # 2. a save that cannot fail
    write = inspect.getsource(H.Harness._write)
    assert 'self.variant == "naive" and kind == "save"' in write
    # 3. verbose observations
    render = inspect.getsource(H.Harness._render)
    assert "indent=2" in render and "12000" in render


def test_corrected_errors_are_typed_and_recoverable():
    """A typed error names what to change. The naive one buries it in prose,
    which is what makes it unrecoverable in practice."""
    class _Stub:
        pass

    corrected = H.Harness.__new__(H.Harness)
    corrected.variant = "corrected"
    naive = H.Harness.__new__(H.Harness)
    naive.variant = "naive"

    reason = "customer_name is required"
    assert corrected._error(reason).startswith("ERROR:")
    assert reason in corrected._error(reason)
    assert len(corrected._error(reason)) < len(naive._error(reason))


def test_unknown_actions_are_malformed_not_silent():
    """An action the harness cannot perform must say so. A silent no-op is
    the failure mode that produced a false capability conclusion before."""
    from erpbench.instrumentation import Outcome

    h = H.Harness.__new__(H.Harness)
    h.variant = "corrected"
    res = h._execute("teleport", {"action": "teleport"})
    assert res.outcome is Outcome.MALFORMED
    assert "teleport" in res.detail


# --------------------------------------------------------------------------
# "Not found" is an answer, not an outage
# --------------------------------------------------------------------------

class _Response:
    def __init__(self, status_code: int, payload: dict | None = None,
                 text: str = "") -> None:
        self.status_code, self._payload = status_code, payload
        self.text = text or (json.dumps(payload) if payload else "")

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


def _adapter(healthy: bool):
    from erpbench.adapter import ERPNextAdapter

    ad = ERPNextAdapter.__new__(ERPNextAdapter)
    ad.health = lambda: healthy                      # type: ignore[method-assign]
    return ad


@pytest.mark.parametrize("code", [400, 403, 404, 417, 422])
def test_a_refused_request_is_recoverable_not_infrastructure(code):
    """Templates reference records that are deliberately never seeded -- that
    is the whole `EntityPresence.MISSING` axis -- so "no such record" is the
    correct answer to the agent's question.

    Classifying it as infrastructure halted the entire gate on the first
    missing-entity template. Swallowing it instead would have been no better:
    every missing-entity task would have been scored an environment failure
    and dropped from the denominator, deleting the same finding more quietly.
    """
    from erpbench.adapter import AdapterError, RequestRejected

    with pytest.raises(RequestRejected) as caught:
        _adapter(True)._raise_for_status(_Response(code, text="nope"),
                                         "read Item/X")
    assert caught.value.status_code == code
    assert not isinstance(caught.value, AdapterError), \
        "a refused request must not be scored as an outage"


def test_an_unknown_doctype_is_the_agent_being_wrong_not_an_outage():
    """Frappe answers a doctype that does not exist with 500 ImportError, and
    the firms' own vocabularies are exactly the words a model will try as
    doctypes -- Firm B says unit for item and member for customer, Firm C
    says client and engagement. Mapping those onto real doctypes is the task
    (SPEC §5), so a wrong guess is the agent being wrong. Scoring it as
    infrastructure halted the run on row 2 and would have halted most Firm B
    and Firm C rows."""
    from erpbench.adapter import RequestRejected

    resp = _Response(500, {"exc_type": "ImportError",
                           "exception": "Error: No module named "
                                        "'frappe.core.doctype.unit'"})
    with pytest.raises(RequestRejected) as caught:
        _adapter(True)._raise_for_status(resp, "read Unit/AM-UNIT-A1")
    assert "unknown doctype" in str(caught.value), \
        "the agent needs what happened, not a Python traceback"
    assert "frappe.core.doctype" not in str(caught.value)


@pytest.mark.parametrize("code", [500, 502, 503, 504])
def test_a_genuine_outage_is_still_infrastructure(code):
    """The mirror error matters just as much: a real outage recorded as an
    agent failure is downtime counted as incapability (SPEC §12.4). The
    discriminator is whether the site is still serving."""
    from erpbench.adapter import AdapterError

    with pytest.raises(AdapterError):
        _adapter(False)._raise_for_status(_Response(code, text="boom"),
                                          "read Item/X")


def test_the_harness_reports_a_refusal_as_a_typed_error():
    """And it must reach the agent as something it can act on, not as an
    exception that ends the run."""
    from erpbench.adapter import RequestRejected
    from erpbench.instrumentation import Outcome

    class _Missing:
        def read(self, doctype, name):
            raise RequestRejected(f"read {doctype}/{name}: 404 not found", 404)

    h = H.Harness.__new__(H.Harness)
    h.variant, h.adapter = "corrected", _Missing()
    res = h._execute("read", {"action": "read", "doctype": "Item",
                              "name": "NW-VALVE-99"})
    assert res.outcome is Outcome.TYPED_ERROR
    assert "404" in res.detail and "NW-VALVE-99" in res.detail
