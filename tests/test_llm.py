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

import shadow.llm
from shadow.config import get_config
from shadow.llm import (
    LiteLLMClient, LLMUsage, make_client, mark_cacheable, resolve_model_provider,
    supports_prompt_caching,
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
    # A cache write is neither a fresh input token nor a cached read: it is
    # billed above the input rate, so it is counted on its own.
    assert usage.cache_write_tokens == 300
    assert usage.input_tokens == 0
    assert usage.total_input_tokens == 1000


def test_offline_call_site_kwargs_never_reach_the_provider(stub_litellm):
    LiteLLMClient("claude-haiku-4-5-20251001").complete(
        [{"role": "user", "content": "U"}], policy="tool_agent",
        policy_context={"router": None})
    kwargs = stub_litellm[0]["kwargs"]
    # The assertion is about the offline call-site keys, not about the call
    # carrying no kwargs at all -- transport concerns like num_retries are
    # allowed through.
    assert "policy" not in kwargs and "policy_context" not in kwargs


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


def test_every_request_carries_a_timeout(stub_litellm):
    """A dropped-but-not-closed connection blocks forever otherwise. A 270-row
    run hung at row 231 on an ESTABLISHED socket: 8 hours elapsed, 1m45s of
    CPU, no progress and no halt — the one failure the halt machinery cannot
    report, because nothing returns to report it."""
    LiteLLMClient("openrouter/qwen/qwen3-8b").complete(
        [{"role": "user", "content": "go"}])
    assert stub_litellm[0]["kwargs"]["timeout"] > 0


def test_an_explicit_timeout_is_not_overridden(stub_litellm):
    LiteLLMClient("openrouter/qwen/qwen3-8b").complete(
        [{"role": "user", "content": "go"}], timeout=7)
    assert stub_litellm[0]["kwargs"]["timeout"] == 7


def test_provider_selection():
    assert make_client("claude-haiku-4-5-20251001", "offline").provider == "offline"
    assert make_client("claude-haiku-4-5-20251001", "litellm").provider == "litellm"


@pytest.mark.parametrize("env_var", shadow.llm.CREDENTIAL_ENV_VARS)
def test_every_credentialed_provider_routes_to_a_real_model(monkeypatch, env_var):
    """A key that is set must never leave `auto` on the offline stub.

    INSTRUCTIONS §4 records `provider: auto` silently falling back to a stub
    and producing numbers that read as model numbers. OPENROUTER_API_KEY was
    absent from the credential list, so the calibration gate -- whose Qwen
    models are reachable only through OpenRouter -- would have run its 270
    rows against the stub with nothing but a stderr line to say so. This is
    parameterised over the whole list so adding a provider without teaching
    `auto` about it fails here rather than in a results file.
    """
    for var in shadow.llm.CREDENTIAL_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv(env_var, "probe-value-not-a-real-key")

    assert shadow.llm._credentials_present() is True
    assert make_client("openrouter/qwen/qwen3-8b", "auto").provider == "litellm"
    assert resolve_model_provider("openrouter/qwen/qwen3-8b", "litellm") == "litellm"


def test_no_credentials_still_refuses_under_require_model(monkeypatch):
    """`--require-model` refuses rather than warns (SPEC §12.4)."""
    for var in shadow.llm.CREDENTIAL_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    with pytest.raises(shadow.llm.MissingCredentials):
        resolve_model_provider("openrouter/qwen/qwen3-8b", "litellm")


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
    # Retrieval is exercised in tests/test_retrieval.py; this test is about
    # who chooses the tool, so the catalog is offered whole.
    result = run_tool_task(task, ToolCatalog(tools=[spec]),
                           client=make_client("claude-haiku-4-5-20251001", "litellm"),
                           executor=executor, tool_k=8, floor=-1.0)

    # The model picked the tool and the arguments, from the rendered catalog.
    assert executor.calls == [("list_records", {"doctype": "Bin"})]
    assert result.answer == "250"
    assert all(not step.usage.simulated for step in result.steps)
    # And the catalog it chose from was in the cacheable system message.
    assert "list_records" in seen_prompts[0]


def test_cache_writes_are_priced_above_the_input_rate():
    """Anthropic bills cache creation at 1.25x input; folding it into
    input_tokens understates the first step of every task."""
    from shadow.bench.metrics import CACHE_WRITE_MULTIPLIER, usd

    cfg = get_config()
    model = "claude-sonnet-5"
    price = cfg.costs[model]
    usage = {"model": model, "input_tokens": 0, "cached_input_tokens": 0,
             "cache_write_tokens": 1_000_000, "output_tokens": 0}
    assert usd(cfg, usage) == pytest.approx(price.input * CACHE_WRITE_MULTIPLIER)


def test_cost_can_be_repriced_without_the_cache_discount():
    """Caching favours the tool agent structurally, so the comparison is
    reported both ways rather than banking the discount silently."""
    from shadow.bench.metrics import usd

    cfg = get_config()
    model = "claude-sonnet-5"
    price = cfg.costs[model]
    usage = {"model": model, "input_tokens": 0, "cached_input_tokens": 1_000_000,
             "cache_write_tokens": 0, "output_tokens": 0}
    assert usd(cfg, usage) == pytest.approx(price.cached_input)
    assert usd(cfg, usage, price_cache_at_full=True) == pytest.approx(price.input)
    assert price.cached_input < price.input


def test_both_cost_rows_reach_the_scored_condition():
    """The report has to be able to state the comparison both ways, so the
    scorer carries the repriced total alongside the billed one."""
    from shadow.bench.metrics import score_condition

    cfg = get_config()
    rows = [{
        "condition": "B_tools", "task_id": "T#0", "template_id": "T",
        "success": True, "wall_s": 1.0, "n_steps": 1, "steps": [],
        "usage": {"model": "claude-sonnet-5", "input_tokens": 100,
                  "cached_input_tokens": 10_000, "cache_write_tokens": 0,
                  "output_tokens": 10},
    }]
    m = score_condition(rows, "B_tools", cfg)
    assert m.cached_input_tokens == 10_000
    # Repricing cache reads upward can only ever cost more, never less.
    assert m.usd_per_successful_task_uncached > m.usd_per_successful_task


def test_a_real_model_never_gets_the_scripted_recipe(stub_litellm):
    """Condition A's validity depends on this.

    The browser agent passes a deterministic recipe driver down as
    policy_context so the offline provider can act without a model. If the
    real client ever consulted it, condition A would quietly become the
    scripted baseline again and the headline comparison would be measuring
    a recipe against tools while claiming to measure a model.
    """
    calls = stub_litellm

    class ExplodingDriver:
        def __getattr__(self, name):
            raise AssertionError(
                "the real client consulted the scripted recipe driver")

    client = LiteLLMClient("claude-sonnet-5")
    resp = client.complete(
        [{"role": "user", "content": "go"}],
        policy="browser_agent",
        policy_context={"driver": ExplodingDriver()})
    assert resp.text is not None
    # And neither the policy name nor its context leaks into the request.
    kwargs = calls[-1]["kwargs"]
    assert "policy" not in kwargs and "policy_context" not in kwargs


def test_failed_runs_still_count_toward_cost_per_success():
    """A condition must not look cheap by failing.

    This became load-bearing once a real model started failing a third of
    condition A's runs: if the numerator summed only successful runs, the
    two failures below would be free and the headline would reward giving
    up early.
    """
    from shadow.bench.metrics import score_condition

    cfg = get_config()

    def row(task: str, ok: bool):
        return {"condition": "A_browser", "task_id": task, "template_id": "T",
                "success": ok, "wall_s": 1.0, "n_steps": 1, "steps": [],
                "usage": {"model": "claude-sonnet-5", "input_tokens": 1_000_000,
                          "cached_input_tokens": 0, "cache_write_tokens": 0,
                          "output_tokens": 0}}

    price = cfg.costs["claude-sonnet-5"].input
    one_pass = score_condition([row("a", True)], "A_browser", cfg)
    assert one_pass.usd_per_successful_task == pytest.approx(price)

    # Same single success, two failures alongside it: three runs paid for,
    # one task delivered.
    with_failures = score_condition(
        [row("a", True), row("b", False), row("c", False)], "A_browser", cfg)
    assert with_failures.usd_per_successful_task == pytest.approx(price * 3)
    assert with_failures.success_rate == pytest.approx(1 / 3)


def test_a_condition_that_never_succeeds_has_unbounded_cost():
    """Not zero, and not a crash -- the charts and tables have to render it."""
    from shadow.bench.metrics import score_condition

    rows = [{"condition": "B_tools", "task_id": "a", "template_id": "T",
             "success": False, "wall_s": 1.0, "n_steps": 1, "steps": [],
             "usage": {"model": "claude-sonnet-5", "input_tokens": 10,
                       "output_tokens": 0}}]
    m = score_condition(rows, "B_tools", get_config())
    assert m.usd_per_successful_task == float("inf")
    assert m.usd_per_successful_task_uncached == float("inf")


def test_transient_provider_errors_are_retried(stub_litellm):
    """A provider 5xx must not be scored as the agent failing the task.

    One Anthropic 500 during the headline run ended a task at zero steps and
    was recorded as a failure, landing on the success rate the benchmark
    exists to measure. Nothing downstream can tell that apart from a genuine
    failure, so it has to be handled at the call.
    """
    LiteLLMClient("claude-sonnet-5").complete([{"role": "user", "content": "U"}])
    assert stub_litellm[0]["kwargs"]["num_retries"] >= 1


def test_a_run_that_spent_nothing_costs_nothing():
    """Scoring must survive a row whose model field never got filled in."""
    from shadow.bench.metrics import MissingCost, usd

    cfg = get_config()
    dead = {"model": "", "input_tokens": 0, "cached_input_tokens": 0,
            "cache_write_tokens": 0, "output_tokens": 0}
    assert usd(cfg, dead) == 0.0
    # But a record that actually consumed tokens still has to be priced.
    with pytest.raises(MissingCost):
        usd(cfg, {"model": "", "input_tokens": 1})


ANTHROPIC_MIN_CACHEABLE_TOKENS = 1024


def _prefix_tokens(text: str) -> int:
    """Local tokenizer count. No network.

    Anthropic's own count runs higher than this one -- a prefix measured at
    1062 here was billed as 1430 -- so treating this number as the budget is
    the conservative direction.
    """
    import litellm

    return litellm.token_counter(
        model="claude-sonnet-5", messages=[{"role": "user", "content": text}])


def test_cacheable_prefixes_clear_the_provider_floor():
    """Anthropic declines to cache a block under ~1024 tokens, silently.

    97 benchmark runs reported cached_input_tokens: 0 with correctly formed
    cache_control blocks, because condition A's prefix was 230 tokens and
    condition B's was 606. There is no error for this -- the request simply
    comes back uncached -- so only an explicit check catches the regression.
    """
    from shadow.route.agent import SYSTEM_PROMPT as B_SYS
    from shadow.route.browser_agent import SYSTEM_PROMPT as A_SYS

    # Condition A's prefix is the whole stable instruction block and must
    # clear the floor on its own: it carries no catalog.
    a_tokens = _prefix_tokens(A_SYS)
    assert a_tokens >= ANTHROPIC_MIN_CACHEABLE_TOKENS, (
        f"condition A prefix is {a_tokens} tokens, under the "
        f"{ANTHROPIC_MIN_CACHEABLE_TOKENS}-token floor; it will not cache")
    # Condition B reaches the floor as instructions plus retrieved catalog,
    # so its instruction block alone may be smaller. It is measured here so
    # that a shrink shows up as a failure rather than as a silent loss of
    # caching at low k.
    assert _prefix_tokens(B_SYS) >= 600


def test_the_cache_breakpoint_covers_one_contiguous_prefix():
    """Caching keys on an exact prefix, so the stable part must come first
    and carry the breakpoint as a single block."""
    from shadow.llm import ANTHROPIC_CACHE_CONTROL

    marked = mark_cacheable(
        [{"role": "system", "content": "S" * 8000},
         {"role": "user", "content": "changes every step"}],
        "claude-sonnet-5")
    system = marked[0]["content"]
    assert isinstance(system, list) and len(system) == 1
    assert system[0]["cache_control"] == ANTHROPIC_CACHE_CONTROL
    # The volatile message must never be marked: it would poison every hit.
    assert isinstance(marked[1]["content"], str)


def test_a_hard_deadline_bounds_a_call_that_ignores_the_read_timeout():
    """litellm's `timeout` is a read timeout: a provider trickling bytes never
    trips it, and one call was seen running 960s against a 120s setting. The
    gate's row deadline cannot help either — it is checked between steps and
    cannot interrupt a call in flight. Only a wall-clock alarm holds."""
    import time as _time

    t0 = _time.time()
    with pytest.raises(shadow.llm.RequestDeadlineExceeded):
        with shadow.llm.hard_deadline(0.3):
            _time.sleep(5)
    assert _time.time() - t0 < 2, "the alarm must fire, not wait out the sleep"


def test_the_deadline_disarms_so_it_cannot_fire_into_later_work():
    """A leaked itimer would raise inside whatever ran next, which is far
    worse than the hang it replaces."""
    import time as _time

    with shadow.llm.hard_deadline(0.3):
        pass
    _time.sleep(0.5)          # would fire here if the timer leaked


def test_a_deadline_breach_is_infrastructure_not_an_agent_failure():
    """SPEC §12.4: it must be status=error and leave the denominator, and it
    must not be mistaken for a fatal auth/quota stop."""
    from erpbench import gate

    exc = shadow.llm.RequestDeadlineExceeded("provider call exceeded 300s")
    assert gate._is_fatal_provider_error(exc) is None, \
        "a slow provider is not an auth failure; it must not halt the run"


def test_the_deadline_survives_a_retry_wrapper_that_swallows_exceptions():
    """litellm wraps calls in a retry decorator that catches Exception. A
    deadline raised as an ordinary error was caught and retried with the
    one-shot itimer already spent, so the alarm fired and the hang continued
    anyway — a bound that appears in the logs and is not in force. It must be
    a BaseException, in the same category as KeyboardInterrupt."""
    import time as _time

    assert not issubclass(shadow.llm.RequestDeadlineExceeded, Exception), \
        "an Exception subclass is catchable by the retry wrapper it must escape"

    def swallowing_retry_loop():
        for _ in range(5):
            try:
                _time.sleep(2)
            except Exception:
                continue

    t0 = _time.time()
    with pytest.raises(shadow.llm.RequestDeadlineExceeded):
        with shadow.llm.hard_deadline(0.4):
            swallowing_retry_loop()
    assert _time.time() - t0 < 2, "the deadline did not escape the retry loop"


def test_the_gate_catches_the_deadline_explicitly():
    """Because it is a BaseException, a generic `except Exception` no longer
    sees it — every call site that must survive one has to name it."""
    import inspect

    from erpbench import gate

    src = inspect.getsource(gate.run_one)
    assert "except RequestDeadlineExceeded" in src, \
        "run_one must name the deadline, or it escapes and kills the run"
