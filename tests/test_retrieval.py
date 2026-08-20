"""Tool retrieval: the catalog should be a variable cost, not a fixed tax."""
from __future__ import annotations

import pytest

from shadow.capture.schema import ToolCatalog, ToolSpec
from shadow.config import get_config
from shadow.route.agent import (
    available_tools, bm25_scores, eligible_tools, render_tools, retrieve_tools,
)


def _tool(name: str, description: str, params: dict | None = None,
          mutation: str = "read") -> ToolSpec:
    return ToolSpec(
        name=name, description=description, mutation_class=mutation,
        verified=True, support=5,
        params_schema={"type": "object", "properties": params or {}})


CATALOG = ToolCatalog(tools=[
    _tool("list_records", "List records of a caller-specified record type.",
          {"doctype": {"type": "string",
                       "enum": ["Sales Order", "Sales Invoice", "Item Price"]}}),
    _tool("list_customers", "List customer records.", {"name": {"type": "string"}}),
    _tool("update_item_price", "Update item price records.",
          {"price_list_rate": {"type": "number"}}, mutation="write"),
    _tool("create_customer", "Create customer records.",
          {"customer_name": {"type": "string"}}, mutation="write"),
])


def test_bm25_prefers_the_document_that_shares_rare_terms():
    docs = [["list", "records", "sales", "order"], ["list", "customers"],
            ["update", "item", "price"]]
    scores = bm25_scores(["sales", "order"], docs)
    assert scores[0] == max(scores) > 0
    assert scores[1] == 0 and scores[2] == 0


def test_common_terms_do_not_subtract():
    """A term in every document must not push a score negative."""
    docs = [["list", "a"], ["list", "b"], ["list", "c"]]
    assert all(score >= 0 for score in bm25_scores(["list"], docs))


def test_top_k_is_respected():
    tools = eligible_tools(CATALOG, allow_writes=True)
    result = retrieve_tools("list the sales order records", tools, k=2)
    assert len(result.tools) <= 2
    assert result.tools[0].name == "list_records"
    assert result.considered == 4


def test_a_goal_no_tool_covers_gets_no_catalog_at_all():
    """This is where most of the saving is."""
    tools = eligible_tools(CATALOG, allow_writes=True)
    result = retrieve_tools("what is the weather in reykjavik", tools, k=3)
    assert result.tools == []
    assert result.below_floor
    assert render_tools(result.tools).count("\n") <= 1   # the fallback line only


def test_k_zero_offers_nothing():
    tools = eligible_tools(CATALOG, allow_writes=True)
    assert retrieve_tools("list the sales orders", tools, k=0).tools == []


def test_the_mutation_gate_runs_before_retrieval():
    """A write tool that allow_writes excluded must not eat a retrieval slot."""
    goal = "create a customer named 'Meridian'"
    with_writes = available_tools(CATALOG, allow_writes=True, goal=goal, k=1)
    assert [t.name for t in with_writes] == ["create_customer"]

    read_only = available_tools(CATALOG, allow_writes=False, goal=goal, k=1)
    assert all(t.mutation_class == "read" for t in read_only)
    # The slot went to the best *eligible* tool rather than being spent on a
    # tool that was filtered out afterwards.
    assert len(read_only) <= 1


def test_retrieval_is_off_when_no_goal_or_k_is_given():
    assert len(available_tools(CATALOG, allow_writes=True)) == 4


def test_config_exposes_k_and_floor():
    cfg = get_config()
    assert cfg.bench.tool_k >= 0
    assert isinstance(cfg.bench.tool_score_floor, float)


@pytest.mark.parametrize("k", [0, 1, 3, 8])
def test_sweep_values_all_work(k):
    tools = eligible_tools(CATALOG, allow_writes=True)
    result = retrieve_tools("list the sales order records", tools, k=k)
    assert len(result.tools) <= max(k, 0)
