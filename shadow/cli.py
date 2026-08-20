"""Shadow command line.

    python -m shadow.cli seed        # deterministic data + seed snapshot
    python -m shadow.cli observe     # demonstration traffic (OBSERVE only)
    python -m shadow.cli distill     # capture -> episodes -> tools
    python -m shadow.cli trace EPID  # human-auditable dataflow trace
    python -m shadow.cli verify      # replay tools against the live app
    python -m shadow.cli serve       # run the synthesized MCP server
    python -m shadow.cli bench       # conditions A and B on EVAL
    python -m shadow.cli report      # metrics + charts + FINDINGS inputs
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from shadow.capture.schema import (
    read_catalog, read_episodes, read_records, write_episodes, write_jsonl,
)
from shadow.config import REPO_ROOT, get_config
from shadow.distill.emit import catalog_to_openapi_like, emit, render_catalog
from shadow.distill.filter import filter_records, summarize
from shadow.distill.induce import induce
from shadow.distill.provenance import ProvenanceEngine
from shadow.distill.segment import segment


def cmd_seed(args) -> int:
    cfg = get_config()
    bench_root = Path("/home/frappe/frappe-bench")
    python = bench_root / "env" / "bin" / "python"
    script = REPO_ROOT / "oracle" / "frappe_seed.py"
    if not python.exists():
        print(f"bench python not found at {python}; run infra/setup_erpnext.sh",
              file=sys.stderr)
        return 2
    rc = subprocess.call(["sudo", "-u", "frappe", "-H", str(python), str(script),
                          "--site", cfg.app.site], cwd=str(bench_root / "sites"))
    if rc != 0:
        return rc
    from oracle.reset import snapshot

    print(f"seed image written to {snapshot(cfg.app.site)}")
    return 0


def cmd_observe(args) -> int:
    from shadow.bench.generate_traffic import generate

    cfg = get_config()
    if args.fresh:
        cfg.path("capture").unlink(missing_ok=True)
    entries = generate(args.sessions, cfg, seed=args.seed)
    ok = sum(1 for e in entries if e.ok)
    print(f"{ok}/{len(entries)} demonstrations completed")
    return 0 if ok else 1


def cmd_distill(args) -> int:
    cfg = get_config()
    records = read_records(cfg.path("capture"))
    if not records:
        print(f"no capture at {cfg.path('capture')}", file=sys.stderr)
        return 2
    kept, stats = filter_records(records)
    write_jsonl(cfg.path("filtered"), kept)
    print(stats.render())
    if args.show_stream:
        print(summarize(kept, 80))

    result = segment(kept, cfg)
    write_episodes(cfg.path("episodes"), result.episodes)
    print(f"episodes: {len(result.episodes)}")
    for ep in result.episodes[:args.show_episodes]:
        print(f"  {ep.id} n={len(ep.records):3d} coherence={ep.coherence} "
              f"{ep.label!r}")

    induction = induce(result.episodes, cfg)
    catalog = induction.catalog
    catalog.observe_template_ids = sorted(
        set(json.loads(p.read_text()).get("observe", []))
        if (p := cfg.path("split")).exists() else [])
    emit(catalog, cfg.path("tools"), cfg.path("mcp_server"), REPO_ROOT)
    print(f"\nsignature groups: {induction.groups_seen}  "
          f"below min_support: {induction.groups_below_support}  "
          f"dropped by rank: {induction.dropped_by_rank}")
    print(render_catalog(catalog))
    if args.show_dataflow:
        print("\n" + catalog_to_openapi_like(catalog))
    (cfg.path("artifacts") / "induction_diagnostics.json").write_text(json.dumps({
        "groups_seen": induction.groups_seen,
        "groups_below_support": induction.groups_below_support,
        "dropped_by_rank": induction.dropped_by_rank,
        "diagnostics": induction.diagnostics,
        "filter": stats.__dict__,
        "labeler_usage": induction.usage.to_dict(),
    }, indent=2, default=str))
    return 0


def cmd_trace(args) -> int:
    cfg = get_config()
    episodes = read_episodes(cfg.path("episodes"))
    engine = ProvenanceEngine(cfg)
    wanted = {args.episode} if args.episode else None
    shown = 0
    for ep in episodes:
        if wanted and ep.id not in wanted:
            continue
        print(engine.infer(ep).trace(ep))
        print()
        shown += 1
        if not wanted and shown >= args.limit:
            break
    if shown == 0:
        print("no matching episode", file=sys.stderr)
        return 2
    return 0


def cmd_verify(args) -> int:
    from shadow.verify.replay import main as verify_main

    sys.argv = ["replay"] + (["--allow-writes"] if args.allow_writes else [])
    return verify_main()


def cmd_serve(args) -> int:
    from shadow.serve.server import build_server

    cfg = get_config()
    server = build_server(cfg.path("tools"), allow_writes=args.allow_writes,
                          cfg=cfg, require_verified=not args.include_unverified)
    server.run()
    return 0


def cmd_bench(args) -> int:
    from shadow.bench.run import main as bench_main

    argv = ["run", "--conditions", args.conditions]
    if args.trials:
        argv += ["--trials", str(args.trials)]
    if args.allow_writes:
        argv.append("--allow-writes")
    if args.fresh:
        get_config().path("results").unlink(missing_ok=True)
    sys.argv = argv
    return bench_main()


def cmd_report(args) -> int:
    from shadow.bench.report import main as report_main

    sys.argv = ["report"]
    return report_main()


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="shadow", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)

    p = sub.add_parser("seed"); p.set_defaults(func=cmd_seed)

    p = sub.add_parser("observe")
    p.add_argument("--sessions", type=int, default=3)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--fresh", action="store_true", help="discard prior capture")
    p.set_defaults(func=cmd_observe)

    p = sub.add_parser("distill")
    p.add_argument("--show-stream", action="store_true")
    p.add_argument("--show-episodes", type=int, default=15)
    p.add_argument("--show-dataflow", action="store_true")
    p.set_defaults(func=cmd_distill)

    p = sub.add_parser("trace")
    p.add_argument("episode", nargs="?")
    p.add_argument("--limit", type=int, default=3)
    p.set_defaults(func=cmd_trace)

    p = sub.add_parser("verify")
    p.add_argument("--allow-writes", action="store_true")
    p.set_defaults(func=cmd_verify)

    p = sub.add_parser("serve")
    p.add_argument("--allow-writes", action="store_true")
    p.add_argument("--include-unverified", action="store_true")
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser("bench")
    p.add_argument("--trials", type=int, default=None)
    p.add_argument("--conditions", default="A_browser,B_tools")
    p.add_argument("--allow-writes", action="store_true")
    p.add_argument("--fresh", action="store_true")
    p.set_defaults(func=cmd_bench)

    p = sub.add_parser("report"); p.set_defaults(func=cmd_report)
    return ap


def main() -> int:
    args = build_parser().parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
