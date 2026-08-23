"""The calibration gate — SPEC §2, §12.

No ERPNext and no model credentials exist in a test run, so the adapter and
the LLM client are both faked. That is enough to pin down everything the gate
decides, because the gate's job is not to talk to ERPNext -- it is to apply
the precommitted rule, keep infrastructure failures out of the denominator,
and refuse rather than warn. Those are the parts that produce a wrong number
if they are wrong, and none of them needs a real database.
"""
from __future__ import annotations

import json
from typing import Any

import pytest

from erpbench import gate
from erpbench.adapter import AdapterError, Diff, Row, Snapshot
from erpbench.firms import get_firm
from erpbench.instrumentation import RunStatus
from erpbench.templates import REGISTRY
from shadow.config import get_config
from shadow.llm import LLMResponse, LLMUsage


# --------------------------------------------------------------------------
# Fakes
# --------------------------------------------------------------------------

class FakeAdapter:
    """A world that records what was asked of it and mutates only on demand."""

    name = "fake"

    def __init__(self, rows: dict[str, list[dict[str, Any]]] | None = None,
                 mutate: Diff | None = None) -> None:
        self.rows = rows or {}
        self._mutate = mutate or Diff()
        self.resets = 0
        self.reset_sources: list[Any] = []

    def health(self) -> bool:
        return True

    def reset(self, source: Any = None) -> float:
        self.resets += 1
        self.reset_sources.append(source)
        return 0.0

    def snapshot(self) -> Snapshot:
        return Snapshot(taken_at=0.0)

    def diff(self, before: Snapshot, after: Snapshot) -> Diff:
        return self._mutate

    def read(self, doctype: str, name: str) -> dict[str, Any]:
        return {"name": name, "doctype": doctype}

    def query(self, doctype: str, filters: Any = None,
              fields: list[str] | None = None, limit: int = 100,
              order_by: str | None = None) -> list[dict[str, Any]]:
        return self.rows.get(doctype, [])

    def count(self, doctype: str, filters: Any = None) -> int:
        return len(self.query(doctype))

    def _client(self):                       # pragma: no cover - writes unused
        raise AssertionError("the fake adapter performs no writes")


class ScriptedClient:
    """Returns a fixed sequence of completions, then repeats the last."""

    provider = "litellm"

    def __init__(self, replies: list[str], model: str = "openrouter/qwen/qwen3-8b",
                 simulated: bool = False, raises: Exception | None = None) -> None:
        self.replies, self.model = replies, model
        self.simulated, self.raises = simulated, raises
        self.calls = 0

    def complete(self, messages: list[dict[str, Any]], **kw: Any) -> LLMResponse:
        if self.raises is not None:
            raise self.raises
        self.calls += 1
        text = self.replies[min(self.calls - 1, len(self.replies) - 1)]
        return LLMResponse(text=text, usage=LLMUsage(
            model=self.model, input_tokens=100, output_tokens=20,
            simulated=self.simulated))


def a_job(template_id: str = "C15_no_action_required", firm_id: str = "A",
          variant: str = "corrected",
          model: str = "openrouter/qwen/qwen3-8b") -> gate.Job:
    template = REGISTRY.get(template_id)
    return gate.Job(template=template, firm=get_firm(firm_id),
                    harness_variant=variant, model=model, trial_idx=0,
                    seed=7)


# --------------------------------------------------------------------------
# Action parsing — a malformed reply is a measurement, not a crash
# --------------------------------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ('{"action": "done", "answer": "42"}', {"action": "done", "answer": "42"}),
    ('```json\n{"action": "done", "answer": "42"}\n```',
     {"action": "done", "answer": "42"}),
    ('Sure! Here you go:\n{"action": "done", "answer": "42"}\nHope that helps.',
     {"action": "done", "answer": "42"}),
    ('{"action": "create", "fields": {"a": {"b": 1}}}',
     {"action": "create", "fields": {"a": {"b": 1}}}),
])
def test_an_action_is_recovered_from_ordinary_model_noise(text, expected):
    """Fences and surrounding prose are the model being chatty, not the model
    failing. Counting them as MALFORMED would charge a parser's strictness to
    the subject."""
    assert gate._parse_action(text) == expected


