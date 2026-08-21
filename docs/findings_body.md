# Shadow — findings

Watch a person use a legacy web app, synthesize typed task-level tools from
the HTTP traffic, expose them over MCP, and measure what an agent saves by
calling those tools instead of driving the browser.

Everything below was produced by the pipeline in this repository against a
live ERPNext v15 instance seeded with deterministic data.

## Three findings about the harness, which hold whatever the benchmark says

These came out of trying to measure the thing, they are independently
verifiable, and they are the most transferable part of this project. Each one
produced numbers that looked like results and were artefacts.

**1. Anthropic silently declines to cache a prefix under about 1024 tokens.**
Not an error, not a warning — the request simply comes back with
`cache_read_input_tokens: 0`. Every one of the 108 rows in the first
full-model run reported zero cached tokens with correctly formed
`cache_control` blocks, and nothing anywhere indicated a problem. Condition
A's stable prefix was 230 tokens and condition B's was 606. The lesson
generalises past this one provider: a silent no-op on a cost optimisation is
invisible in exactly the place you would look for it, so assert on the
*measured* cache hit, not on the request you constructed. Two further details
are worth carrying: Anthropic's token count runs higher than the local
tokenizer's — a prefix measured at 1062 was billed as 1430 — and the caching
asymmetry turned out to run the *opposite* way from the assumption. Condition
A's prefix is identical for every task, so it is written once and read back
across the whole run; condition B's changes with whatever retrieval surfaced.
Over 54 runs, A wrote 1,430 tokens and read 397,540.

**2. An action that cannot fail is one the agent cannot recover from.** The
`save` action pressed Ctrl+S and returned unconditionally. A save blocked by
ERPNext's mandatory-field validation therefore reported `save ok`, and the
model — correctly trusting its tools — pressed save twenty more times until
its step budget ran out. Two whole task templates scored 0/9 on this. Once
`save` read the dialog and returned `NOT SAVED: Please fill the following
mandatory fields ... In Items, Delivery Date`, the model fixed the document
on the next step.

**3. A dialog nobody dismissed locked the agent out of the page.** The
validation modal from that failed save left a backdrop over the document, and
a backdrop intercepts pointer events. Every subsequent click timed out
against an element that was present, visible, and unreachable. That reads
exactly like a broken selector, and it is not: clicking the same cell in a
direct DOM probe always worked. It was the probe that found it — the model
runs never could have, because from inside the loop the two are
indistinguishable.

### A conclusion this project got wrong, and has corrected

An earlier revision of this document reported that `T09_create_sales_order`
and `T14_create_sales_invoice` were unreachable by a language model driving
the browser, and explained it as a limit of the model's action space: child
tables re-render as you interact with them, refs go stale, and the runs died
on fifteen-second click timeouts. It was a clean mechanism and it was wrong.

The composite actions the scripted recipe used — `grid`, `field`, `link`,
`select_field`, `save` — were implemented in `perform()` the whole time and
simply absent from the action schema the model is shown. The model could not
name what it was never told existed. With the three defects above fixed, both
templates go from 0/9 to 9/9 in both conditions, `T09` in twelve steps
instead of the twenty-five-step ceiling.

The generalisable form: before concluding a model cannot do something,
confirm it was ever able to express it. A capability that exists in the code
and not in the prompt is not a capability.

## The headline, stated with its conditions

Two regimes are measured, and they answer different questions.

**In-distribution** is the deployment case: watch someone do a workflow, then
automate that same workflow on inputs never observed. The holdout is over
instances.

**Held-out transfer** is the generalisation case: watch eight kinds of task,
then automate six kinds never watched. The holdout is over templates.

The contrast between them is the actual finding — how much of this system's
value depends on having observed the specific workflow — and both are in the
results tables below, measured on the same model, the same cost table and the
same accounting path.

On the held-out transfer set, with every harness defect above fixed, both
conditions reach **100% oracle-verified success on all six templates**, and
the tool condition is **1.3× more expensive per successful task** than the
browser alone. Coverage is 7% of actions and 0% of tasks: no held-out task
completes on synthesized tools alone. That is a negative result for transfer
and it is reported as measured.

The pre-fix and post-fix numbers are both in the tables, because the
difference between them is the evidence for the three findings above.

## The two rules, and how they are enforced

