"""Executor and MCP server behaviour, without touching the live app."""
from __future__ import annotations

import asyncio
import json

import pytest

from shadow.capture.schema import (
    Binding, ToolCatalog, ToolSpec, ToolStep, write_catalog,
)
from shadow.config import get_config
from shadow.serve.executor import (
    BindingError, ToolExecutor, resolve_json_path, set_json_path,
)
from shadow.serve.server import build_server, exposed_tools, tool_description


def _tool(name="list_records", mutation="read", verified=True) -> ToolSpec:
    return ToolSpec(
        name=name, description="List records in the ERP system.",
        mutation_class=mutation, verified=verified, support=4,
        params_schema={"type": "object",
                       "properties": {"doctype": {"type": "string"}},
                       "required": ["doctype"]},
        steps=[ToolStep(method="GET", path_template="/api/resource/{p0}",
                        path_bindings={"p0": Binding(kind="user_param",
                                                     param_name="doctype")})])


# --------------------------------------------------------------------------
# json paths
# --------------------------------------------------------------------------

def test_json_path_round_trip():
    doc = {}
    set_json_path(doc, "$.doc.items[1].item_code", "SH-GEAR-01")
    assert doc["doc"]["items"][1]["item_code"] == "SH-GEAR-01"
    assert resolve_json_path(doc, "$.doc.items[1].item_code") == "SH-GEAR-01"


def test_resolve_missing_path_raises():
    with pytest.raises(BindingError):
        resolve_json_path({"a": 1}, "$.a.b")


# --------------------------------------------------------------------------
# executor
# --------------------------------------------------------------------------

class _FakeSession:
    """Records requests; returns a canned response per step."""

    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def request(self, method, path, params=None, json_body=None, data=None):
        import httpx

        self.calls.append({"method": method, "path": path, "params": params,
                           "json": json_body, "data": data})
        body = self.responses[len(self.calls) - 1]
        request = httpx.Request(method, "http://app" + path, params=params)
        return httpx.Response(200, json=body, request=request)


def test_executor_chains_a_response_value_into_the_next_step():
    spec = ToolSpec(
        name="open_latest_order", description="Open the latest sales order.",
        mutation_class="read", verified=True,
        steps=[
            ToolStep(method="POST", path_template="/api/method/list",
                     body_encoding="form",
                     body_bindings={"doctype": Binding(kind="literal",
                                                       literal_value="Sales Order")}),
            ToolStep(method="GET", path_template="/api/resource/Sales Order/{p0}",
                     path_bindings={"p0": Binding(
                         kind="from_response", source_step_index=0,
                         json_path="$.message.values[0][0]")}),
        ])
    session = _FakeSession([{"message": {"values": [["SAL-ORD-2026-00007"]]}},
                            {"data": {"name": "SAL-ORD-2026-00007"}}])
    result = ToolExecutor(get_config(), session=session).execute(spec, {})
    assert result.ok
    assert session.calls[1]["path"] == "/api/resource/Sales Order/SAL-ORD-2026-00007"
    assert result.value == {"data": {"name": "SAL-ORD-2026-00007"}}


def test_executor_fails_closed_on_an_unresolvable_binding():
    spec = ToolSpec(
        name="broken", description="", mutation_class="read", verified=True,
        steps=[
            ToolStep(method="GET", path_template="/api/method/a"),
            ToolStep(method="GET", path_template="/api/resource/X/{p0}",
                     path_bindings={"p0": Binding(kind="from_response",
                                                  source_step_index=0,
                                                  json_path="$.message.missing")}),
        ])
    session = _FakeSession([{"message": {}}, {"data": {}}])
    result = ToolExecutor(get_config(), session=session).execute(spec, {})
    assert not result.ok
    assert "unresolved binding" in (result.error or "")
    assert len(session.calls) == 1        # the second call was never sent


def test_executor_refuses_writes_unless_allowed():
    spec = _tool(mutation="write")
    session = _FakeSession([{"data": {}}])
    result = ToolExecutor(get_config(), session=session).execute(spec, {"doctype": "X"})
    assert not result.ok and "writes are disabled" in (result.error or "")
    assert session.calls == []


def test_optional_parameter_falls_back_to_the_observed_value():
    spec = ToolSpec(
        name="list_with_default", description="", mutation_class="read",
        verified=True,
        steps=[ToolStep(method="GET", path_template="/api/resource/Customer",
                        query_bindings={"limit_page_length": Binding(
                            kind="user_param", param_name="limit",
                            example_value=20)})])
    session = _FakeSession([{"data": []}])
    ToolExecutor(get_config(), session=session).execute(spec, {})
    assert session.calls[0]["params"] == {"limit_page_length": 20}


def test_body_paths_are_reassembled_into_nested_json():
    spec = ToolSpec(
        name="save_doc", description="", mutation_class="write", verified=True,
        steps=[ToolStep(
            method="POST", path_template="/api/method/save", body_encoding="form",
            body_bindings={
                "doc::$.doctype": Binding(kind="literal", literal_value="Customer"),
                "doc::$.customer_name": Binding(kind="user_param",
                                                param_name="customer_name"),
                "action": Binding(kind="literal", literal_value="Save"),
            })])
    session = _FakeSession([{"docs": [{"name": "Acme"}]}])
    result = ToolExecutor(get_config(), session=session, allow_writes=True).execute(
        spec, {"customer_name": "Acme"})
    assert result.ok
    sent = json.loads(session.calls[0]["data"]["doc"])
    assert sent == {"doctype": "Customer", "customer_name": "Acme"}
    assert session.calls[0]["data"]["action"] == "Save"


# --------------------------------------------------------------------------
# server
# --------------------------------------------------------------------------

def test_only_verified_read_tools_are_exposed_by_default():
    catalog = ToolCatalog(tools=[
        _tool("list_records"),
        _tool("create_customer", mutation="write"),
        _tool("delete_customer", mutation="destructive"),
        _tool("unverified_tool", verified=False),
    ])
    names = {t.name for t in exposed_tools(catalog, allow_writes=False)}
    assert names == {"list_records"}
    with_writes = {t.name for t in exposed_tools(catalog, allow_writes=True)}
    assert with_writes == {"list_records", "create_customer", "delete_customer"}


def test_descriptions_warn_about_mutation():
    assert tool_description(_tool(mutation="write")).startswith("[writes data]")
    assert tool_description(_tool(mutation="destructive")).startswith("[DESTRUCTIVE]")


def test_server_registers_the_induced_schema(tmp_path):
    path = tmp_path / "tools.json"
    write_catalog(path, ToolCatalog(tools=[_tool()]))
    server = build_server(path)
    tool = asyncio.run(server.get_tool("list_records"))
    assert tool.parameters["properties"]["doctype"]["type"] == "string"
    assert tool.parameters["required"] == ["doctype"]


def test_emitted_server_module_is_runnable(tmp_path):
    """The generated MCP server must import and build from its catalog."""
    from shadow.config import REPO_ROOT
    from shadow.distill.emit import emit

    catalog = ToolCatalog(tools=[_tool()])
    tools_path = tmp_path / "tools.json"
    server_path = tmp_path / "mcp_server.py"
    emit(catalog, tools_path, server_path, REPO_ROOT)

    source = server_path.read_text()
    assert "build_server" in source and str(tools_path) in source
    compile(source, str(server_path), "exec")
    server = build_server(tools_path)
    assert asyncio.run(server.get_tool("list_records")) is not None
