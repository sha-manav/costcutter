"""Run the whole pipeline for the in-distribution regime, in its own directory.

capture -> distill -> verify -> benchmark, on the D templates, writing to
`artifacts/indist/`. The held-out artifacts are never read or written here:
the two evaluations share code and share nothing else.

    python -m shadow.bench.indist_pipeline capture --sessions 3
    python -m shadow.bench.indist_pipeline distill
    python -m shadow.bench.indist_pipeline verify
    python -m shadow.bench.indist_pipeline bench --require-model --trials 3
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from shadow.capture.schema import read_catalog, read_records, write_episodes, write_jsonl
from shadow.config import Config, get_config
from shadow.distill.emit import emit, render_catalog
from shadow.distill.filter import filter_records
from shadow.distill.induce import induce
from shadow.distill.segment import segment
from shadow.llm import MissingCredentials, resolve_model_provider
from shadow.bench.exclusive import instance_lock
from shadow.bench.indist import (
    INDIST_IDS, check_instance_holdout, eval_tasks, observe_tasks)
from shadow.bench.run import run_condition

INDIST_DIR = "artifacts/indist"


def indist_config(cfg: Config | None = None) -> Config:
    """The same config with every artifact path redirected.

    Redirecting the paths rather than reimplementing the pipeline is what
    keeps the two regimes genuinely comparable: identical filtering,
    segmentation, induction, verification and scoring code, applied to
    different traffic.
    """
    cfg = cfg or get_config()
    clone = cfg.model_copy(deep=True)
    clone.paths = cfg.paths.model_copy(update={
        "artifacts": INDIST_DIR,
        "capture": f"{INDIST_DIR}/capture/out.jsonl",
        "filtered": f"{INDIST_DIR}/capture/filtered.jsonl",
        "episodes": f"{INDIST_DIR}/episodes.json",
        "tools": f"{INDIST_DIR}/tools.json",
        "mcp_server": f"{INDIST_DIR}/mcp_server.py",
        "results": f"{INDIST_DIR}/results.jsonl",
        "charts": f"{INDIST_DIR}/charts",
        # The split file belongs to the held-out regime. This one has no
        # template split at all, so it must never be pointed at that file.
        "split": f"{INDIST_DIR}/split_not_used.json",
    })
    return clone


def _guard(template_id: str) -> None:
    """Only D templates, and only with the instance holdout intact."""
    check_instance_holdout()
    if template_id not in INDIST_IDS:
        raise RuntimeError(
            f"{template_id!r} is not an in-distribution template; this "
            "pipeline must never demonstrate a held-out one")


def cmd_capture(args) -> int:
    from shadow.bench.generate_traffic import generate

    cfg = indist_config()
    tasks = observe_tasks()
    Path(cfg.path("capture")).parent.mkdir(parents=True, exist_ok=True)
    with instance_lock("indist.capture"):
        entries = generate(args.sessions, cfg, seed=args.seed,
                           out_path=cfg.path("capture"),
                           manifest_path=cfg.path("artifacts") / "observe_manifest.json",
                           tasks=tasks, guard=_guard)
    ok = sum(1 for e in entries if e.ok)
    print(f"{ok}/{len(entries)} demonstrations completed; "
          f"capture at {cfg.path('capture')}")
    return 0 if ok == len(entries) else 1


def cmd_distill(args) -> int:
    cfg = indist_config()
    records = read_records(cfg.path("capture"))
    if not records:
        print(f"no capture at {cfg.path('capture')}", file=sys.stderr)
        return 2
    kept, stats = filter_records(records)
    write_jsonl(cfg.path("filtered"), kept)
    print(stats.render())

    result = segment(kept, cfg)
    write_episodes(cfg.path("episodes"), result.episodes)
    print(f"episodes: {len(result.episodes)}")

    induction = induce(result.episodes, cfg)
    catalog = induction.catalog
    # Every D template is observed, so there is no observe/eval template
    # partition to record here -- the holdout is over instances.
    catalog.observe_template_ids = sorted(INDIST_IDS)
    emit(catalog, cfg.path("tools"), cfg.path("mcp_server"),
         Path(__file__).resolve().parent.parent.parent)
    print(render_catalog(catalog))
    (cfg.path("artifacts") / "induction_diagnostics.json").write_text(json.dumps({
        "groups_seen": induction.groups_seen,
        "records_in": induction.records_in,
        "records_load_bearing": induction.records_load_bearing,
        "groups_below_support": induction.groups_below_support,
        "dropped_by_rank": induction.dropped_by_rank,
        "filter": stats.__dict__,
    }, indent=2, default=str))
    return 0


def cmd_verify(args) -> int:
    from shadow.verify.replay import verify_catalog

    cfg = indist_config()
    catalog = read_catalog(cfg.path("tools"))
    with instance_lock("indist.verify"):
        # Writes are replayed only against a freshly reset instance, exactly
        # as in the held-out regime.
        reports = verify_catalog(catalog, cfg, allow_writes=args.allow_writes)
    out = cfg.path("artifacts") / "verify_report.json"
    out.write_text(json.dumps([r if isinstance(r, dict) else r.__dict__
                               for r in reports], indent=2, default=str))
    # Re-emit so the served catalog reflects what actually verified.
    # verify_catalog sets the flag on the in-memory catalog; without this the
    # flags are computed and thrown away, and the benchmark then runs with an
    # empty tool set while reporting nothing wrong.
    emit(catalog, cfg.path("tools"), cfg.path("mcp_server"),
         Path(__file__).resolve().parent.parent.parent)
    verified = sum(1 for t in catalog.tools if t.verified)
    print(f"{verified}/{len(catalog.tools)} tools verified; report -> {out}")
    return 0


def cmd_bench(args) -> int:
    cfg = indist_config()
    try:
        provider = resolve_model_provider(cfg.models.agent, cfg.models.provider)
    except MissingCredentials as exc:
        if args.require_model:
            print(f"--require-model: {exc}", file=sys.stderr)
            return 2
        raise
    if args.require_model and provider != "litellm":
        print(f"--require-model given but the resolved provider is "
              f"{provider!r}", file=sys.stderr)
        return 2

    catalog = read_catalog(cfg.path("tools"))
    tasks = eval_tasks()          # held-out INSTANCES of observed templates
    out_path = Path(args.out) if args.out else cfg.path("results")
    print(f"provider: {provider}  model: {cfg.models.agent}  "
          f"tool_k: {cfg.bench.tool_k}")
    print(f"in-distribution tasks: {len(tasks)} across "
          f"{len({t.template_id for t in tasks})} templates")
    print(f"catalog: {len(catalog.tools)} tools "
          f"({sum(1 for t in catalog.tools if t.verified)} verified)")
    with instance_lock("indist.bench"):
        for condition in args.conditions.split(","):
            run_condition(condition.strip(), tasks, cfg, catalog, args.trials,
                          out_path, args.allow_writes,
                          not args.include_unverified, resume=args.resume)
    print(f"results appended to {out_path}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("capture")
    p.add_argument("--sessions", type=int, default=3)
    p.add_argument("--seed", type=int, default=0)
    p.set_defaults(func=cmd_capture)

    p = sub.add_parser("distill")
    p.set_defaults(func=cmd_distill)

    p = sub.add_parser("verify")
    p.add_argument("--allow-writes", action="store_true",
                   help="replay write tools too, against a reset instance")
    p.set_defaults(func=cmd_verify)

    p = sub.add_parser("bench")
    p.add_argument("--trials", type=int, default=3)
    p.add_argument("--conditions", default="A_browser,B_tools")
    p.add_argument("--allow-writes", action="store_true")
    p.add_argument("--include-unverified", action="store_true")
    p.add_argument("--require-model", action="store_true")
    p.add_argument("--resume", action="store_true")
    p.add_argument("--out", default=None)
    p.set_defaults(func=cmd_bench)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