**The API docs are an oracle, never an input.** ERPNext publishes a full REST
API. It is used only to verify task success and to score synthesis as ground
truth. It lives in `oracle/`, and `tests/test_isolation.py` fails the build if
anything under `shadow/distill/` or `shadow/capture/` imports it — by AST
inspection of every module, and by checking that importing the synthesis
pipeline does not pull an `oracle` module into `sys.modules`.

**The held-out split is over task templates, not instances.** Fourteen
templates were partitioned 60/40 with a fixed seed *before* any capture, and
the split is persisted and never regenerated. Demonstration traffic was
produced only on the eight OBSERVE templates;
`bench/generate_traffic.py` calls `assert_observe_only()` for every task it is
about to drive and raises `HeldOutViolation` on an EVAL id.
`tests/test_heldout.py` asserts the refusal for every held-out template, and
asserts that the generator's source actually contains that call.

    OBSERVE  T01 customer outstanding · T02 overdue invoices ·
             T04 customer order count · T05 item price ·
             T07 items below price · T08 create customer ·
             T11 update item price · T13 update customer group

    EVAL     T03 stock on hand · T06 latest order total ·
             T09 create sales order · T10 create supplier ·
             T12 create item · T14 create sales invoice

Nothing about the EVAL templates was observed. Four of the six are creates on
record types the demonstrations never wrote.

## What the pipeline produced

Four demonstration sessions, 96 demonstrations across the eight OBSERVE
templates, driven through the ERPNext desk UI behind a mitmproxy capture.

| stage | result |
| --- | --- |
| raw flows captured | 1,606 |
| kept after filtering | 1,076 (67.0%) |
| — of which navigation markers | 98 |
| dropped as assets | 40 |
| dropped as websocket/socket.io frames | 386 |
| collapsed as polling | 104 |
| records inside episodes | 1,020 |
| episodes after segmentation | 87 |
| load-bearing records after the trim | 195 (19.1% of episode records, 12.1% of raw flows) |
| chrome endpoints found by document frequency | 3 |
| tools emitted (support ≥ 3) | 8 |
| tools verified against the live instance | 8 |

The 67% retention deserves a note, because the build spec expected well under
20%. The capture is host-scoped to the application and the whole
demonstration run shares one browser context, so assets are fetched once and
then served from cache for the remaining 95 demonstrations; what survives the
filter is already almost entirely API traffic. The compression the spec was
pointing at does happen — it happens one stage later. The load-bearing trim
takes 1,020 records inside episodes down to 195, which is 12.1% of the raw
capture. Three endpoints were identified as page chrome purely by document
frequency:

    GET  /api/method/frappe.desk.doctype.event.event.get_events
    GET  /api/method/frappe.desk.form.load.getdoctype
    POST /api/method/frappe.client.validate_link

None of them is in a hand-written list; they were found because they appear
in ≥ 80% of episodes regardless of what the user was doing.

### Did segmentation recover the tasks?

Episodes are cut from the traffic alone. The demonstration manifest — which
records what was driven and when, and which the synthesis pipeline never
reads — makes it possible to check afterwards
(`scripts/segmentation_check.py`):

* 96 demonstrations produced 87 episodes
* 9 episodes span more than one demonstration
* 2 overlap no demonstration at all (login, idle navigation)

So boundaries are recovered for roughly 90% of demonstrations. Labels are a
different story, and the failure is informative:

| demonstrated | most common label |
| --- | --- |
| T01 customer outstanding | `create or update sales invoice` ×7 |
| T02 overdue invoices | `create or update sales invoice` ×9 |
| T04 customer order count | `create or update sales order` ×7 |
| T05 item price | `create or update item price` ×8 |
| T07 items below price | `create or update item price` ×10 |
| T08 create customer | `submit customer` ×10 |
| T11 update item price | `submit item price` ×10 |
| T13 update customer group | `view customer` ×6 |

The *subject* is right almost every time — the labeller identifies the record
type the user was working on from the traffic. The *verb* is wrong on the
reads, because the deterministic labeller sees the desk saving list-view
settings with a POST and concludes something was created. This is exactly the
judgement an LLM labeller is for, and it is the one place in the pipeline
where the offline stand-in is clearly worse than a model would be. It does
not affect the tools: induction groups on request signatures, not on labels;
labels only feed naming, where the mutation classifier overrides them.

### The interesting tool

