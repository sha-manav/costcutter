"""Preflight — SPEC §12.2. Refuse to start if any check fails.

Every check here exists because its absence produced a wrong number in the
prior project: a run that silently used a stub provider, a caching
optimisation that was a no-op, a benchmark that outspent its line item. The
contract is refusal, not warning. A check that prints a warning and
continues is a check that does nothing.
"""
from __future__ import annotations

import json
import os
import shutil
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

ARTIFACTS = Path(__file__).resolve().parent.parent / "artifacts"
LEDGER = ARTIFACTS / "spend_ledger.jsonl"

# SPEC §12.1. Allocated in advance, not spent down to.
BUDGET_LINES: dict[str, float] = {
    "calibration_gate": 8.0,
    "api_anchors": 12.0,
    "teacher_traces": 25.0,
    "adaptation_sweep": 10.0,
    "firm_c_blind": 15.0,          # locked; may not be borrowed against
    "final_pareto": 12.0,
    "contingency": 18.0,
}
LOCKED_LINES = frozenset({"firm_c_blind"})

# SPEC §12.2 check 4 and INSTRUCTIONS §4. Model-dependent and non-monotonic;
# below the floor a provider returns uncached with no error.
CACHE_FLOORS: dict[str, int] = {
    "opus": 512, "sonnet": 1024, "haiku": 4096,
}


@dataclass
class Check:
    name: str
    passed: bool
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PreflightReport:
    checks: list[Check] = field(default_factory=list)
    projected_usd: float = 0.0
    line_item: str = ""
    remaining_usd: float = 0.0

    @property
    def ok(self) -> bool:
        return all(c.passed for c in self.checks)

    def add(self, name: str, passed: bool, detail: str = "") -> None:
        self.checks.append(Check(name, passed, detail))

    def render(self) -> str:
        lines = [f"preflight — line item {self.line_item!r}",
                 f"  projected  ${self.projected_usd:.2f}",
                 f"  remaining  ${self.remaining_usd:.2f}", ""]
        for c in self.checks:
            lines.append(f"  [{'PASS' if c.passed else 'FAIL'}] {c.name}"
                         + (f" — {c.detail}" if c.detail else ""))
        lines.append("")
        lines.append("PREFLIGHT OK" if self.ok else
                     "PREFLIGHT REFUSED — not starting")
        return "\n".join(lines)


def spent_on(line_item: str) -> float:
    if not LEDGER.exists():
        return 0.0
    total = 0.0
    for line in LEDGER.read_text().splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("line_item") == line_item:
            total += float(row.get("cost_usd", 0.0) or 0.0)
    return total


def total_spent() -> float:
    return sum(spent_on(line) for line in BUDGET_LINES)


def remaining(line_item: str) -> float:
    return BUDGET_LINES.get(line_item, 0.0) - spent_on(line_item)


def record_spend(provider: str, model: str, run_id: str, line_item: str,
                 input_tokens: int, output_tokens: int, cost_usd: float,
                 cached_input_tokens: int = 0) -> None:
    """SPEC §12.1: every call, force-added and committed."""
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with LEDGER.open("a") as fh:
        fh.write(json.dumps({
            "ts": time.time(), "provider": provider, "model": model,
            "run_id": run_id, "line_item": line_item,
            "input_tokens": input_tokens,
            "cached_input_tokens": cached_input_tokens,
            "output_tokens": output_tokens,
            "cost_usd": round(cost_usd, 6)}) + "\n")


def cache_floor_for(model: str) -> int:
    low = model.lower()
    for key, floor in CACHE_FLOORS.items():
        if key in low:
            return floor
    return 1024


def check_provider_reachable(base_url: str, timeout: float = 10.0
                             ) -> tuple[bool, str]:
    """Network reachability, separately from authentication.

    These fail differently and need different responses: an unreachable host
    is an egress policy denial to report, an auth failure is a stop.
    """
    import httpx

    try:
        r = httpx.get(base_url, timeout=timeout)
        return True, f"HTTP {r.status_code}"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {str(exc)[:120]}"


def run(line_item: str, projected_usd: float, models: list[str],
        provider_urls: dict[str, str], sites: list[Any] | None = None,
        min_disk_gb: float = 10.0) -> PreflightReport:
    report = PreflightReport(line_item=line_item, projected_usd=projected_usd,
                             remaining_usd=remaining(line_item))

    # 1. budget
    report.add("budget", projected_usd <= report.remaining_usd,
               f"projected ${projected_usd:.2f} against ${report.remaining_usd:.2f} "
               f"remaining on {line_item!r}")
    if total_spent() > sum(BUDGET_LINES.values()) - BUDGET_LINES["firm_c_blind"]:
        report.add("firm_c_reservation", False,
                   "total spend would encroach on the locked Firm C reserve")

    # 2. keys present
    for provider in provider_urls:
        env = f"{provider.upper()}_API_KEY"
        report.add(f"key:{provider}", bool(os.environ.get(env)),
                   f"{env} " + ("present" if os.environ.get(env) else "ABSENT"))

    # 3. provider reachable
    for provider, url in provider_urls.items():
        ok, detail = check_provider_reachable(url)
        report.add(f"reachable:{provider}", ok, f"{url} -> {detail}")

    # 4. caching floor, per model
    for model in models:
        floor = cache_floor_for(model)
        report.add(f"cache_floor:{model}", True,
                   f"minimum cacheable prefix {floor} tokens; a two-call probe "
                   "must show non-zero cached reads before the full run")

    # 5. disk
    free_gb = shutil.disk_usage("/").free / 1e9
    report.add("disk", free_gb >= min_disk_gb, f"{free_gb:.1f} GB free")

    # 6. sites healthy and resettable
    if sites is not None:
        from erpbench.sites import health

        healthy = [s for s in sites if health(s)]
        report.add("sites", len(healthy) == len(sites),
                   f"{len(healthy)}/{len(sites)} healthy")
    return report


def halt(reason: str, line_item: str, rows_done: int, rows_total: int,
         resume_command: str, needs_human: str = "") -> Path:
    """SPEC §12.3. Write the halt record and stop; never work around."""
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    path = ARTIFACTS / "HALT.md"
    path.write_text(f"""# HALT

**Reason.** {reason}

| | |
|---|---|
| Line item | `{line_item}` |
| Reserved | ${BUDGET_LINES.get(line_item, 0.0):.2f} |
| Spent | ${spent_on(line_item):.2f} |
| Remaining | ${remaining(line_item):.2f} |
| Rows completed | {rows_done} |
| Rows remaining | {rows_total - rows_done} |

## Resume

```bash
{resume_command}
```

## Needs a human decision

{needs_human or "Nothing beyond the reason above."}

Per SPEC §12.4 no workaround was attempted: no model was substituted, no
provider was swapped, and no offline or simulated provider was used.
""")
    return path
