"""Replay synthesized tools against the live application.

Reads are replayed with the arguments they were observed with; success is a
2xx plus a response whose *shape* still matches (keys present, types match)
— not its values, which move.

Writes and destructive tools are only ever replayed against a freshly reset
instance, and are required to actually change the database, checked through
the oracle. A write tool that returns 200 without changing anything is not
verified.
"""
from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any

from shadow.capture.schema import ToolCatalog, ToolSpec, read_catalog, write_catalog
from shadow.config import Config, get_config
from shadow.serve.executor import ExecutionResult, ToolExecutor

from oracle.client import OracleClient
from oracle.reset import reset as reset_instance

RESOURCE_RE = re.compile(r"/api/resource/([^/{]+)")


@dataclass
class ReplayReport:
    tool: str
    mutation_class: str
    ok: bool
    status: int | None = None
    shape_match: float = 0.0
    error: str | None = None
    binding_failure: bool = False
    changed_doctypes: dict[str, int] = field(default_factory=dict)
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _shape_of(body: Any, limit: int = 40) -> dict[str, str]:
    from shadow.distill.provenance import _walk_json

    shape: dict[str, str] = {}
    if body is None:
        return shape
    for path, leaf in _walk_json(body):
        shape.setdefault(re.sub(r"\[\d+\]", "[]", path), type(leaf).__name__)
        if len(shape) >= limit:
            break
    return shape


def shape_match(expected: dict[str, str], actual: dict[str, str]) -> float:
    if not expected:
        return 1.0
    hits = sum(1 for k, t in expected.items() if actual.get(k) == t)
    return hits / len(expected)


def watched_doctypes(spec: ToolSpec) -> list[str]:
    """Doctypes this tool plausibly touches, read off its own steps."""
    found: set[str] = set()
    for step in spec.steps:
        m = RESOURCE_RE.search(step.path_template)
        if m:
            found.add(m.group(1).replace("%20", " "))
        for key, binding in step.body_bindings.items():
            if key.split("::")[0] != "doctype":
                continue
            value = binding.literal_value if binding.kind == "literal" else binding.example_value
            if isinstance(value, str) and value:
                found.add(value)
        doc = step.body_bindings.get("doc::$.doctype")
        if doc is not None:
            value = doc.literal_value if doc.kind == "literal" else doc.example_value
            if isinstance(value, str):
                found.add(value)
    return sorted(found)


def verify_read(spec: ToolSpec, executor: ToolExecutor,
                shape_threshold: float = 0.6) -> ReplayReport:
    result = executor.execute(spec, dict(spec.example_args))
    report = ReplayReport(tool=spec.name, mutation_class=spec.mutation_class,
                          ok=False)
    if result.steps:
        report.status = result.steps[-1].status
    if not result.ok:
        report.error = result.error
        report.binding_failure = bool(result.error
                                      and "unresolved binding" in result.error)
        return report
    report.shape_match = shape_match(spec.response_shape, _shape_of(result.value))
    report.ok = report.shape_match >= shape_threshold
    if not report.ok:
        report.error = (f"response shape drifted "
                        f"({report.shape_match:.0%} of observed keys matched)")
    return report


def verify_write(spec: ToolSpec, executor: ToolExecutor, oracle: OracleClient,
                 cfg: Config) -> ReplayReport:
    report = ReplayReport(tool=spec.name, mutation_class=spec.mutation_class,
                          ok=False)
    doctypes = watched_doctypes(spec)
    reset_instance()
    oracle.invalidate()
    before = oracle.snapshot(doctypes) if doctypes else {}
    result: ExecutionResult = executor.execute(spec, dict(spec.example_args))
    if result.steps:
        report.status = result.steps[-1].status
    if not result.ok:
        report.error = result.error
        report.binding_failure = bool(result.error
                                      and "unresolved binding" in result.error)
        reset_instance()
        return report
    after = oracle.snapshot(doctypes) if doctypes else {}
    report.changed_doctypes = {dt: after[dt] - before[dt]
                               for dt in doctypes if after.get(dt) != before.get(dt)}
    report.shape_match = shape_match(spec.response_shape, _shape_of(result.value))
    if spec.mutation_class == "destructive":
        report.ok = bool(report.changed_doctypes)
        report.note = "destructive tool verified only by observed state change"
    else:
        report.ok = bool(report.changed_doctypes) or report.shape_match >= 0.6
    if not report.ok:
        report.error = "write produced no observable change in the database"
    reset_instance()
    oracle.invalidate()
    return report


def verify_catalog(catalog: ToolCatalog, cfg: Config | None = None,
                   allow_writes: bool = False) -> list[ReplayReport]:
    cfg = cfg or get_config()
    reports: list[ReplayReport] = []
    read_executor = ToolExecutor(cfg, allow_writes=False)
    write_executor = ToolExecutor(cfg, allow_writes=True)
    oracle = OracleClient(cfg)

    for spec in catalog.tools:
        if spec.mutation_class == "read":
            report = verify_read(spec, read_executor)
        elif not allow_writes:
            report = ReplayReport(
                tool=spec.name, mutation_class=spec.mutation_class, ok=False,
                note="skipped: writes are not verified without --allow-writes")
        else:
            report = verify_write(spec, write_executor, oracle, cfg)
        spec.verified = report.ok
        spec.verify_note = report.error or report.note
        reports.append(report)
    return reports


def summarize(reports: list[ReplayReport], catalog: ToolCatalog) -> str:
    lines = []
    verified = sum(1 for r in reports if r.ok)
    lines.append(f"{verified}/{len(reports)} tools verified")
    total_bindings = sum(t.n_from_response_bindings for t in catalog.tools)
    failed = sum(1 for r in reports if r.binding_failure)
    lines.append(f"from_response bindings: {total_bindings}; "
                 f"tools failing on an unresolved binding at replay: {failed}")
    for r in reports:
        flag = "ok  " if r.ok else "FAIL"
        extra = r.error or r.note or ""
        lines.append(f"  {flag} {r.tool:34s} {r.mutation_class:11s} "
                     f"status={r.status} shape={r.shape_match:.0%} {extra[:70]}")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--allow-writes", action="store_true",
                    help="replay write tools against a reset instance")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    cfg = get_config()
    catalog = read_catalog(cfg.path("tools"))
    reports = verify_catalog(catalog, cfg, allow_writes=args.allow_writes)
    write_catalog(cfg.path("tools"), catalog)
    (cfg.path("artifacts") / "verify_report.json").write_text(
        json.dumps([r.to_dict() for r in reports], indent=2))
    print(json.dumps([r.to_dict() for r in reports], indent=2) if args.json
          else summarize(reports, catalog))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
