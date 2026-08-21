"""Model access with full token accounting.

Every call made by either agent condition goes through here so that cost
comparisons are apples-to-apples: same accounting code, same cost table,
same prompt construction.

Two providers:

* ``litellm``  — real API calls, usage read from the provider response.
* ``offline``  — no API call is made. The prompt is still constructed and
  tokenised exactly as it would be, so input/output token *accounting* is
  real; the *decision* comes from a registered deterministic policy. Used
  when no model credentials are available. Runs are tagged so no result can
  be mistaken for an LLM-in-the-loop run.
"""
from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

__all__ = [
    "LLMUsage", "LLMResponse", "LLMClient", "make_client",
    "count_tokens", "OfflinePolicy", "register_policy",
]


@dataclass
class LLMUsage:
    model: str
    input_tokens: int = 0
    # Tokens served from the provider's prompt cache. Priced separately, and
    # reported separately so a reader can see how much of the prefix was
    # actually reused rather than take it on faith.
    cached_input_tokens: int = 0
    # Tokens written INTO the cache. Anthropic bills these above the normal
    # input rate (1.25x), so folding them into input_tokens understates the
    # cost of the first step of every task.
    cache_write_tokens: int = 0
    output_tokens: int = 0
    latency_s: float = 0.0
    simulated: bool = False

    @property
    def total_input_tokens(self) -> int:
        return self.input_tokens + self.cached_input_tokens + self.cache_write_tokens

    def __add__(self, other: "LLMUsage") -> "LLMUsage":
        return LLMUsage(
            model=self.model or other.model,
            input_tokens=self.input_tokens + other.input_tokens,
            cached_input_tokens=self.cached_input_tokens + other.cached_input_tokens,
            cache_write_tokens=self.cache_write_tokens + other.cache_write_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            latency_s=self.latency_s + other.latency_s,
            simulated=self.simulated or other.simulated,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "input_tokens": self.input_tokens,
            "cached_input_tokens": self.cached_input_tokens,
            "cache_write_tokens": self.cache_write_tokens,
            "output_tokens": self.output_tokens,
            "latency_s": round(self.latency_s, 4),
            "simulated": self.simulated,
        }


@dataclass
class LLMResponse:
    text: str
    usage: LLMUsage
    raw: Any = None
    tool_calls: list[dict[str, Any]] = field(default_factory=list)

    def json(self) -> Any:
        """Parse the response as JSON, tolerating fenced code blocks."""
        text = self.text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text
            text = text.rsplit("```", 1)[0]
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end > start:
            text = text[start:end + 1]
        return json.loads(text)


