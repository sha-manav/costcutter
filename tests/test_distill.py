"""Unit tests for the synthesis core, on synthetic traffic."""
from __future__ import annotations

import time

import pytest

from shadow.capture.schema import Episode, HttpRecord
from shadow.config import get_config
from shadow.distill.classify import classify_step, classify_steps
from shadow.distill.filter import collapse_polling, filter_records
from shadow.distill.induce import induce, infer_type, maybe_enum, normalize_path
from shadow.distill.provenance import ProvenanceEngine
from shadow.distill.segment import segment
from shadow.capture.schema import ToolStep


def rec(i, method, path, *, query=None, body=None, resp=None, ts=0.0,
        ct="application/json", status=200):
    return HttpRecord(id=f"r{i}", ts_start=ts, ts_end=ts + 0.05, method=method,
                      url=f"http://app{path}", path=path, query=query or {},
                      req_body=body, status=status, resp_body=resp,
                      content_type=ct, duration_ms=50)


# --------------------------------------------------------------------------
# filter
# --------------------------------------------------------------------------

def test_filter_drops_assets_and_keeps_documents():
    records = [
        rec(0, "GET", "/app/home", ct="text/html"),
        rec(1, "GET", "/assets/app.js", ct="application/javascript"),
        rec(2, "GET", "/assets/logo.png", ct="image/png"),
        rec(3, "POST", "/api/method/frappe.client.get_list", resp={"message": []}),
        rec(4, "GET", "/api/method/ping", status=304),
    ]
    kept, stats = filter_records(records)
    paths = [r.path for r in kept]
    assert "/assets/app.js" not in paths and "/assets/logo.png" not in paths
    assert "/app/home" in paths and "/api/method/frappe.client.get_list" in paths
    assert stats.dropped_not_modified == 1
    assert stats.retention < 1.0


def test_polling_collapses_only_when_response_is_unchanged():
    steady = [rec(i, "GET", "/api/method/heartbeat", resp={"n": 1}, ts=i * 5.0)
              for i in range(6)]
    kept, collapsed = collapse_polling(list(steady))
    assert collapsed == 5 and len(kept) == 1 and kept[0].is_polling
    assert kept[0].collapsed_count == 6

    changing = [rec(i, "GET", "/api/method/heartbeat", resp={"n": i}, ts=i * 5.0)
                for i in range(6)]
    kept2, collapsed2 = collapse_polling(list(changing))
    assert collapsed2 == 0 and len(kept2) == 6


# --------------------------------------------------------------------------
# provenance
# --------------------------------------------------------------------------

def _order_episode(order_id: str, customer: str) -> Episode:
    return Episode(id="e", label="open order", records=[
        rec(0, "POST", "/api/method/frappe.desk.reportview.get",
            body={"doctype": "Sales Order"},
            resp={"message": {"values": [[order_id, customer]],
                              "keys": ["name", "customer"]}}, ts=0.0),
        rec(1, "GET", f"/api/resource/Sales Order/{order_id}",
            resp={"data": {"name": order_id, "grand_total": 100}}, ts=1.0),
    ])


def test_provenance_links_id_to_the_earlier_response():
    engine = ProvenanceEngine(get_config())
    result = engine.infer(_order_episode("SAL-ORD-2026-00001", "Acme Industrial"))
    path_bindings = {site.key: b for site, b in result.bindings.items()
                     if site.step_index == 1 and site.location == "path"}
    binding = path_bindings["seg3"]
    assert binding.kind == "from_response"
    assert binding.source_step_index == 0
    assert binding.json_path == "$.message.values[0][0]"


def test_provenance_trace_is_printable():
    engine = ProvenanceEngine(get_config())
    episode = _order_episode("SAL-ORD-2026-00001", "Acme Industrial")
    trace = engine.infer(episode).trace(episode)
    assert "SAL-ORD-2026-00001" in trace and "$." in trace
    assert trace.count("\n") > 2


def test_short_and_stoplisted_values_are_not_matched():
    engine = ProvenanceEngine(get_config())
    episode = Episode(id="e", records=[
        rec(0, "GET", "/api/method/a", resp={"message": {"ok": 1, "x": "ab"}}, ts=0),
        rec(1, "GET", "/api/method/b", query={"flag": "1", "s": "ab"}, ts=1),
    ])
    result = engine.infer(episode)
    kinds = {site.key: b.kind for site, b in result.bindings.items()
             if site.step_index == 1 and site.location == "query"}
    assert kinds["flag"] == "literal"     # stoplisted
    assert kinds["s"] == "literal"        # below min_value_len


# --------------------------------------------------------------------------
# induction
# --------------------------------------------------------------------------

def test_normalize_path_templates_identifiers():
    template, idx = normalize_path("/api/resource/Sales Order/SAL-ORD-2026-00001")
    assert template == "/api/resource/Sales Order/{}"
    assert idx == [3]
    assert normalize_path("/api/method/frappe.client.get_list")[1] == []


def _list_episode(idx: int, doctype: str, customer: str) -> Episode:
    base = idx * 100.0
    return Episode(id=f"ep{idx}", label=f"list {doctype}", records=[
        rec(0, "POST", "/api/method/frappe.desk.reportview.get",
            body={"doctype": doctype, "filters": customer, "page_length": 20},
            resp={"message": {"values": [[f"{doctype}-1", customer]]}}, ts=base),
    ])