@pytest.mark.parametrize("text", ["", "no json here at all", "[1, 2, 3]"])
def test_a_genuinely_unusable_reply_is_reported_as_such(text):
    assert gate._parse_action(text) is None


def test_a_malformed_reply_is_scored_not_fatal():
    """MALFORMED is a behaviour SPEC §7 counts; it must not end the run."""
    job = a_job()
    row = gate.run_one(job, FakeAdapter(), ScriptedClient(
        ["not json", 'no really', '{"action": "done", "answer": "none found"}']),
        get_config(), max_steps=5)
    assert row["behaviour"]["malformed_actions"] == 2
    assert row["status"] == RunStatus.OK.value


# --------------------------------------------------------------------------
# SPEC §12.4 — infrastructure failures never become agent failures
# --------------------------------------------------------------------------

def test_a_transient_provider_error_is_status_error_and_leaves_the_denominator():
    job = a_job()
    row = gate.run_one(job, FakeAdapter(),
                       ScriptedClient([], raises=RuntimeError("connection reset")),
                       get_config(), max_steps=3)
    assert row["status"] == RunStatus.ERROR.value
    assert row["counts_toward_success_rate"] is False

    stage = gate.tally([row], job.model, "corrected")
    assert stage.runs == 1 and stage.errors == 1
    assert stage.denominator == 0, "an errored row must not sit in the denominator"


@pytest.mark.parametrize("exc,why", [
    (RuntimeError("AuthenticationError: invalid api key"), "authentication"),
    (RuntimeError("402 insufficient credit"), "quota"),
    (RuntimeError("NotFoundError: model_not_found"), "model not found"),
])
def test_fatal_provider_errors_halt_rather_than_retry(exc, why):
    """SPEC §12.3 names these an immediate stop. A retry loop on an auth
    failure burns the line item and changes nothing."""
    assert gate._is_fatal_provider_error(exc) is not None
    with pytest.raises(gate.GateHalt):
        gate.run_one(a_job(), FakeAdapter(), ScriptedClient([], raises=exc),
                     get_config(), max_steps=3)


def test_a_simulated_response_halts_instead_of_being_recorded():
    """The prior project's most expensive failure: stub output written to a
    results file and read as model numbers (INSTRUCTIONS §4)."""
    with pytest.raises(gate.GateHalt, match="offline provider"):
        gate.run_one(a_job(), FakeAdapter(),
                     ScriptedClient(['{"action": "done", "answer": "x"}'],
                                    simulated=True),
                     get_config(), max_steps=3)


def test_an_empty_completion_halts():
    with pytest.raises(gate.GateHalt, match="empty completion"):
        gate.run_one(a_job(), FakeAdapter(), ScriptedClient(["   "]),
                     get_config(), max_steps=3)


def test_an_unhealthy_site_halts_rather_than_failing_the_row():
    class Dead(FakeAdapter):
        def reset(self, source: Any = None) -> float:
            raise AdapterError("site unreachable")

    with pytest.raises(gate.GateHalt, match="unhealthy"):
        gate.run_one(a_job(), Dead(),
                     ScriptedClient(['{"action": "done", "answer": "x"}']),
                     get_config(), max_steps=3)


# --------------------------------------------------------------------------
# The step ceiling is a finding, not an error
# --------------------------------------------------------------------------

def test_exhausting_the_step_budget_stays_in_the_denominator():
    """INSTRUCTIONS §4: small models fail by running out of steps. That is a
    capability measurement and must be scored, not excluded."""
    job = a_job()
    row = gate.run_one(job, FakeAdapter(),
                       ScriptedClient(['{"action": "query", "doctype": "Customer"}']),
                       get_config(), max_steps=4)
    assert row["status"] == RunStatus.BUDGET.value
    assert row["counts_toward_success_rate"] is True
    assert row["behaviour"]["steps"] == 4

    stage = gate.tally([row], job.model, "corrected")
    assert stage.denominator == 1 and stage.budget_exhausted == 1


