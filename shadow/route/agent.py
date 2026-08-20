"""Condition B — tools first, browser as the fallback of last resort.

Same loop, same accounting, same oracle as condition A. The only difference
is what the agent can do: synthesized tools are offered first, and the
browser is exposed as a single `browser_task(goal)` tool. Every action is
labelled with what served it, which is where the coverage number comes from.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from typing import Any

from shadow.browser import browser_page, login
from shadow.capture.schema import ToolCatalog, ToolSpec
from shadow.config import Config, get_config
from shadow.llm import LLMClient, LLMUsage, make_client, register_policy
from shadow.route.browser_agent import RunResult, StepRecord, RecipeDriver
from shadow.route.observation import snapshot
from shadow.serve.executor import ExecutionResult, ToolExecutor
from shadow.serve.server import tool_description

SYSTEM_PROMPT = """You are an agent operating an ERP system.

You have tools that were synthesized by watching a person use the app. Prefer
them: they are far cheaper and faster than the browser. `browser_task` drives
the UI and should only be used when no tool fits.

Reply with one JSON object and nothing else:
  {"action": "tool", "name": "<tool name>", "arguments": {...}}
  {"action": "browser_task", "goal": "<what to do in the UI>"}
  {"action": "done", "answer": "<final answer, or a short confirmation>"}
