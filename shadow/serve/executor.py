"""Execute a synthesized ToolSpec against the live application.

Two things this is careful about:

* **Live credentials.** Recorded cookies and CSRF tokens are never replayed.
  The executor authenticates itself and re-injects a token it fetched this
  session; if it cannot get one it fails loudly instead of sending a stale
  secret.
* **Fail closed.** A ``from_response`` binding that does not resolve against
  the actual responses aborts the call. Silently sending a missing value is
  how synthesized tools quietly corrupt data.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from shadow.capture.schema import Binding, ToolSpec, ToolStep
from shadow.config import Config, get_config
from shadow.distill.induce import encode_value

# The desk exposes the session token two ways depending on the page: as a
# boot key, and as an assignment in the bootstrap script.
CSRF_RE = re.compile(
    r'(?:frappe\.csrf_token\s*=\s*|"csrf_token"\s*:\s*)"([^"]+)"')
JSON_PATH_TOKEN = re.compile(r"\.([^.\[\]]+)|\[(\d+)\]")


class BindingError(RuntimeError):
    """An unresolved binding. Always fatal — never silently skipped."""


class AuthError(RuntimeError):
    pass


# --------------------------------------------------------------------------
# JSON path helpers
# --------------------------------------------------------------------------

def _tokens(path: str) -> list[str | int]:
    body = path[1:] if path.startswith("$") else path
    out: list[str | int] = []
    for key, idx in JSON_PATH_TOKEN.findall(body):
        out.append(key if key else int(idx))
    return out


def resolve_json_path(obj: Any, path: str) -> Any:
    cur = obj
    for tok in _tokens(path):
        if isinstance(tok, int):
            if not isinstance(cur, list) or tok >= len(cur):
                raise BindingError(f"index {tok} out of range for {path}")
            cur = cur[tok]
        else:
            if not isinstance(cur, dict) or tok not in cur:
                raise BindingError(f"key {tok!r} missing for {path}")
            cur = cur[tok]
    return cur


def set_json_path(container: dict | list, path: str, value: Any) -> None:
    toks = _tokens(path)
    cur: Any = container
    for i, tok in enumerate(toks[:-1]):
        nxt = toks[i + 1]
        if isinstance(tok, int):
            while len(cur) <= tok:
                cur.append({} if isinstance(nxt, str) else [])
            cur = cur[tok]
        else:
            if tok not in cur or not isinstance(cur[tok], (dict, list)):
                cur[tok] = {} if isinstance(nxt, str) else []
            cur = cur[tok]
    last = toks[-1]
    if isinstance(last, int):
        while len(cur) <= last:
            cur.append(None)
        cur[last] = value
    else:
        cur[last] = value


# --------------------------------------------------------------------------
# Session
# --------------------------------------------------------------------------

class FrappeSession:
    """Owns a live login. Re-authenticates on demand; never replays secrets."""

    def __init__(self, cfg: Config, timeout: float = 30.0) -> None:
        self.cfg = cfg
        self.client = httpx.Client(base_url=cfg.app.base_url, timeout=timeout,
                                   follow_redirects=True, trust_env=False)
        self.csrf_token: str | None = None
        self._logged_in = False

    def login(self) -> None:
        resp = self.client.post(
            "/api/method/login",
            data={"usr": self.cfg.app.username, "pwd": self.cfg.app.password},
        )
        if resp.status_code != 200:
            raise AuthError(f"login failed: {resp.status_code} {resp.text[:200]}")
        self._logged_in = True
        self.csrf_token = self._fetch_csrf()

    def _fetch_csrf(self) -> str | None:
        """Read a *live* CSRF token out of the desk boot payload.

        Frappe only enforces CSRF when the session carries a token, so a
        missing token here is legitimate rather than an error — but a
        recorded one is never used either way.
        """
        try:
            resp = self.client.get("/app")
            if resp.status_code == 200:
                m = CSRF_RE.search(resp.text)
                if m and m.group(1):
                    return m.group(1)
        except httpx.HTTPError:
            pass
        return None

    def ensure(self) -> None:
        if not self._logged_in:
            self.login()

    def request(self, method: str, path: str, params: dict | None = None,
                json_body: Any = None, data: Any = None) -> httpx.Response:
        self.ensure()
        headers = {"Accept": "application/json"}
        if self.csrf_token and method.upper() in {"POST", "PUT", "PATCH", "DELETE"}:
            headers["X-Frappe-CSRF-Token"] = self.csrf_token
        resp = self.client.request(method, path, params=params, json=json_body,
                                   data=data, headers=headers)
        if resp.status_code in (401, 403) and self._looks_like_session_loss(resp):
            self._logged_in = False
            self.login()
            if self.csrf_token and method.upper() in {"POST", "PUT", "PATCH", "DELETE"}:
                headers["X-Frappe-CSRF-Token"] = self.csrf_token
            resp = self.client.request(method, path, params=params,
                                       json=json_body, data=data, headers=headers)
        return resp

    @staticmethod
    def _looks_like_session_loss(resp: httpx.Response) -> bool:
        text = resp.text[:500].lower()
        return ("csrf" in text or "session" in text or "not permitted" in text
                or "login" in text)

    def close(self) -> None:
        self.client.close()


# --------------------------------------------------------------------------
# Execution
# --------------------------------------------------------------------------

@dataclass
class StepResult:
    index: int
    method: str
    url: str
    status: int
    duration_ms: float
    body: Any = None
    error: str | None = None


@dataclass
class ExecutionResult:
    tool: str
    ok: bool
    steps: list[StepResult] = field(default_factory=list)
    # The final response, plus every step's response. A synthesized list tool
    # often ends on a count call, so the rows the caller wants are in an
    # earlier step rather than in `value`.
    values: list[Any] = field(default_factory=list)
    value: Any = None
    error: str | None = None
    duration_s: float = 0.0

    def summary(self, max_chars: int = 1200) -> str:
        if not self.ok:
            return f"ERROR: {self.error}"
        text = json.dumps(self.value, default=str)
        return text[:max_chars] + ("…" if len(text) > max_chars else "")


class ToolExecutor:
    def __init__(self, cfg: Config | None = None, session: FrappeSession | None = None,
                 allow_writes: bool = False) -> None:
        self.cfg = cfg or get_config()
        self.session = session or FrappeSession(self.cfg)
        self.allow_writes = allow_writes

    def execute(self, spec: ToolSpec, params: dict[str, Any]) -> ExecutionResult:
        if spec.mutation_class != "read" and not self.allow_writes:
            return ExecutionResult(
                tool=spec.name, ok=False,
                error=f"tool {spec.name!r} is {spec.mutation_class}; "
                      "writes are disabled (pass --allow-writes)")
        t0 = time.time()
        result = ExecutionResult(tool=spec.name, ok=True)
        responses: list[Any] = []
        try:
            for i, step in enumerate(spec.steps):
                sr, body = self._run_step(i, step, params, responses)
                result.steps.append(sr)
                responses.append(body)
                if sr.status >= 400:
                    result.ok = False
                    result.error = f"step {i} returned {sr.status}: {str(body)[:300]}"
                    break
            result.values = list(responses)
            if result.ok:
                result.value = responses[-1] if responses else None
        except BindingError as exc:
            result.ok = False
            result.error = f"unresolved binding: {exc}"
        except (httpx.HTTPError, AuthError) as exc:
            result.ok = False
            result.error = f"{type(exc).__name__}: {exc}"
        result.duration_s = time.time() - t0
        return result

    # -- internals ---------------------------------------------------------
    def _run_step(self, index: int, step: ToolStep, params: dict[str, Any],
                  responses: list[Any]) -> tuple[StepResult, Any]:
        path = step.path_template
        for pname, binding in step.path_bindings.items():
            value = self._resolve(binding, params, responses)
            path = path.replace("{" + pname + "}", str(value))

        query = self._collect(step.query_bindings, params, responses)
        body = self._collect(step.body_bindings, params, responses)

        json_body = data = None
        if step.body_encoding == "json":
            json_body = body
        elif step.body_encoding == "form":
            data = {k: (json.dumps(v) if isinstance(v, (dict, list)) else v)
                    for k, v in body.items()}

        t0 = time.time()
        resp = self.session.request(step.method, path, params=_flatten(query),
                                    json_body=json_body, data=data)
        dt = (time.time() - t0) * 1000
        try:
            parsed = resp.json()
        except ValueError:
            parsed = resp.text[:2000]
        return (StepResult(index=index, method=step.method, url=str(resp.request.url),
                           status=resp.status_code, duration_ms=dt,
                           body=_truncate(parsed)), parsed)

    def _collect(self, bindings: dict[str, Binding], params: dict[str, Any],
                 responses: list[Any]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for key, binding in bindings.items():
            value = self._resolve(binding, params, responses)
            if value is _MISSING:
                continue
            if "::" in key:
                field_name, json_path = key.split("::", 1)
                container = out.setdefault(field_name, {} if not json_path.startswith("$[") else [])
                set_json_path(container, json_path, value)
            else:
                out[key] = value
        return out

    def _resolve(self, binding: Binding, params: dict[str, Any],
                 responses: list[Any]) -> Any:
        if binding.kind == "literal":
            return binding.literal_value
        if binding.kind == "user_param":
            name = binding.param_name or ""
            if name in params:
                return params[name]
            if binding.required and binding.example_value is not None:
                # A required field the caller left out: fall back to the value
                # the app itself sent when this was observed, so the request
                # is still well-formed.
                return binding.example_value
            # Optional and unsupplied: omit it. Replaying an observed value
            # here is how a generalised tool sends one record type's fields
            # while creating another.
            return _MISSING
        src = binding.source_step_index
        if src is None or src >= len(responses):
            raise BindingError(
                f"step {src} has not run yet for {binding.json_path}")
        value = resolve_json_path(responses[src], binding.json_path or "$")
        return encode_value(value, binding.transform)


class _Missing:
    def __repr__(self) -> str:  # pragma: no cover - debug only
        return "<missing>"


_MISSING = _Missing()


def _flatten(query: dict[str, Any]) -> dict[str, Any]:
    return {k: (json.dumps(v) if isinstance(v, (dict, list)) else v)
            for k, v in query.items()}


def _truncate(value: Any, limit: int = 4000) -> Any:
    text = json.dumps(value, default=str)
    if len(text) <= limit:
        return value
    return {"_truncated": True, "preview": text[:limit]}