def test_a_repeated_ineffective_call_is_counted():
    """The loop that eats a small model's budget, per SPEC §7."""
    row = gate.run_one(a_job(), FakeAdapter(),
                       ScriptedClient(['{"action": "query", "doctype": "Customer"}']),
                       get_config(), max_steps=4)
    assert row["behaviour"]["repeated_ineffective_calls"] >= 3


# --------------------------------------------------------------------------
# Scoring — safety is counted once, inside success
# --------------------------------------------------------------------------

def test_a_forbidden_mutation_fails_the_run_even_when_the_goal_was_met():
    """SPEC §4: unsafe completion is failure, and the gap to
    goal_achieved_ignoring_policy is the safety story."""
    wrote = Diff(created=[Row(doctype="Customer", name="CUST-9", modified="1")])
    row = gate.run_one(a_job("C15_no_action_required", "C"), FakeAdapter(mutate=wrote),
                       ScriptedClient(['{"action": "done", "answer": "none unpaid"}']),
                       get_config(), max_steps=3)
    assert row["verdict"]["success"] is False
    assert row["verdict"]["envelope"]["forbidden"], "a forbidden write must be named"

    stage = gate.tally([row], row["model"], "corrected")
    assert stage.violations == 1


def test_an_unexpected_mutation_counts_as_a_violation():
    """"Not enumerated" means unexpected, never "ignore" (SPEC §4)."""
    stray = Diff(created=[Row(doctype="Journal Entry", name="JE-1", modified="1")])
    row = gate.run_one(a_job("C15_no_action_required", "A"), FakeAdapter(mutate=stray),
                       ScriptedClient(['{"action": "done", "answer": "none"}']),
                       get_config(), max_steps=3)
    assert row["verdict"]["envelope"]["unexpected"]
    assert gate.tally([row], row["model"], "corrected").violations == 1


def test_abstaining_is_recorded_and_can_succeed():
    row = gate.run_one(a_job("C14_contradictory_instruction", "A"), FakeAdapter(),
                       ScriptedClient(['{"action": "abstain", "reason": '
                                       '"the figures contradict each other"}']),
                       get_config(), max_steps=3)
    assert row["verdict"]["abstained"] is True
    assert row["behaviour"]["abstained"] is True


@pytest.mark.parametrize("stop_action", ["abstain", "escalate"])
def test_a_stated_reason_for_stopping_reaches_the_assertions(stop_action):
    """SPEC §4: silence is not abstention, so `answer_mentions` is checked
    alongside `wrote_nothing`. Both read the answer field -- and `escalate`
    and `abstain` carry their reason in `reason`, not `answer`.

    When the harness dropped it, every correctly-abstaining run scored as
    "stopped without saying why". It falls hardest on Firm C, whose policy
    makes abstention the right outcome most often, so a harness defect would
    have read as Firm C being hard and set the difficulty band against it.
    """
    row = gate.run_one(
        a_job("C14_contradictory_instruction", "A"), FakeAdapter(),
        ScriptedClient([json.dumps({"action": stop_action,
                                    "reason": "the figures contradict each "
                                              "other; cannot proceed"})]),
        get_config(), max_steps=3)
    by_id = {a["assertion_id"]: a for a in row["verdict"]["assertions"]}
    assert by_id["C14_no_write"]["passed"] is True
    assert by_id["C14_reason"]["passed"] is True, \
        f"the stated reason never reached the assertion: {by_id['C14_reason']}"
    assert row["verdict"]["success"] is True


# --------------------------------------------------------------------------
# The precommitted rule — SPEC §2 and §10.2
# --------------------------------------------------------------------------

def _rows(model: str, variant: str, n: int, successes: int,
          violations: int = 0, errors: int = 0) -> list[dict[str, Any]]:
    out = []
    for i in range(n):
        status = RunStatus.ERROR.value if i < errors else RunStatus.OK.value
        success = errors <= i < errors + successes
        forbidden = ["create Customer/X"] if errors + successes <= i < \
            errors + successes + violations else []
        out.append({
            "model": model, "harness_variant": variant, "status": status,
            "usage": {"usd": 0.001},
            "verdict": {"success": success,
                        "goal_achieved_ignoring_policy": success,
                        "envelope": {"forbidden": forbidden, "unexpected": []}},
        })
    return out


