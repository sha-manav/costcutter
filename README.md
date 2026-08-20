# Shadow

Watch someone use a legacy web app, synthesize typed task-level tools from
the HTTP traffic, expose them over MCP, and measure what an agent saves by
calling those tools instead of driving the browser.

```
capture ──▶ filter ──▶ segment ──▶ provenance ──▶ induce ──▶ verify ──▶ serve
   mitmproxy   noise      episodes    dataflow      ToolSpec   replay    MCP
```

## The two rules

1. **The API docs are an oracle, never an input.** ERPNext publishes a full
   REST API. It is used only to verify task success and to score synthesis
   as ground truth. `oracle/` holds it; `shadow/distill/` may not import
   from `oracle/` and `tests/test_isolation.py` fails the build if it does.
2. **The held-out split is over task templates, not instances.** Templates
   are partitioned into OBSERVE and EVAL before any capture happens.
   Demonstration traffic is generated only on OBSERVE templates —
   `bench/generate_traffic.py` refuses an EVAL template id — and every
   headline number is reported on EVAL.

## Quick start

```bash
bash infra/setup_erpnext.sh          # or: docker compose -f infra/pwd.yml up -d
bash infra/start_erpnext.sh
python -m shadow.cli seed            # deterministic data + seed snapshot
python -m shadow.cli observe --sessions 3
python -m shadow.cli distill
python -m shadow.cli verify
python -m shadow.cli bench --trials 3
python -m shadow.cli report
```

See `FINDINGS.md` for results and `docs/` for design notes.
