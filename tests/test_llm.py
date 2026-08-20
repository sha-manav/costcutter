"""The provider path, exercised against a stub.

No model credentials exist in this environment, so the network leg of the
litellm path is unproven — see FINDINGS. Everything up to and including the
provider call is covered here: what gets sent, how usage is read back, and
that the two conditions are accounted identically.
"""
from __future__ import annotations

import json
import sys
import types
from typing import Any

import pytest

from shadow.config import get_config
from shadow.llm import (
    LiteLLMClient, LLMUsage, make_client, mark_cacheable, supports_prompt_caching,
)


class _Details:
    def __init__(self, cached: int) -> None:
        self.cached_tokens = cached


class _Usage:
    def __init__(self, prompt: int, completion: int, cached: int = 0,
                 cache_write: int = 0, style: str = "details") -> None:
        self.prompt_tokens = prompt
        self.completion_tokens = completion
        if style == "details":
            self.prompt_tokens_details = _Details(cached)
        else:
            self.prompt_tokens_details = None
            self.cache_read_input_tokens = cached
            self.cache_creation_input_tokens = cache_write


class _Message:
    def __init__(self, content: str) -> None:
        self.content = content
        self.tool_calls = None


class _Choice:
    def __init__(self, content: str) -> None:
        self.message = _Message(content)


class _Response:
    def __init__(self, content: str, usage: _Usage) -> None:
        self.choices = [_Choice(content)]
        self.usage = usage


@pytest.fixture
def stub_litellm(monkeypatch):
    """Install a stand-in `litellm` that records what it was called with."""
    calls: list[dict[str, Any]] = []
    module = types.ModuleType("litellm")

    def completion(model: str, messages: list[dict], **kwargs: Any):
        calls.append({"model": model, "messages": messages, "kwargs": kwargs})
        return _Response(json.dumps({"action": "done", "answer": "42"}),
                         _Usage(prompt=1000, completion=20, cached=800))

    module.completion = completion
    module.token_counter = lambda **kw: 7
    monkeypatch.setitem(sys.modules, "litellm", module)
    return calls


def test_usage_comes_from_the_provider_not_the_tokenizer(stub_litellm):
    resp = LiteLLMClient("claude-haiku-4-5-20251001").complete(
        [{"role": "system", "content": "S"}, {"role": "user", "content": "U"}])
    assert resp.usage.simulated is False
    assert resp.usage.cached_input_tokens == 800
    assert resp.usage.input_tokens == 200          # prompt minus cached
    assert resp.usage.output_tokens == 20
    assert resp.usage.total_input_tokens == 1000


def test_anthropic_cache_fields_are_read(monkeypatch):
    module = types.ModuleType("litellm")
    module.completion = lambda model, messages, **kw: _Response(
        "{}", _Usage(prompt=0, completion=5, cached=700, cache_write=300,
                     style="anthropic"))
    module.token_counter = lambda **kw: 1
    monkeypatch.setitem(sys.modules, "litellm", module)
    usage = LiteLLMClient("claude-haiku-4-5-20251001").complete(
        [{"role": "user", "content": "U"}]).usage
    assert usage.cached_input_tokens == 700
    assert usage.input_tokens == 300               # the cache write


def test_offline_call_site_kwargs_never_reach_the_provider(stub_litellm):
    LiteLLMClient("claude-haiku-4-5-20251001").complete(
        [{"role": "user", "content": "U"}], policy="tool_agent",
        policy_context={"router": None})
    assert stub_litellm[0]["kwargs"] == {}


def test_the_fixed_prefix_is_marked_cacheable(stub_litellm):
    LiteLLMClient("claude-haiku-4-5-20251001").complete(
        [{"role": "system", "content": "FIXED"}, {"role": "user", "content": "VAR"}])
    sent = stub_litellm[0]["messages"]
    assert sent[0]["content"][0]["cache_control"] == {"type": "ephemeral"}
    assert sent[0]["content"][0]["text"] == "FIXED"
    assert sent[1] == {"role": "user", "content": "VAR"}


