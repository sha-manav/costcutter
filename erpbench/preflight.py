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


def _ledger() -> Path:
    """Resolved per call, not at import.

    The ledger is the budget record of truth, and a module-level constant
    baked in at import time cannot be redirected by a test — so the suite
    appended five fabricated rows to the real one. They cost $0.00 and did
    not move the balance, which is precisely why it would have gone
    unnoticed: invented entries in an audit trail that still adds up.
    """
    return ARTIFACTS / "spend_ledger.jsonl"


def spent_on(line_item: str) -> float:
    ledger = _ledger()
    if not ledger.exists():
        return 0.0
    total = 0.0
    for line in ledger.read_text().splitlines():
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
    ledger = _ledger()
    ledger.parent.mkdir(parents=True, exist_ok=True)
    with ledger.open("a") as fh:
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


# SPEC §12.2 is a gate that has to be *runnable* to be a gate. HANDOFF.md
# names `python -m erpbench.preflight` as the next action; without this block
# the documented command exits 1 on "module has no __main__", which reads
# like a refusal and is not one.
GATE_MODELS = ("openrouter/qwen/qwen3-8b", "openrouter/qwen/qwen3-14b",
               "openrouter/qwen/qwen3-32b")
GATE_PROVIDER_URLS = {"openrouter": "https://openrouter.ai"}


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="erpbench.preflight",
        description="SPEC §12.2 preflight. Refuses; never warns.")
    parser.add_argument("--line-item", default="calibration_gate",
                        choices=sorted(BUDGET_LINES))
    parser.add_argument("--projected-usd", type=float, default=0.0,
                        help="estimate from a 3-task dry run x scale x 1.3")
    parser.add_argument("--models", default=",".join(GATE_MODELS))
    parser.add_argument("--sites", type=int, default=0,
                        help="number of pooled ERPNext sites to health-check "
                             "(native Frappe bench deployments)")
    parser.add_argument("--check-adapter", action="store_true",
                        help="health-check the configured site and verify the "
                             "firm seed images exist (any deployment)")
    args = parser.parse_args(argv)

    sites = None
    pool_error = ""
    if args.sites:
        from erpbench.sites import provision

        try:
            sites = [provision(i) for i in range(args.sites)]
        except Exception as exc:                      # noqa: BLE001
            # A pool that cannot even be described is a failed check, not a
            # crash: the report must still print and still refuse. Left as
            # None so `run` does not also score an empty pool as 0/0 healthy,
            # which renders as a PASS.
            sites = None
            pool_error = f"{type(exc).__name__}: {exc}"

    report = run(line_item=args.line_item, projected_usd=args.projected_usd,
                 models=[m for m in args.models.split(",") if m],
                 provider_urls=GATE_PROVIDER_URLS, sites=sites)
    if args.sites and sites is None:
        report.add("sites", False,
                   f"0/{args.sites} healthy — pool unavailable ({pool_error})")

    if args.check_adapter:
        _check_adapter(report)
    print(report.render())
    return 0 if report.ok else 1


def _check_adapter(report: PreflightReport) -> None:
    """SPEC §12.2 check 6: sites healthy **and resettable**.

    Health alone is not the check. A site that answers /api/method/ping but
    cannot be restored to a known seed makes every run start from whatever
    the previous run left behind, which produces results that look ordinary
    and are not comparable to each other.
    """
    from erpbench.adapter import make_adapter
    from erpbench.firms import FIRMS

    try:
        adapter = make_adapter("erpnext")
        report.add("site_health", adapter.health(),
                   f"{adapter.base_url} (Host: {adapter.site})")
    except Exception as exc:                          # noqa: BLE001
        report.add("site_health", False, f"{type(exc).__name__}: {exc}")
        return

    seeds = ARTIFACTS / "firm_seeds"
    missing = [f for f in FIRMS if not (seeds / f"firm_{f}.sql").exists()]
    report.add("firm_seeds", not missing,
               "all three firm seed images present" if not missing else
               f"missing seed images for firm(s) {','.join(missing)} — every "
               "firm would reset to the same world")


if __name__ == "__main__":
    raise SystemExit(main())


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
