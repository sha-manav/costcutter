# Shadow

Watch a person use a legacy web app, synthesize typed task-level tools from
the HTTP traffic, expose them over MCP, and measure what an agent saves by
calling those tools instead of driving the browser.

```
capture ──▶ filter ──▶ segment ──▶ provenance ──▶ induce ──▶ verify ──▶ serve
mitmproxy    noise      episodes    dataflow      ToolSpec    replay     MCP
```

Results and an honest account of what did not work: **[FINDINGS.md](FINDINGS.md)**.
Design rationale: [docs/design.md](docs/design.md). Prior art:
[docs/prior_art.md](docs/prior_art.md).

## The two rules

1. **The API docs are an oracle, never an input.** ERPNext publishes a full
   REST API. It is used only to verify task success and to score synthesis as
   ground truth. It lives in `oracle/`; `shadow/distill/` may not import from
   it, and `tests/test_isolation.py` fails the build if it does — by AST
   inspection and by checking that importing the pipeline does not pull an
   `oracle` module into `sys.modules`.
2. **The held-out split is over task templates, not instances.** Templates are
   partitioned into OBSERVE and EVAL before any capture, with a fixed seed,
   and the split is persisted and never regenerated. Demonstration traffic is
   produced only on OBSERVE templates — `bench/generate_traffic.py` raises
   `HeldOutViolation` on an EVAL id — and every headline number is reported on
   EVAL.

## What a synthesized tool looks like

```
update_item_price  [write]  support=9
  [0] POST /api/method/frappe.desk.reportview.get
        body.filters             <- param(filters)
  [1] GET  /api/method/frappe.desk.form.load.getdoc
        query.name               <- step[0] $.message.values[0][0]
  [2] POST /api/method/frappe.desk.form.save.savedocs
        body.doc.name            <- step[0] $.message.values[0][0]
        body.doc.item_code       <- step[1] $.docs[0].item_code
        body.doc.price_list      <- step[1] $.docs[0].price_list
        body.doc.currency        <- step[1] $.docs[0].currency
        body.doc.price_list_rate <- param(price_list_rate)
```

Find the record, load it, write it back with every field carried across from
the load and one field left as a parameter. The dataflow was inferred from
traffic alone — no schema, no documentation.

## Quick start

```bash
sudo -u frappe bash infra/setup_erpnext.sh   # or: docker compose -f infra/pwd.yml up -d
sudo bash infra/start_erpnext.sh
bash scripts/run_all.sh 4 3                  # 4 observation sessions, 3 trials
```

`scripts/run_all.sh` runs seed → observe → distill → verify → bench →
attainable → sweep → report unattended from a clean instance. Step-by-step
instructions, including how to run with a real model, are in
[docs/reproduce.md](docs/reproduce.md).

The containerised `frappe_docker` path is the one to prefer. The native
`infra/setup_erpnext.sh` exists because the sandbox this was built in cannot
reach container-registry blob CDNs; it installs the same ERPNext version from
source and produces the same site.

## Layout

```
shadow/
  capture/   mitmproxy addon, typed artifacts
  distill/   filter · segment · provenance · induce · classify · emit
  verify/    replay synthesized tools against the live app
  serve/     FastMCP server, ToolSpec executor
  route/     tool-first agent (B) and browser baseline (A)
  bench/     task registry, traffic generation, runner, metrics, charts
oracle/      REST oracle, seed, reset, success checks — never imported by distill/
```
