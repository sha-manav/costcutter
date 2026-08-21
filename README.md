# Shadow — an executable evaluation environment for agent work on a real ERP

An agent benchmark on **ERPNext v15**, a real enterprise application, where
every task is graded by reading the database back through the REST API rather
than by judging the agent's text.

Eighteen task templates across two holdout regimes, a browser agent with a
Frappe-aware action space, deterministic seed data, and a one-command reset
between runs. The model is a parameter.

```
18 templates  ·  54 evaluated instances  ·  2 conditions  ·  reset every run
held-out: 6 templates / 18 instances   in-distribution: 12 / 36
```

**Results:** [FINDINGS.md](FINDINGS.md) — including the parts that did not
work, and one earlier conclusion this project got wrong and has corrected.
**Frozen harness:** [HARNESS.md](HARNESS.md).

## Why this environment

Most agent benchmarks grade a transcript. This one grades the database.

* **Programmatic verification.** Every check in `oracle/checks.py` queries
  ERPNext over REST and compares against computed state — a number derived
  from the tables, or the row that must now exist with the right fields. An
  agent that says it created the sales order and did not, fails.
* **A real application, not a mock.** ERPNext v15 with its actual desk UI:
  autocomplete link fields, child-table grids that re-render as you type,
  mandatory-field validation that blocks a save, modal dialogs that intercept
  clicks. The failure modes are the ones real software has.
* **Identical starting state.** Every task begins from a restored snapshot
  (~6s), so runs are independent and writes are safe to score.
* **Two holdout regimes**, because they answer different questions —
  generalising to unseen *kinds* of work, versus automating work you watched.

## The action space

The browser agent gets five primitives — `click`, `type`, `select`,
`navigate`, `scroll` — plus five composite actions that make Frappe's desk
tractable. **The composite actions are the contribution.** Without them, a
model cannot express a child-table edit at all, and two whole task templates
score 0/9 on timeouts against cells that never become clickable.

| Action | What it handles |
|---|---|
| `field` | plain input or textarea, addressed by fieldname |
| `link` | a foreign key — an autocomplete widget, so typing alone leaves it unset |
| `select_field` | a Select dropdown |
| `grid` | one cell of one child-table row, including columns only shown in the row editor |
| `save` | Ctrl+S, then **reports whether the save actually happened** |

A worked document creation, which is what the whole action space exists for:

```json
{"action": "navigate", "url": "/app/sales-order/new"}
{"action": "link",  "field": "customer", "value": "Juniper Analytics"}
{"action": "grid",  "field": "items", "row": 0, "column": "item_code",
 "value": "SH-SENSOR-01", "is_link": true}
{"action": "grid",  "field": "items", "row": 0, "column": "qty", "value": 12}
{"action": "save"}
{"action": "done",  "answer": "created"}
```

Two design rules, both learned the hard way and both in FINDINGS:

* **`save` can fail.** It returns `NOT SAVED: <the validation error>` so the
  agent can fix the document. An action that cannot fail is one the agent
  cannot recover from.
* **A blocking modal is dismissed before any action touches the page.** A
  modal backdrop swallows pointer events, and every later click then times out
  against an element that is present, visible and unreachable.

## The task sets

| Set | Split | Templates | Instances | Where |
|---|---|---|---|---|
| Held-out | over **templates** — eight kinds observed, six never seen | 6 | 18 | `shadow/bench/tasks.py` |
| In-distribution | over **instances** — same workflows, unseen parameters | 12 | 36 | `shadow/bench/indist.py` |

Reads ask for a number or a name that must match the database. Writes must
leave the right row behind: `oracle/checks.py` re-reads the created document
and checks its child rows, so "a sales order exists" is not enough — it must
carry the requested item at the requested quantity.

Both sets include child-table workflows (sales order, sales invoice,
quotation, purchase order), which are the highest-token tasks and the ones
that break naive action spaces.

## Quick start

```bash
sudo -u frappe bash infra/setup_erpnext.sh   # or: docker compose -f infra/pwd.yml up -d
sudo bash infra/start_erpnext.sh
export ANTHROPIC_API_KEY=...                 # environment only; never a config field
bash scripts/run_all.sh 4 3                  # observe 4 sessions, bench 3 trials
```

That runs seed → observe → distill → verify → bench → report unattended from a
clean instance. Step-by-step instructions are in
[docs/reproduce.md](docs/reproduce.md).

Benchmark only, against an already-seeded instance:

```bash
python -m shadow.cli bench --require-model --trials 3            # held-out
python -m shadow.bench.indist_pipeline bench --require-model     # in-distribution
python -m shadow.cli report                                      # metrics + charts
```

`--require-model` refuses to run rather than silently falling back to the
offline deterministic provider — which is how an early revision of this
project produced numbers that looked like model numbers and were not.

Swap the model with `--model <id>`; it must have an entry in `costs:` in
`config.yaml` or scoring refuses the run. Long runs survive container
restarts under `scripts/supervise.sh`.

## Adding a task

1. Add a `TaskTemplate` to `shadow/bench/tasks.py` (or `indist.py`) with a
   goal string, `kind`, the name of its check, and parameter sets.
2. Add the check to `oracle/checks.py` and register it in `CHECKS`. Read the
   expected value out of the database; never trust the agent's answer.
3. Add a recipe to `shadow/route/recipes.py` if the task needs demonstration
   traffic. Recipes are generators that yield actions.
4. `pytest` — the suite enforces the holdout invariants, including that no
   in-distribution template writes a record type the held-out set writes.

## Layout

```
shadow/
  capture/   mitmproxy addon, typed artifacts
  distill/   filter · segment · provenance · induce · classify · emit
  verify/    replay synthesized tools against the live app
  serve/     FastMCP server, ToolSpec executor
  route/     browser agent (A), tool-first agent (B), action space, recipes
  bench/     task registry, traffic generation, runner, metrics, charts
oracle/      REST oracle, seed, reset, success checks — never imported by distill/
```

## The tool-synthesis experiment

The environment was built to test a hypothesis: watch a person work, induce
typed tools from the observed HTTP traffic, serve them over MCP, and an agent
calling those tools should beat one driving the browser.

It was tested in both regimes and **it lost** — condition B is more expensive
at equal success. The synthesized tools are real, verified against a live
instance, and induced without reading a line of API documentation; they are
simply too fine-grained to move the economics. The pipeline, the induced
dataflow, and the full negative result are in [FINDINGS.md](FINDINGS.md).

The two rules that kept that experiment honest still hold in the code:

1. **The API docs are an oracle, never an input.** ERPNext publishes a full
   REST API, used only to verify success and to score synthesis as ground
   truth. It lives in `oracle/`; `shadow/distill/` may not import it, and
   `tests/test_isolation.py` fails the build if it does — by AST inspection,
   and by checking that importing the pipeline pulls no `oracle` module into
   `sys.modules`.
2. **The held-out split is over templates, not instances.** Partitioned before
   any capture, persisted, never regenerated. Demonstration traffic is refused
   for an EVAL id — `generate_traffic.py` raises `HeldOutViolation`.

## License

MIT — see [LICENSE](LICENSE).