def test_caching_is_not_forced_on_providers_that_lack_it():
    assert supports_prompt_caching("claude-haiku-4-5-20251001")
    assert not supports_prompt_caching("gpt-4o")
    plain = mark_cacheable([{"role": "system", "content": "S"}], "gpt-4o")
    assert plain == [{"role": "system", "content": "S"}]


def test_caching_can_be_disabled(stub_litellm):
    LiteLLMClient("claude-haiku-4-5-20251001", prompt_caching=False).complete(
        [{"role": "system", "content": "S"}])
    assert stub_litellm[0]["messages"][0]["content"] == "S"


def test_provider_selection():
    assert make_client("claude-haiku-4-5-20251001", "offline").provider == "offline"
    assert make_client("claude-haiku-4-5-20251001", "litellm").provider == "litellm"


def test_both_conditions_price_usage_through_one_function():
    """A and B must not be costed by different code."""
    from shadow.bench.metrics import usd

    cfg = get_config()
    usage = LLMUsage(model=cfg.models.agent, input_tokens=1000,
                     cached_input_tokens=1000, output_tokens=100).to_dict()
    price = cfg.costs[cfg.models.agent]
    expected = (1000 / 1e6 * price.input + 1000 / 1e6 * price.cached_input
                + 100 / 1e6 * price.output)
    assert usd(cfg, usage) == pytest.approx(expected)
    # And a model with no price entry is refused rather than costed at zero.
    from shadow.bench.metrics import MissingCost

    with pytest.raises(MissingCost):
        usd(cfg, {"model": "no-such-model", "input_tokens": 1})


def test_the_model_drives_tool_selection_under_litellm(monkeypatch, tmp_path):
    """Condition B must route on the model's reply, not the lexical matcher.

    The lexical router stays registered as the offline fallback; it must not
    be consulted when a real provider is configured.
    """
    from shadow.capture.schema import Binding, ToolCatalog, ToolSpec, ToolStep
    from shadow.bench.tasks import Task
    from shadow.route import agent as agent_mod
    from shadow.route.agent import run_tool_task

    spec = ToolSpec(
        name="list_records", description="List records.", mutation_class="read",
        verified=True, support=9,
        params_schema={"type": "object", "required": ["doctype"],
                       "properties": {"doctype": {"type": "string"}}},
        steps=[ToolStep(method="GET", path_template="/api/resource/{p0}",
                        path_bindings={"p0": Binding(kind="user_param",
                                                     param_name="doctype")})])

    replies = [json.dumps({"action": "tool", "name": "list_records",
                           "arguments": {"doctype": "Bin"}}),
               json.dumps({"action": "done", "answer": "250"})]
    seen_prompts: list[str] = []
    module = types.ModuleType("litellm")

    def completion(model: str, messages: list[dict], **kw: Any):
        seen_prompts.append(json.dumps(messages))
        return _Response(replies[min(len(seen_prompts) - 1, len(replies) - 1)],
                         _Usage(prompt=500, completion=10, cached=0))

    module.completion = completion
    module.token_counter = lambda **kw: 5
    monkeypatch.setitem(sys.modules, "litellm", module)

    def explode(*_args, **_kw):  # the lexical router must not run
        raise AssertionError("lexical router used while a provider is configured")

    monkeypatch.setattr(agent_mod, "select_tool", explode)

    class _Executor:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict]] = []

        def execute(self, tool_spec, params):
            from shadow.serve.executor import ExecutionResult

            self.calls.append((tool_spec.name, params))
            return ExecutionResult(tool=tool_spec.name, ok=True,
                                   values=[{"data": []}], value={"data": []})

    executor = _Executor()
    task = Task(id="T03_stock_on_hand#0", template_id="T03_stock_on_hand",
                goal="What is the total actual quantity in stock for item 'X'?",
                kind="read", check="stock_on_hand", params={"item_code": "X"})
    result = run_tool_task(task, ToolCatalog(tools=[spec]),
                           client=make_client("claude-haiku-4-5-20251001", "litellm"),
                           executor=executor)

    # The model picked the tool and the arguments, from the rendered catalog.
    assert executor.calls == [("list_records", {"doctype": "Bin"})]
    assert result.answer == "250"
    assert all(not step.usage.simulated for step in result.steps)
    # And the catalog it chose from was in the cacheable system message.
    assert "list_records" in seen_prompts[0]