def test_induction_separates_constants_from_parameters():
    episodes = [
        _list_episode(0, "Sales Order", "Acme Industrial"),
        _list_episode(1, "Sales Invoice", "Borealis Freight"),
        _list_episode(2, "Item Price", "Cobalt Robotics"),
        _list_episode(3, "Sales Order", "Delta Mining Co"),
    ]
    result = induce(episodes, get_config())
    assert len(result.catalog.tools) == 1
    spec = result.catalog.tools[0]
    assert spec.support == 4
    props = spec.params_schema["properties"]
    # doctype and filters vary -> parameters; page_length is constant -> literal
    assert {"doctype", "filters"} <= set(props)
    body = spec.steps[0].body_bindings
    assert body["page_length"].kind == "literal"
    assert body["doctype"].kind == "user_param"


def test_induction_respects_min_support():
    episodes = [_list_episode(0, "Sales Order", "Acme Industrial"),
                _list_episode(1, "Sales Invoice", "Borealis Freight")]
    result = induce(episodes, get_config())
    assert result.catalog.tools == []
    assert result.groups_below_support == 1


# --------------------------------------------------------------------------
# classification
# --------------------------------------------------------------------------

@pytest.mark.parametrize("method,path,expected", [
    ("GET", "/api/resource/Customer", "read"),
    ("POST", "/api/method/frappe.desk.reportview.get", "read"),
    ("POST", "/api/method/frappe.client.get_list", "read"),
    ("POST", "/api/method/frappe.desk.form.save.savedocs", "write"),
    ("PUT", "/api/resource/Customer/{p0}", "write"),
    ("DELETE", "/api/resource/Customer/{p0}", "destructive"),
    ("POST", "/api/method/frappe.client.cancel", "destructive"),
])
def test_mutation_classes(method, path, expected):
    assert classify_step(ToolStep(method=method, path_template=path)) == expected


def test_worst_class_wins():
    steps = [ToolStep(method="GET", path_template="/api/resource/Customer"),
             ToolStep(method="POST", path_template="/api/method/frappe.client.delete")]
    assert classify_steps(steps) == "destructive"


# --------------------------------------------------------------------------
# segmentation
# --------------------------------------------------------------------------

def test_segmentation_cuts_on_idle_gap():
    cfg = get_config()
    gap = cfg.segment.idle_gap_s + 5
    records = [
        rec(0, "POST", "/api/method/frappe.desk.reportview.get",
            body={"doctype": "Sales Order"}, resp={"message": {}}, ts=0.0),
        rec(1, "GET", "/api/resource/Sales Order/SAL-ORD-2026-00001",
            resp={"data": {}}, ts=1.0),
        rec(2, "POST", "/api/method/frappe.desk.reportview.get",
            body={"doctype": "Customer"}, resp={"message": {}}, ts=gap),
        rec(3, "GET", "/api/resource/Customer/Acme Industrial",
            resp={"data": {}}, ts=gap + 1),
    ]
    episodes = segment(records, cfg).episodes
    assert len(episodes) == 2
    assert all(len(e.records) == 2 for e in episodes)
    assert episodes[0].records[0].episode_id == episodes[0].id


def test_enum_requires_a_saturated_value_set():
    """Low cardinality alone is not a closed domain."""
    cfg = get_config()
    # Three names in three observations: an identifier, not an enum.
    assert maybe_enum(["Acme", "Borealis", "Cobalt"], cfg.induce.enum_max) is None
    # Three values across twelve observations: a closed set.
    repeated = ["Sales Order", "Sales Invoice", "Item Price"] * 4
    assert maybe_enum(repeated, cfg.induce.enum_max) == [
        "Sales Order", "Sales Invoice", "Item Price"]


@pytest.mark.parametrize("values,expected", [
    (["2026-08-20", "2026-01-01"], {"type": "string", "format": "date"}),
    (["2026-08-20 13:54:57.606903"], {"type": "string", "format": "date"}),
    (["2026-08-20T13:54:57Z"], {"type": "string", "format": "date"}),
    (["SAL-ORD-2026-00001"], {"type": "string"}),
    (["5", "12"], {"type": "integer"}),
    (["45.00", "62.5"], {"type": "number"}),
])
def test_type_inference(values, expected):
    assert infer_type(values) == expected


# --- curriculum staging ----------------------------------------------------
# 2026-08-27: restage() sent every planned-T1 trace that did not recover into
# T2, the execution stage. Hard-recovery instances are drawn with MISSING /
# STALE / AMBIGUOUS parameters where the correct outcome is usually a refusal,
# and a refusal passes rejection sampling because declining an impossible task
# is a full success. 84 of T2's 129 examples arrived that way; the model
# trained on it stopped writing entirely (0/175 on tasks requiring a write).

def _trace(actions, recovery=0):
    return {"actions": [{"action": a} for a in actions],
            "behaviour": {"recovery_events": recovery}}


def test_a_refusal_is_never_staged_as_execution():
    """Stage by what the trace demonstrates, not by what it failed to show.

    The rule, not the instance: any accepted trace whose terminal action is a
    refusal is policy data, wherever it was planned. Execution data must
    demonstrate execution.
    """
    from erpbench.teacher import restage

    for planned in ("T1", "T2", "T3"):
        for refusal in ("escalate", "abstain"):
            got = restage(_trace(["read_policy", "query", refusal]), planned)
            assert got == "T3", (
                f"a trace ending in {refusal!r} planned {planned} was staged "
                f"{got}; refusals are policy data, never execution")


def test_completed_execution_still_reaches_t2():
    from erpbench.teacher import restage

    assert restage(_trace(["read_policy", "query", "create", "done"]), "T2") == "T2"
    # Recovery still wins over everything else.
    assert restage(_trace(["query", "create", "done"], recovery=1), "T2") == "T1"
