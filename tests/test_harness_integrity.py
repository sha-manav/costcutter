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
