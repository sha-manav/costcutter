# Shadow — findings

Watch a person use a legacy web app, synthesize typed task-level tools from
the HTTP traffic, expose them over MCP, and measure what an agent saves by
calling those tools instead of driving the browser.

Everything below was produced by the pipeline in this repository against a
live ERPNext v15 instance seeded with deterministic data.

## The headline, stated with its conditions

On six held-out ERPNext task templates — disjoint from the eight the
demonstrations covered — **4.7% of agent actions and 17% of tasks were served
by tools synthesized from observed traffic**, at 100% oracle-verified success
in both conditions.

On the one held-out template a synthesized tool actually covered, the tool
path took **2 steps and 2.4s p95 against the browser's 3 steps and 12.7s — a
5.3× latency drop** — for the same verified answer.

**Cost did not fall.** Per successful task, condition B cost $0.0122 against
condition A's $0.0082: 1.5× *more*. On the 83% of held-out tasks no tool
covered, the agent pays for a failed tool attempt and then drives the browser
anyway, and the tool catalog rides in every prompt. Even on the covered
template the two are near parity ($0.00289 vs $0.00252), because an ERPNext
list page is cheap to look at and a catalog of eight tool schemas is not.

Both conditions ran on deterministic stand-in policies rather than a model —
no API credentials were available in the build environment. Success is
oracle-checked and real, token counts are measured from the real prompts, and
latency excludes model inference. The section
[How the numbers were produced](#how-the-numbers-were-produced-and-what-they-do-not-include)
states exactly what that changes and in which direction; the short version is
that every bias runs against the result above.

The rest of this document is mostly about *why* coverage is 17% and not
higher, because that turns out to be the interesting part.

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

Read these together with the section that follows, which states exactly what
the numbers do and do not include: no model was in the loop, so success is
oracle-checked and real, token counts are measured from the real prompts,
latency excludes inference, and both stand-in policies are biased against the
result being shown.

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

## How the numbers were produced, and what they do not include

**No model was in the loop.** The build environment has no API credentials
for any provider, so both conditions ran on the deterministic providers in
`shadow/llm.py`. This is stated up front because it changes how each number
should be read, and every result row carries `usage.simulated: true`:

* **Success is real.** Every run is graded by `oracle/checks.py` against the
  ERPNext REST API — a numeric answer compared to a value computed from the
  database, or, for a write, the row that must now exist. An agent that
  claims success without changing anything fails.
* **Token counts are real measurements, not estimates of a hypothetical.**
  Both conditions build the exact prompt they would have sent — same system
  prompt, same accessibility snapshot, same tool schemas, same history
  window — and tokenise it with the provider tokenizer through
  `litellm.token_counter`. What is simulated is the *decision*, not the
  prompt. Cost is then computed from the published price table in
  `config.yaml` by the same function for both conditions.
* **Latency excludes model inference.** The reported wall clock is harness
  latency: browser actions, HTTP calls, page settling. Inference time would
  be added per step, and condition A takes several times more steps than
  condition B, so including it would widen the gap rather than narrow it.

Both stand-in policies are biased *against* the result this project is trying
to show:

* Condition A's policy is a scripted UI recipe. It never explores, never
  backtracks, never misreads a page, and it edits form fields with composite
  actions where a model would need a click and a type. It is a stronger and
  cheaper baseline than an LLM driving the same UI.
* Condition B's router is a lexical matcher over tool names, descriptions and
  schemas. It has no knowledge of the task templates and no mapping from
  goals to tools; it guesses a record type from the goal's words and a filter
  field from the noun in front of a quoted value. It is much weaker than a
  model at exactly the job models are good at.

So the A-vs-B ratios above are lower bounds, and so is the achieved
coverage. To separate the catalog's capability from the router's, coverage
is reported twice: **achieved** (what the lexical router actually got) and
**attainable** (whether *any* verified tool can complete the task when the
oracle supplies the arguments — the ceiling a perfect router would hit).

Running with a real model is one flag: set `models.provider: litellm` and
export credentials, then `python -m shadow.cli bench --trials 3 --fresh`.

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
(`artifacts/results_verbose_schema.jsonl` holds the pre-fix run).

The general point survives the fix: a tool catalog is a fixed tax on every
prompt, paid whether or not any tool is used. Eight tools cost roughly what
an ERPNext list page costs to look at. That is the arithmetic that decides
whether synthesis pays for itself, and it argues for serving a *retrieved*
subset of tools rather than the whole catalog — which this project does not
do.

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
