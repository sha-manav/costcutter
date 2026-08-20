"""The deterministic router and answer reduction used by condition B offline."""
from __future__ import annotations

from shadow.capture.schema import ToolSpec
from shadow.route.agent import (
    entity_hints, extract_answer, fill_arguments, goal_intent, select_tool,
    tool_surface,
)
from shadow.serve.executor import ExecutionResult


def _list_tool() -> ToolSpec:
    return ToolSpec(
        name="list_records", description="List records of a caller-specified type.",
        mutation_class="read", verified=True, support=13,
        params_schema={"type": "object", "required": ["doctype", "fields",
                                                      "filters", "order_by"],
                       "properties": {
                           "doctype": {"type": "string",
                                       "enum": ["Sales Order", "Sales Invoice",
                                                "Item Price"]},
                           "fields": {"type": "array"},
                           "filters": {"type": "array"},
                           "order_by": {"type": "string"}}})


def _write_tool() -> ToolSpec:
    return ToolSpec(name="create_customer", description="Create a customer.",
                    mutation_class="write", verified=True, support=4,
                    params_schema={"type": "object", "properties": {}})


def test_goal_intent():
    assert goal_intent("How many sales orders exist for 'Acme'?") == "read"
    assert goal_intent("Create a new customer named 'Meridian Logistics'.") == "write"


def test_a_question_never_selects_a_write_tool():
    goal = "How many sales orders exist for the customer 'Acme Industrial'?"
    chosen = select_tool(goal, [_list_tool(), _write_tool()])
    assert chosen is not None and chosen.spec.name == "list_records"


def test_a_change_never_selects_a_read_tool():
    goal = "Create a new customer named 'Meridian Logistics'."
    chosen = select_tool(goal, [_list_tool(), _write_tool()])
    assert chosen is not None and chosen.spec.mutation_class == "write"


def test_entity_hints_name_the_field_in_front_of_a_quoted_value():
    hints = entity_hints("total for the customer 'Acme Industrial' today")
    assert ("customer", "Acme Industrial") in hints


def test_enum_values_are_part_of_the_routable_surface():
    surface = tool_surface(_list_tool())
    assert {"sales", "order", "invoice"} <= surface


def test_arguments_come_from_the_goal_text():
    goal = "What is the grand total of the most recent sales order for customer 'Acme Industrial'?"
    args = fill_arguments(_list_tool(), goal)
    assert args["doctype"] == "Sales Order"
    assert args["filters"] == [["customer", "=", "Acme Industrial"]]
    assert "grand_total" in args["fields"]


def test_columnar_results_are_reduced_to_an_answer():
    payload = {"message": {"keys": ["name", "grand_total", "customer"],
                           "values": [["SO-1", 589.0, "Acme Industrial"]]}}
    result = ExecutionResult(tool="list_records", ok=True,
                             values=[payload, {"message": 1}], value={"message": 1})
    goal = "What is the grand total of the sales order for customer 'Acme Industrial'?"
    assert extract_answer(goal, result) == "589.00"
    assert extract_answer("How many sales orders exist for 'Acme Industrial'?",
                          result) == "1"


def test_declines_when_the_named_entity_is_absent_from_the_result():
    payload = {"message": {"keys": ["name", "grand_total", "customer"],
                           "values": [["SO-9", 42.0, "Someone Else"]]}}
    result = ExecutionResult(tool="list_records", ok=True, values=[payload],
                             value=payload)
    goal = "What is the grand total of the sales order for customer 'Acme Industrial'?"
    assert extract_answer(goal, result) is None


def test_offline_and_litellm_clients_accept_the_same_call():
    """Both providers must take the same call site kwargs.

    The offline provider routes on them; the real one must drop them rather
    than forward them to the API as unknown parameters.
    """
    import inspect

    from shadow.llm import LiteLLMClient

    source = inspect.getsource(LiteLLMClient.complete)
    assert 'kwargs.pop("policy", None)' in source
    assert 'kwargs.pop("policy_context", None)' in source


def test_compact_schema_does_not_carry_whole_observed_values():
    """A tool's prompt cost is paid on every step of every task."""
    from shadow.capture.schema import ToolSpec
    from shadow.route.agent import MAX_EXAMPLE_CHARS, compact_schema, render_tools

    spec = ToolSpec(
        name="list_records", description="List records.", mutation_class="read",
        verified=True,
        params_schema={
            "type": "object", "required": ["doctype"],
            "properties": {
                "doctype": {"type": "string", "enum": ["Sales Order", "Item Price"]},
                "fields": {"type": "array",
                           "examples": [[f"`tabSales Order`.`col{i}`"
                                         for i in range(30)]]},
            }})
    compact = compact_schema(spec)
    assert compact["doctype"]["enum"] == ["Sales Order", "Item Price"]
    assert "optional" not in compact["doctype"]
    assert compact["fields"]["optional"] is True
    assert len(compact["fields"]["example"]) <= MAX_EXAMPLE_CHARS + 1
    # The full 30-column specimen must not reach the prompt.
    assert "col29" not in render_tools([spec])