`update_item_price` is the one to look at, because it is the thing an OpenAPI
extractor cannot produce:

    [0] POST /api/method/frappe.desk.reportview.get
          body.filters            <- param(filters)
    [1] GET  /api/method/frappe.desk.form.load.getdoc
          query.name              <- step[0] $.message.values[0][0]
    [2] POST /api/method/frappe.desk.form.save.savedocs
          body.doc.name           <- step[0] $.message.values[0][0]
          body.doc.owner          <- step[1] $.docs[0].owner
          body.doc.creation       <- step[1] $.docs[0].creation
          body.doc.modified_by    <- step[1] $.docs[0].owner
          body.doc.item_code      <- step[1] $.docs[0].item_code
          body.doc.item_name      <- step[1] $.docs[0].item_name
          body.doc.uom            <- step[1] $.docs[0].uom
          body.doc.price_list     <- step[1] $.docs[0].price_list
          body.doc.currency       <- step[1] $.docs[0].currency
          body.doc.valid_from     <- step[1] $.docs[0].valid_from
          body.doc.price_list_rate<- param(price_list_rate)
          body.doc.modified       <- param(modified)

Find the record, load it, write it back with eight fields carried across from
the load response and the price left as a parameter. The dataflow was
inferred from traffic alone — no schema, no documentation.

Two details are worth noticing. `doc.modified` came out as a parameter rather
than a carried field, because Frappe's optimistic-concurrency timestamp is
the one field the client does *not* echo unchanged; the inducer could not
explain it from any response, so it asked the caller for it. That is the
correct conservative answer, and it is also the kind of thing a
documentation-driven integration gets wrong. Second, `filters` is a
parameter, so the tool is not pinned to the item it was observed editing —
but it *is* pinned to Item Price, because `doc.price_list` and the filter's
record type never varied in the demonstrations.

`list_records` is the one that generalises. Induced from list-view traffic on
Item Price, Sales Invoice and Sales Order, its `doctype` came out as a
parameter rather than a constant because it varied across episodes. Pointed
at `Bin` — a record type no demonstration ever touched — it returns stock
rows correctly. That is the whole thesis in one call.

## Results

Read these together with the section that follows, which states how they
were produced: `claude-sonnet-5` in the loop for both conditions, success
oracle-checked against the database, token counts read from the provider,
latency inclusive of inference, and cost reported both as billed and with
the cache discount removed.

<!-- GENERATED TABLES -->

### Charts

![Coverage on held-out templates vs observation volume](artifacts/charts/coverage_vs_sessions.png)

Coverage against how much of the same user's work was observed. Flat: the
tool that transfers is induced from the first session, and more of the same
kind of work adds depth rather than reach.

![Cost per successful task](artifacts/charts/cost_per_successful_task.png)

Cost per successful task, both conditions. B is higher, and the reason is in
the per-template table above: on the five templates no tool covers, B pays
for a failed tool attempt *and* the browser run.

![Task latency distribution](artifacts/charts/latency_distribution.png)

Latency per task. The distributions overlap almost entirely — except for B's
lower tail at 2.4s, which is the covered template. That tail is the whole
effect, and it is what more coverage would multiply.

## How the numbers were produced

**A real model is in the loop.** Both conditions run `claude-sonnet-5`
through litellm against the Anthropic API. Every result row carries
`usage.simulated: false`, token counts are read from the provider's own usage
object rather than from a tokenizer estimate, and both conditions are costed
by one function from the price table in `config.yaml`. The earlier revision
of this document reported numbers from deterministic stand-in policies
because the build environment had no credentials; those runs are kept, not
deleted, under `artifacts/results_offline*.jsonl`, and every arm produced
that way is labelled `_offline` so it cannot be mistaken for this one.

What that changes, relative to the previous revision:

* **Latency now includes inference.** The old wall clock was harness latency
  only — browser actions, HTTP calls, page settling. It now includes the
  model's thinking time on every step, which is the number a user would feel.
* **The router is the model.** Condition B's tool selection and argument
  filling are done by the model from the retrieved schemas. The lexical
  matcher it replaces is still in the tree as the offline fallback, and the
  gap between the two is reported below rather than assumed.
* **The baseline got weaker, and that is the point.** Condition A used to be
  a scripted UI recipe. The previous revision argued that this made the
  baseline *stronger* than an LLM would be, because a recipe never explores,
  never backtracks, never misreads a page, and edits form fields with
  composite actions where a model needs a click and a type. That was an
  assertion. It is now a measurement, and it held.

### Prompt caching, and why it is reported twice