def count_tokens(model: str, messages: list[dict[str, Any]] | str) -> int:
    """Token count for accounting. Uses the provider tokenizer when available."""
    try:
        import litellm

        if isinstance(messages, str):
            return int(litellm.token_counter(model=model, text=messages))
        return int(litellm.token_counter(model=model, messages=messages))
    except Exception:
        text = messages if isinstance(messages, str) else json.dumps(messages)
        # Fallback: 4 chars/token is the standard rough English estimate.
        return max(1, len(text) // 4)


class LLMClient(Protocol):
    provider: str
    model: str

    def complete(self, messages: list[dict[str, Any]], **kwargs: Any) -> LLMResponse: ...


ANTHROPIC_CACHE_CONTROL = {"type": "ephemeral"}


def supports_prompt_caching(model: str) -> bool:
    """Whether to mark the fixed prefix cacheable for this model."""
    name = model.lower()
    return "claude" in name or "anthropic" in name


def mark_cacheable(messages: list[dict[str, Any]], model: str) -> list[dict[str, Any]]:
    """Mark the system message as a cacheable prefix.

    Both conditions build a system message that is identical across every
    step of every task — the instructions, and for the tool agent the tool
    catalog — and a user message that carries only what changed. Marking the
    first as cacheable is what makes the catalog tax a one-off rather than a
    per-step charge, and it is applied identically to both conditions so the
    comparison stays honest.
    """
    if not supports_prompt_caching(model):
        return messages
    out: list[dict[str, Any]] = []
    for message in messages:
        if message.get("role") != "system" or not isinstance(message.get("content"), str):
            out.append(message)
            continue
        out.append({"role": "system",
                    "content": [{"type": "text", "text": message["content"],
                                 "cache_control": ANTHROPIC_CACHE_CONTROL}]})
    return out


class LiteLLMClient:
    provider = "litellm"

    def __init__(self, model: str, prompt_caching: bool = True) -> None:
        self.model = model
        self.prompt_caching = prompt_caching

    def complete(self, messages: list[dict[str, Any]], **kwargs: Any) -> LLMResponse:
        import litellm

        # Call-site routing for the offline provider; not a model parameter.
        kwargs.pop("policy", None)
        kwargs.pop("policy_context", None)
        if self.prompt_caching:
            messages = mark_cacheable(messages, self.model)
        t0 = time.time()
        resp = litellm.completion(model=self.model, messages=messages, **kwargs)
        dt = time.time() - t0
        usage = getattr(resp, "usage", None)
        prompt_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
        cached = 0
        details = getattr(usage, "prompt_tokens_details", None)
        if details is not None:
            cached = int(getattr(details, "cached_tokens", 0) or 0)
        # Anthropic reports cache reads and writes outside prompt_tokens_details.
        cached = cached or int(getattr(usage, "cache_read_input_tokens", 0) or 0)
        cache_write = int(getattr(usage, "cache_creation_input_tokens", 0) or 0)
        prompt_tokens = max(prompt_tokens, cached + cache_write)
        text = resp.choices[0].message.content or ""
        tool_calls = []
        for tc in getattr(resp.choices[0].message, "tool_calls", None) or []:
            tool_calls.append({
                "id": getattr(tc, "id", ""),
                "name": tc.function.name,
                "arguments": tc.function.arguments,
            })
        return LLMResponse(
            text=text,
            usage=LLMUsage(
                model=self.model,
                input_tokens=max(0, prompt_tokens - cached - cache_write),
                cached_input_tokens=cached,
                cache_write_tokens=cache_write,
                output_tokens=int(getattr(usage, "completion_tokens", 0) or 0),
                latency_s=dt,
            ),
            raw=resp,
            tool_calls=tool_calls,
        )


OfflinePolicy = Callable[[list[dict[str, Any]], dict[str, Any]], str]
_POLICIES: dict[str, OfflinePolicy] = {}


def register_policy(name: str, policy: OfflinePolicy) -> None:
    """Register a deterministic stand-in for the model, keyed by call site."""
    _POLICIES[name] = policy


class OfflineClient:
    """Deterministic provider. Builds and tokenises the real prompt, then
    answers from a registered policy instead of calling a model."""

    provider = "offline"

    def __init__(self, model: str) -> None:
        self.model = model

    def complete(self, messages: list[dict[str, Any]], **kwargs: Any) -> LLMResponse:
        site = kwargs.pop("policy", "default")
        ctx = kwargs.pop("policy_context", {}) or {}
        policy = _POLICIES.get(site)
        if policy is None:
            raise RuntimeError(
                f"offline provider has no policy registered for call site {site!r}; "
                "set models.provider: litellm and supply credentials, or register one"
            )
        t0 = time.time()
        text = policy(messages, ctx)
        dt = time.time() - t0
        return LLMResponse(
            text=text,
            usage=LLMUsage(
                model=self.model,
                input_tokens=count_tokens(self.model, messages),
                cached_input_tokens=0,
                output_tokens=count_tokens(self.model, text),
                latency_s=dt,
                simulated=True,
            ),
        )


def _credentials_present() -> bool:
    return any(os.environ.get(k) for k in
               ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "AZURE_API_KEY",
                "GEMINI_API_KEY", "LITELLM_API_KEY"))


class MissingCredentials(RuntimeError):
    pass


def make_client(model: str, provider: str = "auto") -> LLMClient:
    """Build a client. ``auto`` uses litellm when credentials exist.

    The fallback is announced. A run that silently degrades to the offline
    provider produces numbers that look like model numbers and are not, and
    the only thing standing between that and a wrong headline is a flag in
    each result row.
    """
    if provider == "auto":
        if _credentials_present():
            provider = "litellm"
        else:
            provider = "offline"
            print("[shadow] no model credentials found; using the offline "
                  "deterministic provider. Result rows are tagged "
                  "usage.simulated=true.", file=sys.stderr)
    if provider == "litellm":
        return LiteLLMClient(model)
    if provider == "offline":
        return OfflineClient(model)
    raise ValueError(f"unknown provider {provider!r}")
