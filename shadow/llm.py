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
    cached_input_tokens: int = 0
    output_tokens: int = 0
    latency_s: float = 0.0
    simulated: bool = False

    def __add__(self, other: "LLMUsage") -> "LLMUsage":
        return LLMUsage(
            model=self.model or other.model,
            input_tokens=self.input_tokens + other.input_tokens,
            cached_input_tokens=self.cached_input_tokens + other.cached_input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            latency_s=self.latency_s + other.latency_s,
            simulated=self.simulated or other.simulated,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "input_tokens": self.input_tokens,
            "cached_input_tokens": self.cached_input_tokens,
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


class LiteLLMClient:
    provider = "litellm"

    def __init__(self, model: str) -> None:
        self.model = model

    def complete(self, messages: list[dict[str, Any]], **kwargs: Any) -> LLMResponse:
        import litellm

        t0 = time.time()
        resp = litellm.completion(model=self.model, messages=messages, **kwargs)
        dt = time.time() - t0
        usage = getattr(resp, "usage", None)
        prompt_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
        cached = 0
        details = getattr(usage, "prompt_tokens_details", None)
        if details is not None:
            cached = int(getattr(details, "cached_tokens", 0) or 0)
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
                input_tokens=max(0, prompt_tokens - cached),
                cached_input_tokens=cached,
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


def make_client(model: str, provider: str = "auto") -> LLMClient:
    """Build a client. ``auto`` uses litellm when credentials exist."""
    if provider == "auto":
        provider = "litellm" if _credentials_present() else "offline"
    if provider == "litellm":
        return LiteLLMClient(model)
    if provider == "offline":
        return OfflineClient(model)
    raise ValueError(f"unknown provider {provider!r}")