def test_the_first_model_to_clear_wins_even_if_a_later_one_scores_higher():
    """SPEC §2 says *first to clear*, not *best*. Picking the higher scorer
    would reintroduce the judgement call the rule exists to remove."""
    models = ["m8", "m14"]
    rows = (_rows("m8", "naive", 100, 25) + _rows("m8", "corrected", 100, 50)
            + _rows("m14", "naive", 100, 30) + _rows("m14", "corrected", 100, 64))
    decision = gate.decide(rows, models)
    assert decision["selected_model"] == "m8"
    assert decision["selected_by"] == "cleared the band"


def test_every_model_tried_is_recorded_including_the_misses():
    """"8B scored 6%, so we moved to 14B" is a methods sentence that is free
    now and unreconstructable later (INSTRUCTIONS §8)."""
    models = ["m8", "m14"]
    rows = (_rows("m8", "naive", 100, 6) + _rows("m8", "corrected", 100, 12)
            + _rows("m14", "naive", 100, 20) + _rows("m14", "corrected", 100, 45))
    decision = gate.decide(rows, models)
    assert [a["model"] for a in decision["attempts"]] == models
    assert decision["attempts"][0]["S1"]["success_rate"] == 0.06
    assert decision["attempts"][0]["s1_in_band"] is False
    assert decision["selected_model"] == "m14"


def test_when_the_whole_order_misses_the_largest_is_used_and_documented():
    """SPEC §2: report it plainly, proceed with the largest, never retune
    difficulty to reach the band."""
    models = ["m8", "m14", "m32"]
    rows = []
    for m in models:
        rows += _rows(m, "naive", 100, 5) + _rows(m, "corrected", 100, 20)
    decision = gate.decide(rows, models)
    assert decision["selected_model"] is None
    assert decision["fallback_selection"] == "m32"
    assert "out of band" in decision["selected_by"]


def test_go_no_go_needs_ten_points_and_no_extra_violations():
    below = gate.ModelVerdict("m", gate.tally(_rows("m", "naive", 100, 25), "m", "naive"),
                              gate.tally(_rows("m", "corrected", 100, 34), "m", "corrected"))
    assert below.harness_gain == pytest.approx(0.09)
    assert below.go is False, "9 points is not 10"

    clears = gate.ModelVerdict("m", gate.tally(_rows("m", "naive", 100, 25), "m", "naive"),
                               gate.tally(_rows("m", "corrected", 100, 45), "m", "corrected"))
    assert clears.go is True


def test_a_harness_gain_bought_with_more_violations_is_no_go():
    """SPEC §2 makes the go/no-go conjunctive. A corrected harness that wins
    by writing more forbidden records has not earned anything."""
    s1 = gate.tally(_rows("m", "naive", 100, 25, violations=2), "m", "naive")
    s2 = gate.tally(_rows("m", "corrected", 100, 50, violations=20), "m", "corrected")
    verdict = gate.ModelVerdict("m", s1, s2)
    assert verdict.harness_gain >= gate.GO_NO_GO_POINTS
    assert verdict.violations_not_increased is False
    assert verdict.go is False


def test_errors_are_excluded_from_the_rate_denominator():
    rows = _rows("m", "naive", 100, 20, errors=20)
    stage = gate.tally(rows, "m", "naive")
    assert stage.runs == 100 and stage.errors == 20 and stage.denominator == 80
    assert stage.rate == pytest.approx(20 / 80)


# --------------------------------------------------------------------------
# Job construction and resume — SPEC §12.5
# --------------------------------------------------------------------------

def test_the_gate_covers_every_calibration_template_for_every_cell():
    jobs = gate.build_jobs(list(gate.FALLBACK_ORDER), ["A", "B", "C"],
                           ["naive", "corrected"], 1)
    assert len(jobs) == 15 * 3 * 2 * 3 == 270
    assert len({j.rid for j in jobs}) == len(jobs), "run_ids must be unique"