Caching is not neutral between these two conditions. Condition B's prompt has
a fixed prefix — instructions plus the retrieved tool schemas — that is
identical across steps and cacheable. Condition A re-sends a page snapshot
that changes on every step and cannot be cached. Any cache discount therefore
flows to B by construction, and banking it silently would be taking an
advantage the reader cannot see.

So cost is reported twice throughout: as billed, and again with cache reads
repriced at the full input rate. Cache writes are billed at 1.25x input in
both rows. The implementation marks the fixed prefix `cache_control:
ephemeral` for Anthropic models, identically in both conditions.

The measured cache hit rate is in the token rows of the results table. It is
worth reading before drawing a conclusion from the caching argument above: a
cacheable block must reach 1024 tokens before Anthropic will cache it at all,
and these prompts are small enough that the question may be moot in practice
even though it is real in principle. A separate check with an 18KB prefix
confirms the mechanism works end to end — 5,610 tokens written, then read
back — so a zero in those rows is the minimum biting, not a broken path.

### What is still true of the grading

* **Success is oracle-checked.** Every run is graded by `oracle/checks.py`
  against the ERPNext REST API — a numeric answer compared to a value
  computed from the database, or, for a write, the row that must now exist.
  An agent that claims success without changing anything fails.
* **The oracle grades a post-condition, so it is blind to side effects.** A
  run that creates the right record *and* three wrong ones scores as a pass.
  That is not hypothetical: it is what the lexical router did, and the
  collateral-write table measures whether the model repeats it.
* **Coverage is reported twice.** **Achieved** is what the router actually
  got. **Attainable** is whether *any* verified tool can complete the task
  when the oracle supplies the arguments — the ceiling a perfect router would
  hit. The gap between them is a routing problem; the ceiling itself is a
  synthesis problem, and they need different fixes.

### The one thing that would still change these numbers

The demonstrations are a single operator's sessions against a single seeded
instance. Coverage is a property of what was watched, and the volume sweep
below shows it saturating early — more of the *same* work adds depth, not
reach. Nothing here establishes what happens when the observed work is
broader than the held-out work, because that experiment needs a second
operator, not a longer run.

## Where the tokens actually are

Measured per-task input tokens for the browser baseline, by held-out
template:

| template | steps | input tokens | per-step |
| --- | --- | --- | --- |
| T03 stock on hand (read) | 3 | 1,986 | 410 / 780 / 796 |
| T06 latest order total (read) | 3 | 2,243 | 405 / 911 / 927 |
| T09 create sales order (write) | 10 | 13,782 | 412 → 1,595 |
| T14 create sales invoice (write) | 9 | 13,840 | 414 → 1,760 |

A list page is cheap to look at and takes three steps. A document form is
expensive to look at — more interactive elements, more text — and takes nine
or ten. The cost of driving a browser is concentrated almost entirely in the
write tasks, and those are precisely the templates synthesis could not cover.

### The catalog is not free to carry

The first measured run made this concrete in an unflattering way. Every
induced parameter carried its full observed value as `examples`, and for a
list query that value is the entire backtick-qualified column list of
whichever record type happened to be captured. Across eight tools that came
to **7,905 characters of prompt on every single step** — larger than the
accessibility snapshot the tools exist to replace. Condition B cost
$0.01513 per successful task.

Rendering the same tools by shape instead — type, enum, and one example
truncated to 60 characters — cut the catalog to 4,096 characters, the covered
template's per-step input from 2,256 tokens to 1,226, and condition B to
$0.01221. Both numbers are in the repository
(`artifacts/results_offline_verbose_schema.jsonl` holds the pre-fix run).

The general point survives the fix: a tool catalog is a fixed tax on every
prompt, paid whether or not any tool is used. Eight tools cost roughly what
an ERPNext list page costs to look at. That is the arithmetic that decides
whether synthesis pays for itself, and it argues for serving a *retrieved*
subset of tools rather than the whole catalog.

That is what the router now does. Tools are scored against the goal with
BM25 over their names, descriptions, parameters and enum values, and only
the top `k` reach the prompt; a goal that clears no tool's score floor gets
no catalog at all and pays nothing for one. The k-sweep table above measures
what that is worth, with `k=0` as the browser baseline reached through the
tool agent and the largest `k` reproducing the whole-catalog behaviour that
lost.