"""

QUOTED_RE = re.compile(r"'([^']+)'|\"([^\"]+)\"")
NUMBER_RE = re.compile(r"\b\d+(?:\.\d+)?\b")
STOPWORDS = {"the", "a", "an", "of", "for", "with", "and", "in", "to", "is",
             "what", "how", "many", "only", "answer", "number", "count",
             "total", "all", "across", "record", "records", "system", "erp",
             "leave", "it", "as", "draft", "new", "create", "change"}


def tokens(text: str) -> set[str]:
    return {w for w in re.split(r"\W+", text.lower()) if w and w not in STOPWORDS}


# --------------------------------------------------------------------------
# Prompt
# --------------------------------------------------------------------------

def render_tools(tools: list[ToolSpec]) -> str:
    lines = []
    for spec in tools:
        lines.append(f"- {spec.name}: {tool_description(spec)}")
        lines.append(f"    schema: {json.dumps(spec.params_schema)}")
    lines.append("- browser_task: Drive the web UI to accomplish a goal in "
                 "natural language. Expensive; last resort.")
    return "\n".join(lines)


def build_messages(goal: str, tools: list[ToolSpec],
                   history: list[StepRecord]) -> list[dict[str, Any]]:
    hist = []
    for s in history[-3:]:
        detail = s.detail if len(s.detail) < 1200 else s.detail[:1200] + "…"
        hist.append(f"{s.index}: {json.dumps(s.action)} -> {detail}")
    user = (f"GOAL: {goal}\n\nAVAILABLE TOOLS:\n{render_tools(tools)}\n\n"
            f"HISTORY:\n" + ("\n".join(hist) or "(none)"))
    return [{"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user}]


# --------------------------------------------------------------------------
# Deterministic router (offline provider)
#
# A lexical matcher, deliberately naive: it knows nothing about the task
# templates and has no mapping from goals to tools. It therefore measures a
# LOWER BOUND on the coverage an LLM router would achieve.
# --------------------------------------------------------------------------

@dataclass
class Candidate:
    spec: ToolSpec
    score: float
    arguments: dict[str, Any]


def goal_literals(goal: str) -> list[str]:
    out = []
    for m in QUOTED_RE.finditer(goal):
        out.append(m.group(1) or m.group(2))
    return out


def _guess_doctype(goal: str, options: list[str]) -> str | None:
    """Pick the option whose words the goal actually mentions."""
    g = tokens(goal)
    best, best_score = None, 0.0
    for option in options:
        opt_tokens = tokens(str(option))
        if not opt_tokens:
            continue
        overlap = len(opt_tokens & g) / len(opt_tokens)
        if overlap > best_score:
            best, best_score = option, overlap
    return best if best_score >= 0.99 else None


# "... for the customer 'Acme Industrial'" -> ("customer", "Acme Industrial").
# The word in front of a quoted value names the field it belongs to often
# enough to be worth trying, and a wrong guess costs one failed call.
ENTITY_RE = re.compile(r"(\w+)\s+['\"]([^'\"]+)['\"]")


def entity_hints(goal: str) -> list[tuple[str, str]]:
    return [(word.lower(), value) for word, value in ENTITY_RE.findall(goal)
            if word.lower() not in {"the", "a", "an", "for", "of", "with", "named"}]


def _record_type_from_goal(goal: str, options: list[str]) -> str | None:
    """Name the record type the goal is about.

    First try the values this parameter was observed with; then the noun in
    front of the first quoted value. Both are guesses — when neither lands
    the router falls back to the browser, which is the honest outcome for a
    lexical matcher with no model behind it.
    """
    guess = _guess_doctype(goal, options)
    if guess is not None:
        return guess
    hints = entity_hints(goal)
    if hints:
        return hints[0][0].capitalize()
    return None


def fill_arguments(spec: ToolSpec, goal: str) -> dict[str, Any] | None:
    """Fill a tool's parameters from the goal text alone.

    No task metadata, no mapping from templates to tools. Array-valued
    parameters get generic values rather than the observed example, because
    replaying another record type's column list is a guaranteed error.
    """
    props: dict[str, Any] = spec.params_schema.get("properties", {})
    required: list[str] = spec.params_schema.get("required", [])
    literals = goal_literals(goal)
    numbers = [float(n) for n in NUMBER_RE.findall(goal)]
    args: dict[str, Any] = {}
    record_type: str | None = None

    for name, schema in props.items():
        lname = name.lower()
        options = [str(v) for v in (schema.get("enum") or schema.get("examples") or [])]
        if "doctype" in lname or "record_type" in lname:
            record_type = _record_type_from_goal(goal, options)
            if record_type is not None:
                args[name] = record_type
            continue
        if schema.get("type") == "array":
            hints = entity_hints(goal)
            if "field" in lname:
                hinted = next((f for hint, f in _FIELD_HINTS if hint in goal.lower()),
                              None)
                args[name] = (["name"] + ([hinted] if hinted else [])
                              + [h[0] for h in hints])
            else:
                args[name] = [[field, "=", value] for field, value in hints]
            continue
        if "order_by" in lname:
            args[name] = "modified desc"
            continue
        guess = _guess_doctype(goal, options) if options else None
        if guess is not None:
            args[name] = guess
            continue
        if schema.get("type") in {"integer", "number"} and numbers:
            args[name] = numbers[-1]
            continue
        if literals and schema.get("type", "string") == "string":
            args[name] = literals[0]
            continue
    missing = [r for r in required if r not in args]
    if missing:
        return None
    return args


MUTATING_VERBS = {"create", "add", "update", "change", "set", "delete",
                  "remove", "cancel", "submit", "make", "register", "reclassify"}


def goal_intent(goal: str) -> str:
    """Whether the goal asks a question or asks for a change."""
    head = re.split(r"\W+", goal.lower())[:3]
    return "write" if any(w in MUTATING_VERBS for w in head) else "read"


def tool_surface(spec: ToolSpec) -> set[str]:
    """The words a router can see: name, description, and schema.

    The schema matters most for a generic tool — `list_records` says nothing
    about sales orders, but its `doctype` parameter was observed with them.
    """
    parts = [spec.name.replace("_", " "), spec.description]
    for name, schema in spec.params_schema.get("properties", {}).items():
        parts.append(name.replace("_", " "))
        # Enum values only: they are the parameter's declared domain.
        # Example values are incidental, and matching on them lets a tool win
        # a goal just because a customer name happened to be in its capture.
        for value in (schema.get("enum") or [])[:8]:
            if isinstance(value, str) and len(value) < 48:
                parts.append(value)
    return tokens(" ".join(parts))


def select_tool(goal: str, tools: list[ToolSpec]) -> Candidate | None:
    g = tokens(goal)
    intent = goal_intent(goal)
    best: Candidate | None = None
    for spec in tools:
        # A question must not be answered by a tool that writes, and a
        # request to change something must not be served by a read.
        if (intent == "read") != (spec.mutation_class == "read"):
            continue
        surface = tool_surface(spec)
        if not surface:
            continue
        overlap = len(surface & g)
        score = overlap / max(3.0, len(surface) ** 0.5)
        args = fill_arguments(spec, goal)
        if args is None:
            continue
        if best is None or score > best.score:
            best = Candidate(spec, score, args)
    if best is None or best.score <= 0.0:
        return None
    return best


def extract_answer(goal: str, result: ExecutionResult) -> str | None:
    """Generic reduction of a tool result to an answer.

    Deliberately app-agnostic: count rows for a counting question, otherwise
    sum or read the numeric field the goal names. Returns None when the
    reduction is ambiguous, which sends the task to the browser fallback.
    """
    value = result.value
    rows = next((r for r in (_rows(v) for v in reversed(result.values or [value]))
                 if r), None)
    g = goal.lower()
    wants_count = "how many" in g or "count" in g
    literals = [l.lower() for l in goal_literals(goal)]

    if rows is not None:
        if literals:
            filtered = [r for r in rows
                        if any(str(v).lower() in literals for v in r.values())]
            if not filtered and not wants_count:
                # The goal named an entity and the result does not mention it:
                # reducing this to a number would be a guess, so decline and
                # let the browser fallback handle it.
                return None
            rows = filtered or rows
        if wants_count:
            return str(len(rows))
        numeric = _numeric_field(goal, rows)
        if numeric is not None:
            total = sum(float(r[numeric] or 0) for r in rows
                        if _is_number(r.get(numeric)))
            return f"{total:.2f}"
        return None
    if _is_number(value):
        return str(value)
    if isinstance(value, dict):
        for key in ("message", "data", "value"):
            if key in value and _is_number(value[key]):
                return str(value[key])
    return None


COLUMN_KEYS = ("keys", "columns", "fields")
ROW_KEYS = ("values", "data", "rows", "result")


def _columnar(value: Any) -> list[dict] | None:
    """Zip a columnar result (column names + rows of cells) into records.

    Desk endpoints answer with columns and rows rather than objects; without
    this every synthesized list tool would return something an agent cannot
    read.
    """
    if not isinstance(value, dict):
        return None
    columns = next((value[k] for k in COLUMN_KEYS
                    if isinstance(value.get(k), list)
                    and all(isinstance(c, str) for c in value[k])), None)
    rows = next((value[k] for k in ROW_KEYS
                 if isinstance(value.get(k), list)
                 and all(isinstance(r, list) for r in value[k])), None)
    if columns is None or rows is None:
        return None
    return [dict(zip(columns, row)) for row in rows]


def _rows(value: Any) -> list[dict] | None:
    if isinstance(value, list) and value and all(isinstance(r, dict) for r in value):
        return value
    columnar = _columnar(value)
    if columnar is not None:
        return columnar
    if isinstance(value, dict):
        for key in ("message", "data", "values", "result"):
            inner = value.get(key)
            if isinstance(inner, dict):
                nested = _columnar(inner)
                if nested is not None:
                    return nested
                inner = inner.get("values") or inner.get("data") or inner
            if isinstance(inner, list) and inner and all(isinstance(r, dict) for r in inner):
                return inner
    return None


def _is_number(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return True
    try:
        float(str(value))
        return True
    except (TypeError, ValueError):
        return False


_FIELD_HINTS = (
    ("outstanding", "outstanding_amount"),
    ("unpaid", "outstanding_amount"),
    ("grand total", "grand_total"),
    ("total", "grand_total"),
    ("quantity", "actual_qty"),
    ("qty", "actual_qty"),
    ("stock", "actual_qty"),
    ("price", "price_list_rate"),
    ("rate", "price_list_rate"),
)


def _numeric_field(goal: str, rows: list[dict]) -> str | None:
    g = goal.lower()
    keys = set().union(*(set(r) for r in rows)) if rows else set()
    for hint, field in _FIELD_HINTS:
        if hint in g and field in keys:
            return field
    numeric_keys = [k for k in keys
                    if any(_is_number(r.get(k)) for r in rows)
                    and k != "name" and not k.startswith("_")]
    return numeric_keys[0] if len(numeric_keys) == 1 else None


class DeterministicRouter:
    """Offline stand-in for the model in condition B."""

    def __init__(self, goal: str, tools: list[ToolSpec]) -> None:
        self.goal = goal
        self.tools = tools
        self.stage = 0
        self.last_result: ExecutionResult | None = None
        self.browser_answer: str | None = None

    def __call__(self) -> dict[str, Any]:
        if self.stage == 0:
            self.stage = 1
            cand = select_tool(self.goal, self.tools)
            if cand is None:
                self.stage = 2
                return {"action": "browser_task", "goal": self.goal}
            return {"action": "tool", "name": cand.spec.name,
                    "arguments": cand.arguments}
        if self.stage == 1:
            self.stage = 2
            result = self.last_result
            if result is not None and result.ok:
                answer = extract_answer(self.goal, result)
                if answer is not None:
                    self.stage = 3
                    return {"action": "done", "answer": answer}
            return {"action": "browser_task", "goal": self.goal}
        if self.stage == 2:
            self.stage = 3
            return {"action": "done", "answer": self.browser_answer or ""}
        return {"action": "done", "answer": self.browser_answer or ""}


def _router_policy(messages: list[dict[str, Any]], ctx: dict[str, Any]) -> str:
    return json.dumps(ctx["router"]())


register_policy("tool_agent", _router_policy)


# --------------------------------------------------------------------------
# The loop
# --------------------------------------------------------------------------

def available_tools(catalog: ToolCatalog, allow_writes: bool,
                    require_verified: bool = True) -> list[ToolSpec]:
    out = []
    for spec in catalog.tools:
        if require_verified and not spec.verified:
            continue
        if spec.mutation_class != "read" and not allow_writes:
            continue
        out.append(spec)
    return out


def run_tool_task(task, catalog: ToolCatalog, cfg: Config | None = None,
                  client: LLMClient | None = None, recipe_factory=None,
                  allow_writes: bool = False, executor: ToolExecutor | None = None,
                  require_verified: bool = True) -> RunResult:
    cfg = cfg or get_config()
    client = client or make_client(cfg.models.agent, cfg.models.provider)
    tools = available_tools(catalog, allow_writes, require_verified)
    executor = executor or ToolExecutor(cfg, allow_writes=allow_writes)
    result = RunResult(task_id=task.id, template_id=task.template_id,
                       condition="B_tools")
    router = DeterministicRouter(task.goal, tools) if client.provider == "offline" else None
    if client.provider == "offline" and router is None:
        result.error = "offline provider needs a router"
        return result

    t0 = time.time()
    for i in range(cfg.bench.max_steps):
        messages = build_messages(task.goal, tools, result.steps)
        step_t0 = time.time()
        resp = client.complete(messages, policy="tool_agent",
                               policy_context={"router": router})
        try:
            action = json.loads(_json_slice(resp.text))
        except Exception as exc:
            action = {"action": "invalid", "error": str(exc)}

        kind = str(action.get("action", "")).lower()
        served_by = "browser"
        detail = ""
        done = False

        if kind == "tool":
            spec = catalog.by_name(str(action.get("name", "")))
            if spec is None:
                detail = f"no such tool {action.get('name')!r}"
            else:
                exec_result = executor.execute(spec, dict(action.get("arguments", {})))
                if router is not None:
                    router.last_result = exec_result
                served_by = "tool" if exec_result.ok else "tool_failed"
                detail = exec_result.summary()
        elif kind == "browser_task":
            sub = _run_browser_fallback(task, cfg, client, recipe_factory)
            result.steps.extend(sub.steps)
            if router is not None:
                router.browser_answer = sub.answer
            detail = f"browser fallback finished: {sub.answer!r}"
            served_by = "browser_fallback"
        elif kind == "done":
            result.answer = str(action.get("answer", ""))
            result.finished = True
            done = True
            detail = "done"
        else:
            detail = f"unusable action: {action}"

        result.steps.append(StepRecord(
            index=i, action=action, ok=served_by != "tool_failed", detail=detail,
            usage=resp.usage, wall_s=time.time() - step_t0,
            prompt_chars=sum(len(m["content"]) for m in messages),
            served_by=served_by))
        if done:
            break
    result.wall_s = time.time() - t0
    return result


def _run_browser_fallback(task, cfg: Config, client: LLMClient, recipe_factory):
    """The fallback really does drive the browser — its cost is counted."""
    from shadow.route.browser_agent import run_browser_task

    sub = run_browser_task(task, cfg, client, recipe_factory=recipe_factory)
    for step in sub.steps:
        step.served_by = "browser_fallback"
    return sub


def _json_slice(text: str) -> str:
    payload = text.strip()
    if payload.startswith("```"):
        payload = payload.split("\n", 1)[-1].rsplit("```", 1)[0]
    start, end = payload.find("{"), payload.rfind("}")
    return payload[start:end + 1] if start != -1 and end > start else payload