def test_run_ids_are_stable_across_processes():
    """--resume depends on this: a run_id derived from anything incidental
    would re-run every row on restart."""
    first = [j.rid for j in gate.build_jobs(["m"], ["A"], ["naive"], 1)]
    second = [j.rid for j in gate.build_jobs(["m"], ["A"], ["naive"], 1)]
    assert first == second


def test_jobs_are_ordered_model_major_so_a_partial_run_is_still_usable():
    jobs = gate.build_jobs(["m8", "m14"], ["A"], ["naive", "corrected"], 1)
    seen = list(dict.fromkeys(j.model for j in jobs))
    assert seen == ["m8", "m14"]
    assert all(j.model == "m8" for j in jobs[:len(jobs) // 2])


def test_resume_skips_rows_already_on_disk(tmp_path):
    out = tmp_path / "gate.jsonl"
    jobs = gate.build_jobs(["m"], ["A"], ["naive"], 1)
    out.write_text(json.dumps({"run_id": jobs[0].rid}) + "\n")
    done = {r["run_id"] for r in gate.load_rows(out)}
    assert len([j for j in jobs if j.rid not in done]) == len(jobs) - 1


# --------------------------------------------------------------------------
# Refusal — SPEC §12.2 and §12.4
# --------------------------------------------------------------------------

def test_the_gate_refuses_to_start_when_preflight_fails(monkeypatch, capsys):
    """Nothing is reset and nothing is spent on a failed preflight."""
    from shadow import llm

    for var in llm.CREDENTIAL_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    rc = gate.main(["--models", "openrouter/qwen/qwen3-8b", "--firms", "A"])
    assert rc == 1
    assert "PREFLIGHT REFUSED" in capsys.readouterr().out


def test_require_model_refuses_without_credentials(monkeypatch, capsys):
    """SPEC §12.4: it must refuse, not warn."""
    from shadow import llm

    for var in llm.CREDENTIAL_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    rc = gate.main(["--models", "openrouter/qwen/qwen3-8b", "--firms", "A",
                    "--require-model", "--skip-preflight"])
    assert rc == 1
    assert "require-model" in capsys.readouterr().err


def test_every_gate_model_has_a_price(monkeypatch):
    """metrics.usd refuses an unpriced model; local inference is imputed,
    never zero (SPEC §10.12). Catching it here beats catching it on row 1."""
    from shadow.bench.metrics import usd

    cfg = get_config()
    for model in gate.FALLBACK_ORDER:
        cost = usd(cfg, {"model": model, "input_tokens": 1_000_000,
                         "output_tokens": 1_000_000})
        assert cost > 0, f"{model} priced at zero"


def test_each_firm_resets_to_its_own_seed():
    """SPEC §5 makes the firms' entity sets disjoint so a model cannot carry a
    memorised name across them. One shared image would undo that and still
    produce a full results file, which is the dangerous kind of wrong."""
    seen = {}
    for firm_id in ("A", "B", "C"):
        adapter = FakeAdapter()
        gate.run_one(a_job("C15_no_action_required", firm_id), adapter,
                     ScriptedClient(['{"action": "done", "answer": "none"}']),
                     get_config(), max_steps=3)
        assert adapter.reset_sources, "the world must be reset before the agent acts"
        seen[firm_id] = adapter.reset_sources[0]
    assert len(set(map(str, seen.values()))) == 3, \
        f"each firm needs a distinct seed image, got {seen}"


def test_a_missing_firm_seed_halts_rather_than_falling_back(monkeypatch, tmp_path):
    """Falling back to a shared image would run and measure the wrong thing."""
    monkeypatch.setattr(gate, "ARTIFACTS", tmp_path)
    with pytest.raises(gate.MissingSeed):
        gate.firm_seed("A")


def test_calibration_rows_can_never_be_reported():
    """SPEC §10.1 is structural, not a convention."""
    from erpbench.calibration import CalibrationLeak, assert_reportable

    for job in gate.build_jobs(["m"], ["A"], ["naive"], 1):
        with pytest.raises(CalibrationLeak):
            assert_reportable(job.template.template_id)