That sets the real prize and the real result apart. A tool-served task in
condition B costs about 2,500 input tokens: one call plus a finish, each
carrying the tool catalog. Against a read template's 2,000 that is roughly
parity — tools buy latency there, not tokens. Against a write template's
13,800 it would be a ~5× saving. Synthesis did not cover those templates, so
that number is a projection from measured quantities, not a result. It is
also the clearest statement of what the missing capability in finding 1 is
worth.

## What failed to synthesize, and why

This is the section worth reading. Coverage on held-out templates is not
uniform, and the pattern is systematic rather than random.

**1. Writes do not transfer to unobserved record types — structurally.**
Four of the six EVAL templates create a record type the demonstrations never
wrote: Supplier, Item, Sales Order, Sales Invoice. A synthesized write tool
carries the record's *fields* as its parameters, because that is what varied
in the observed traffic. `create_customer` exposes `customer_name`; there is
no parameter through which a caller could set `supplier_name`, because no
demonstration ever set one. Even the generalised `create_record` tool, which
did get `doctype` as a parameter through the backoff pass, can only populate
fields it has seen. The record type is a parameter; the record's schema is
not. This is a genuine limit of synthesis-from-observation, not a bug: you
cannot induce a field nobody ever filled in.

The corollary is a scaling prediction the design supports: observing writes
across *n* record types should turn the doctype into a parameter and the
union of their fields into optional parameters, at which point the marginal
cost of the (n+1)th record type is only its own fields. Testing that
requires an OBSERVE split with more write diversity than this one has, which
would mean regenerating the split — and the split is fixed.

**2. Reads transfer when the query endpoint is generic, and not otherwise.**
Frappe routes every list view through one endpoint with the record type in
the body, so `list_records` generalises to any record type — including `Bin`,
which the router only fails to reach because it cannot *name* it from the
goal ("total actual quantity in stock" does not contain the word "Bin"). An
application that routed each list view through its own endpoint would give a
per-record-type tool with no transfer at all. How far synthesized reads
generalise is a property of the application's API shape, not of the method.

**3. Enum induction and transfer pull against each other.** A parameter whose
observed values are few gets a JSON-Schema enum. That is right for a status
field and wrong for a record type: `list_records` was observed with three
record types and would, under a naive rule, advertise a closed domain of
three — telling a schema-obeying model that `Bin` is not allowed. The
mitigation implemented here is to require the value set to *saturate* before
calling it an enum (distinct values must repeat), which also fixed a routing
bug where a tool won a goal purely because a customer name from its capture
appeared in the text. The tension does not disappear: any closed-domain
signal is also a transfer barrier.

**4. A naive router with writes enabled created records nobody asked for.**
This is the one to take seriously. With `--allow-writes`, the lexical router
answered "Create a sales order for customer 'Juniper Analytics' with 12 units
of item 'SH-SENSOR-01'" by selecting `create_customer` — a tool that creates
customers — with `customer_name: "Juniper Analytics"`. The call succeeded.
ERPNext created a duplicate customer named `Juniper Analytics - 1`. The
router then fell back to the browser, which created the sales order, the
oracle check passed, and **the run is recorded as a success with a stray
record in the database**.

It happened 9 times in 54 runs: 6 on create-sales-order and 3 on
create-sales-invoice, every one of them scored as a pass. The benchmark reset
between tasks hides the damage; a production system has no such reset.

That was the lexical router, and the obvious objection is that a capable
model would not make so crude a substitution. So the experiment was repeated
against one: the same create-type held-out templates, writes enabled, and a
diff of every watched record type taken around each run so that collateral is
measured rather than inferred from the trace. The result is the
collateral-writes table above. Note what the measurement does *not* settle —
a model that avoids this particular substitution has not been shown to be
safe, only to be better at one case; the gating argument below rests on the
oracle's blindness to side effects, which no choice of router changes.

Two things are worth separating here. The synthesized tool did exactly what
it was synthesized to do — `verify/replay.py` had confirmed against a reset
database that it creates a customer, and it created a customer. The failure
is entirely in the thing choosing which tool to call and with what
arguments, and a lexical matcher is a *weak* such thing. But that is the
point: a synthesized write tool is only as safe as its caller, the caller is
a language model, and the oracle check that scores the task cannot see the
collateral. This is the argument for the gating that is already the default —
`--allow-writes` is off in the verifier, off in the MCP server, and off in
the agent — and for something this project does not have: a post-condition
assertion on the tool itself, so that `create_customer` invoked in service of
"create a sales order" is rejected rather than merely regretted.

