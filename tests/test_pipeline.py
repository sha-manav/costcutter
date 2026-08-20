"""End-to-end synthesis on a small synthetic capture.

Guards the seam between stages: a change that quietly stops producing tools
should fail here rather than at the next benchmark run.
"""
from __future__ import annotations

import asyncio
import json

from shadow.capture.schema import HttpRecord, read_catalog, write_jsonl
from shadow.config import REPO_ROOT, get_config
from shadow.distill.emit import emit, render_catalog
from shadow.distill.filter import filter_records
from shadow.distill.induce import induce
from shadow.distill.segment import segment
from shadow.serve.server import build_server

DOCTYPES = ["Sales Order", "Sales Invoice", "Item Price"]
CHROME = "/api/method/frappe.desk.desktop.get_workspace_sidebar_items"


def _rec(uid, ts, method, path, *, query=None, body=None, resp=None,
         ct="application/json"):
    return HttpRecord(id=uid, ts_start=ts, ts_end=ts + 0.1, method=method,
                      url=f"http://app{path}", path=path, query=query or {},
                      req_body=body, status=200, resp_body=resp,
                      content_type=ct, duration_ms=100)


def _chrome(uid, ts, nonce):
    # Response varies, so this is page chrome rather than a polling heartbeat
    # — the two are filtered by different mechanisms and this exercises the
    # document-frequency one.
    return _rec(uid, ts, "POST", CHROME, body={"page": "home"},
                resp={"message": {"unread": nonce, "shortcuts": []}})


def _edit_episode(base: float, doctype: str, record: str) -> list[HttpRecord]:
    """Find a record, open it, save it back."""
    return [
        _chrome(f"{base}-0", base, int(base)),
        _rec(f"{base}-1", base + 1, "POST",
             "/api/method/frappe.desk.reportview.get",
             body={"doctype": doctype, "filters": [[doctype, "name", "=", record]],
                   "fields": ["name"], "page_length": 20},
             resp={"message": {"keys": ["name"], "values": [[record]]}}),
        _rec(f"{base}-2", base + 2, "GET",
             "/api/method/frappe.desk.form.load.getdoc",
             query={"doctype": doctype, "name": record},
             resp={"docs": [{"name": record, "doctype": doctype,
                             "owner": "Administrator", "modified": "2026-01-01"}]}),
        _rec(f"{base}-3", base + 3, "POST",
             "/api/method/frappe.desk.form.save.savedocs",
             body={"doc": json.dumps({"name": record, "doctype": doctype,
                                      "owner": "Administrator",
                                      "modified": "2026-01-01",
                                      "note": f"note for {record}"}),
                   "action": "Save"},
             resp={"docs": [{"name": record}]}),
    ]


def _browse_episode(base: float, doctype: str) -> list[HttpRecord]:
    """Open a list view and read it."""
    return [
        _chrome(f"{base}-0", base, int(base)),
        _rec(f"{base}-1", base + 1, "POST",
             "/api/method/frappe.desk.reportview.get",
             body={"doctype": doctype, "filters": [], "fields": ["name"],
                   "page_length": 20},
             resp={"message": {"keys": ["name"], "values": [["A"], ["B"]]}}),
    ]


def _capture() -> list[HttpRecord]:
    cfg = get_config()
    # Wider than both cut thresholds: past idle_gap_s so episodes split, and
    # past merge_gap_s so same-labelled neighbours do not re-merge.
    gap = cfg.segment.idle_gap_s + cfg.segment.merge_gap_s + 5
    records: list[HttpRecord] = []
    for i in range(4):
        records += _edit_episode(i * gap, DOCTYPES[i % len(DOCTYPES)], f"REC-{i:04d}")
    for i in range(4, 8):
        records += _browse_episode(i * gap, DOCTYPES[i % len(DOCTYPES)])
    records.append(_rec("asset", 1, "GET", "/assets/app.js",
                        ct="application/javascript"))
    return records


def test_capture_to_servable_tools(tmp_path):
    cfg = get_config()
    write_jsonl(tmp_path / "out.jsonl", _capture())

    kept, stats = filter_records(_capture())
    assert stats.retention < 1.0
    assert all("/assets/" not in r.path for r in kept)

    episodes = segment(kept, cfg).episodes
    assert len(episodes) == 8

    result = induce(episodes, cfg)
    catalog = result.catalog
    assert catalog.tools, render_catalog(catalog)

    # Chrome was found by document frequency, not from a hand-written list,
    # and the trim kept it out of every tool.
    assert any(CHROME in d for d in result.diagnostics)
    assert all(CHROME not in step.path_template
               for tool in catalog.tools for step in tool.steps)

    write = next(t for t in catalog.tools if t.mutation_class == "write")
    assert write.support >= cfg.induce.min_support
    # Load then save. The search that preceded them in every episode is
    # chrome by document frequency and nothing depends on its response, so
    # the record name becomes a parameter and the call is dropped.
    assert [s.method for s in write.steps] == ["GET", "POST"]

    doc_bindings = write.steps[-1].body_bindings
    # The record type varied across episodes, so it became a parameter.
    doctype_binding = doc_bindings.get("doc::$.doctype")
    assert doctype_binding is not None and doctype_binding.kind == "user_param"
    # Fields the save echoed back from the load are carried, not asked for.
    owner = doc_bindings.get("doc::$.owner")
    assert owner is not None and owner.kind == "from_response"
    assert owner.source_step_index == 0
    # `action=Save` never varied, so it is baked in as a literal.
    action = doc_bindings.get("action")
    assert action is not None and action.kind == "literal"
    assert action.literal_value == "Save"

    read = next(t for t in catalog.tools if t.mutation_class == "read")
    assert "doctype" in read.params_schema["properties"]

    # And it serves over MCP once verified.
    for tool in catalog.tools:
        tool.verified = True
    tools_path = tmp_path / "tools.json"
    emit(catalog, tools_path, tmp_path / "mcp_server.py", REPO_ROOT)
    assert read_catalog(tools_path).by_name(write.name) is not None
    server = build_server(tools_path, allow_writes=True)
    assert asyncio.run(server.get_tool(write.name)) is not None
