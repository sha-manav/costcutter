"""Condition A — the baseline. An agent that can only drive the browser.

Every model call is instrumented: input tokens, cached tokens, output
tokens, wall clock. Cost is computed later from the config table, by the
same code that scores condition B.

Policy sources:
  * a real model, via litellm, when credentials are present
  * a deterministic recipe (route/recipes.py) otherwise

The deterministic policy still builds and tokenises the *real* prompt — the
observation the model would have read — so token accounting is measured
rather than assumed. It is also a stronger baseline than an LLM: it never
explores, never backtracks, and uses composite field actions. Any advantage
condition B shows over it is therefore a lower bound.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable

from shadow.browser import browser_page, login
from shadow.config import Config, get_config
from shadow.llm import LLMClient, LLMUsage, make_client, register_policy
from shadow.route.actions import ACTION_SCHEMA, perform
from shadow.route.observation import Observation, snapshot

SYSTEM_PROMPT = """You are an agent operating an ERP web application through a browser.
You see an accessibility snapshot of the current page: a list of interactive
elements with refs, and the page text. Work towards the user's goal one action
at a time. When the goal is a question, finish with the answer; when it is a
change, make the change and then finish.

""" + ACTION_SCHEMA


@dataclass
class StepRecord:
    index: int
    action: dict[str, Any]
    ok: bool
    detail: str
    usage: LLMUsage
    wall_s: float
    prompt_chars: int
    served_by: str = "browser"

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index, "action": self.action, "ok": self.ok,
            "detail": self.detail[:300], "usage": self.usage.to_dict(),
            "wall_s": round(self.wall_s, 3), "prompt_chars": self.prompt_chars,
            "served_by": self.served_by,
        }


@dataclass
class RunResult:
    task_id: str
    template_id: str
    condition: str
    answer: str | None = None
    steps: list[StepRecord] = field(default_factory=list)
    wall_s: float = 0.0
    error: str | None = None
    finished: bool = False

    @property
    def usage(self) -> LLMUsage:
        total = LLMUsage(model=self.steps[0].usage.model if self.steps else "")
        for s in self.steps:
            total = total + s.usage
        return total

    @property
    def tool_actions(self) -> int:
        return sum(1 for s in self.steps if s.served_by == "tool")

    @property
    def browser_actions(self) -> int:
        return sum(1 for s in self.steps if s.served_by != "tool")

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id, "template_id": self.template_id,
            "condition": self.condition, "answer": self.answer,
            "steps": [s.to_dict() for s in self.steps],
            "n_steps": len(self.steps), "wall_s": round(self.wall_s, 3),
            "usage": self.usage.to_dict(), "error": self.error,
            "finished": self.finished,
            "tool_actions": self.tool_actions,
            "browser_actions": self.browser_actions,
        }


# --------------------------------------------------------------------------
# Deterministic policy plumbing
# --------------------------------------------------------------------------

def _driver_policy(messages: list[dict[str, Any]], ctx: dict[str, Any]) -> str:
    """Offline stand-in: ask the run's driver for the next action."""
    driver: Callable[[], dict[str, Any]] = ctx["driver"]
    return json.dumps(driver())


register_policy("browser_agent", _driver_policy)


class RecipeDriver:
    """Steps a recipe generator, feeding it the latest observation."""

    def __init__(self, recipe_factory, params: dict[str, Any]) -> None:
        self.gen = recipe_factory(params)
        self.started = False
        self.last_obs: Observation | None = None

    def set_observation(self, obs: Observation) -> None:
        self.last_obs = obs

    def __call__(self) -> dict[str, Any]:
        try:
            if not self.started:
                self.started = True
                return next(self.gen)
            return self.gen.send(self.last_obs)
        except StopIteration:
            return {"action": "done", "answer": ""}


# --------------------------------------------------------------------------
# Prompt construction (identical shape for both providers)
# --------------------------------------------------------------------------

def build_messages(goal: str, obs: Observation,
                   history: list[StepRecord]) -> list[dict[str, Any]]:
    recent = history[-3:]
    hist_lines = [f"{s.index}: {json.dumps(s.action)} -> "
                  f"{'ok' if s.ok else 'FAILED: ' + s.detail[:120]}"
                  for s in recent]
    user = (f"GOAL: {goal}\n\n"
            f"RECENT ACTIONS:\n" + ("\n".join(hist_lines) or "(none)") +
            f"\n\nCURRENT PAGE:\n{obs.render()}")
    return [{"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user}]


def parse_action(text: str) -> dict[str, Any]:
    payload = text.strip()
    if payload.startswith("```"):
        payload = payload.split("\n", 1)[-1].rsplit("```", 1)[0]
    start, end = payload.find("{"), payload.rfind("}")
    if start == -1 or end <= start:
        raise ValueError(f"no JSON action in model reply: {text[:200]!r}")
    return json.loads(payload[start:end + 1])


# --------------------------------------------------------------------------
# The loop
# --------------------------------------------------------------------------

def run_browser_task(task, cfg: Config | None = None, client: LLMClient | None = None,
                     recipe_factory=None, page: Any = None) -> RunResult:
    """Run one task in condition A.

    `page` may be supplied by the caller (so a whole trial can share one
    browser); otherwise a browser is launched for this task alone.
    """
    import time

    cfg = cfg or get_config()
    client = client or make_client(cfg.models.agent, cfg.models.provider)
    result = RunResult(task_id=task.id, template_id=task.template_id,
                       condition="A_browser")
    driver = RecipeDriver(recipe_factory, task.params) if recipe_factory else None
    if client.provider == "offline" and driver is None:
        result.error = ("offline provider needs a recipe for this template; "
                        "supply credentials to run with a real model")
        return result

    t0 = time.time()
    own_page = page is None
    ctx = browser_page(cfg) if own_page else _null_context(page)
    with ctx as pg:
        try:
            login(pg, cfg)
            pg.goto(f"{cfg.app.base_url}/app", wait_until="domcontentloaded")
            obs = snapshot(pg)
            for i in range(cfg.bench.max_steps):
                if driver is not None:
                    driver.set_observation(obs)
                messages = build_messages(task.goal, obs, result.steps)
                step_t0 = time.time()
                resp = client.complete(messages, policy="browser_agent",
                                       policy_context={"driver": driver})
                try:
                    action = parse_action(resp.text)
                except ValueError as exc:
                    action = {"action": "invalid", "error": str(exc)}
                outcome = perform(pg, obs, action, cfg)
                if outcome.observation is not None:
                    obs = outcome.observation
                result.steps.append(StepRecord(
                    index=i, action=action, ok=outcome.ok, detail=outcome.detail,
                    usage=resp.usage, wall_s=time.time() - step_t0,
                    prompt_chars=sum(len(m["content"]) for m in messages)))
                if outcome.done:
                    result.answer = outcome.answer
                    result.finished = True
                    break
        except Exception as exc:
            result.error = f"{type(exc).__name__}: {exc}"
    result.wall_s = time.time() - t0
    return result


class _null_context:
    def __init__(self, page: Any) -> None:
        self.page = page

    def __enter__(self) -> Any:
        return self.page

    def __exit__(self, *exc: Any) -> None:
        return None