The ablation above measures what the gating costs, and the answer is nothing:
with writes gated off, success stays at 100%, coverage stays at 17% (the
covered template is a read), failed tool calls drop from 36 to 9, cost per
successful task falls from $0.01221 to $0.00974, and no synthesized tool can
touch the database. Exposing the write tools bought no capability on this
split and created nine records nobody asked for.

**5. The compounding curve does not compound — within one workload.** The
sweep runs the whole pipeline over increasing prefixes of the same capture.
More observation buys more tools (2 → 7 → 7 → 8) and more episodes
(23 → 46 → 66 → 87), but held-out coverage is flat: attainable coverage sits
at its ceiling of 33% after a *single* session, and achieved coverage does
not move at all. The generic list tool — the one that transfers — is induced
from the first session's traffic, because the OBSERVE templates already vary
the record type between them. Everything after that adds depth on task types
already seen. The reading is that observation volume and observation
*variety* are different axes, and only the second one moves held-out
coverage. A curve that rises would need new kinds of work, not more of the
same. (The sweep verifies reads only, so its verified-tool count is 4 rather
than the 8 of the main run, and it predates the prompt-size fix below.)

**6. Segmentation is easy here and would not be in production.** The
demonstrations are separated by deliberate idle gaps, which is the easy case.
A real user's session interleaves tasks, abandons them, and comes back; the
LLM refinement pass (label + coherence + split) exists for that and was
exercised only lightly here.

**7. Eight tools, not fifteen.** The acceptance target of ≥15 verified tools
with support ≥3 is not reachable from this observation set: it contains eight
task types, and the emitted catalog is deliberately curated rather than a
long tail. Reaching fifteen would mean either lowering `min_support` below
the point where a signature is evidence of anything, or observing more kinds
of work. The support distribution is in
`artifacts/induction_diagnostics.json`.

## Prior art

**mitmproxy2swagger** turns a capture into an OpenAPI document: paths,
methods, parameter names, example schemas. It documents an endpoint surface.
It does not infer where a value came from, does not group requests into user
tasks, and emits nothing executable.

**mitmproxy-mcp** exposes captured flows to a model over MCP so it can search
and read them — a debugging surface over the capture. The application's
operations stay data; the tools are "search flows", "read a flow".

**WALT — Web Agents that Learn Tools** ([arXiv:2510.01524](https://arxiv.org/abs/2510.01524),
Salesforce AI Research, Oct 2025) reverse-engineers functionality already
built into a website — search, filter, sort, post, comment, create, edit,
delete — into deterministic high-level calls, discovered and validated by an
agent driving the site. It reports 52.9% success on VisualWebArena and 50.1%
on WebArena, with roughly 1.4× fewer steps and 21.3% fewer actions.

**SkillWeaver** ([arXiv:2504.07079](https://arxiv.org/abs/2504.07079), Apr
2025) has an agent explore a site under an LLM-generated curriculum, practise
candidate skills, and distil the successful ones into reusable APIs. It
reports relative success-rate improvements of 31.8% on WebArena and 39.8% on
real sites, and skills from a strong agent lifting a weaker one by up to
54.3%.

**UiPath Task Mining** records what users do across desktop applications and
mines the recordings for automation candidates, producing process
documentation and RPA suggestions for a human to implement.

**Where this differs.** The input is passive: no exploration, no crawler, no
agent in the loop during capture — only traffic from someone doing their job.
That matters when the app is a production ERP, where exploration means
creating real invoices. The output carries dataflow: a tool is a sequence of
calls with `from_response` bindings that lift values out of earlier steps,
which is what makes a list-then-load-then-save update possible rather than a
single-request replay. And the evaluation is held out over task *templates* —
tools are synthesized from one set of task types and measured on a disjoint
set. Coverage measured on the templates you observed is memorisation; that
distinction is what the whole design turns on.

## Reproducing

```bash
sudo -u frappe bash infra/setup_erpnext.sh   # or docker compose -f infra/pwd.yml up -d
sudo bash infra/start_erpnext.sh
bash scripts/run_all.sh 4 3                  # 4 observation sessions, 3 trials
```

`scripts/run_all.sh` runs seed → observe → distill → verify → bench →
attainable → sweep → report, unattended, from a clean instance. Details in
`docs/reproduce.md`; design rationale in `docs/design.md`.
