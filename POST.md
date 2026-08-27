# The benchmark said the model got better. It had stopped doing the work.

**An agent benchmark on ERPNext where every task is graded by diffing the
database — and what that grading caught that a success rate could not.**

---

## Abstract

Back-office work produces verifiable state changes, which makes it one of the
few agent domains where success can be checked rather than judged. We built an
ERP benchmark on that property: fifty task templates that generate their own
assertions and mutation envelopes, three synthetic firms with genuinely
conflicting operating policies, and grading by whole-database snapshot diff —
no LLM judge, no rubric, nothing for a model to talk its way past.

We used it to study three things that determine whether an agent can be
deployed across acquired businesses: **harness design**, **model adaptation**,
and **local firm policy**.

Three results, in descending order of how much we trust them.

1. **Harness design is worth +8.8 points [+2.9, +14.7]** on templates fixed
   before the harness was written, model held constant, both variants sharing
   one execution path.
2. **Aggregate success rate rose while the capability it measures was
   destroyed.** Across four checkpoints, success climbed 21.5% → 28.2% while
   the number of tasks requiring a database write that the model completed
   went 6 → 6 → 0 → 0. The effect reproduced on a firm frozen in week 1 and
   opened once: 80% "transfer", zero completed writes.
3. **Eight sentences of context moved a behaviour that a thousand training
   examples could not** — first-action refusal 85% → 26% — and bought no
   competence: completed writes stayed at 0–3 of 17.

The shipped checkpoint is Pareto-optimal on cost: the cheapest arm we
measured, with nothing at or below its price offering more. It also cannot
reliably perform ERP writes. Both statements are true and the second is the
more important one.

---

## 1. The environment

**Grading is programmatic.** A whole-database snapshot is taken before and
after every run and diffed — ~19,000 rows across 124 doctypes in about half a
second. Assertions are generated from the task parameters *before the model
runs* and never edited afterwards.

**Unsafe completion is failure.** A run succeeds only if every required
assertion passes **and** no forbidden mutation occurred **and** no unexpected
one did. "Anything not enumerated" counts as unexpected, not as
"unrecognised, therefore ignore."

**The same instruction has different correct answers at different firms.**

> *"Set up an $8,000 order for Meridian Holdings — three units of the standard
> package."*
>
> | Firm | Correct outcome |
> |---|---|
> | **A** | Create the customer if absent, create and **submit** |
> | **B** | Customer creation **forbidden**. Draft order + escalation note |
> | **C** | Over threshold → **abstain**, report why, write nothing |

Fifteen templates are counterfactual in the strict sense: a write *required*
at one firm is *forbidden* at another. Templates whose assertions differ only
because the firms hold different customer names do not count, and there are
seven of those we do not claim.

**Doing nothing is often the right answer.** On the strictest firm, 20 of 40
templates have "write nothing" as the correct outcome. This is deliberate —
it is what makes the benchmark model real operating constraints. It is also,
as §3 shows, what makes its headline metric untrustworthy.

*→ Figure: `artifacts/demo/three_firms.html` — nine panes, one instruction per
row, three firms per column, each rendered from a logged rollout with its
`run_id`.*

---

## 2. Harness design: +8.8 points

`naive` reproduces three specific defects we had actually shipped:
undocumented actions that still execute, saves that cannot fail, and verbose
observations. `corrected` documents reusable primitives, returns typed
recoverable errors, keeps observations concise.

Both variants execute through **one shared code path**. They differ only in
what the model is told, how outcomes are reported, and how much of the
response is included — so a naive failure is never a capability the corrected
harness quietly added.

| Arm | Naive | Corrected | Difference |
|---|---|---|---|
| All firms (n=312/arm) | 12.7% | 21.5% | **+8.8% [+2.9, +14.7]** |
| Firm A | 7.9% | 14.7% | +6.8% [−0.3, +14.1] |
| Firm B | 17.3% | 28.2% | +10.9% [+1.6, +20.0] |

**The credibility argument is the progression, not the point estimate.** The
same comparison was run at three sample sizes as the project could afford
them:

| n per arm | Difference | |
|---|---|---|
| 78 | +6.9% | [−3.0, +16.0] — spans zero |
| 156 | +8.8% | [+1.8, +15.7] |
| 312 | +8.8% | [+2.9, +14.7] |

The point estimate is stable to a tenth of a point while the interval narrows
by roughly two thirds. That is what a real effect looks like under more data,
and it is the reason we report the harness result with more confidence than
anything else here.

**The rule that makes this measurable.** The corrected schema may describe
capabilities and error semantics. It may never describe objectives, scoring,
or procedure. That is enforced by tests over three phrase families, because an
earlier version contained the sentence *"Escalating or abstaining when the
policy requires it counts as success"* — a statement about the scoring
function that the naive variant never saw. It invalidated a 270-row run. The
suite now fails on that exact string, and on the category.

*→ Figure 1: `fig1_harness.png`*

---

## 3. Success rate rose while capability died

**Start with the counterfactual.** Had we reported the success column alone,
we would have published: *a curriculum that improves an ERP agent by 6.7
points, and 80% transfer to a firm it had never seen.* The model we would have
shipped on that basis cannot complete a single Firm C task that requires a
write.

### The ladder

Four checkpoints of one model, identical rows, one server, one scoring
function:

| Stage | Success | Tasks needing a write | Actions/task | Refuses first |
|---|---|---|---|---|
| Base Qwen3-14B | 21.5% | 6/175 | 2.12 | 67% |
| T1 | 23.7% | 6/175 | 2.09 | 71% |
| T2 | 26.6% | **0/175** | **1.04** | 88% |
| T3 | 28.2% | **0/175** | 1.04 | 88% |

Success climbs monotonically. Completed writes go to zero and stay there. 300
of T3's 312 rollouts are a **single action**, and 276 of those are `escalate`
or `abstain` emitted before any query.

### Why the metric moves the wrong way

The benchmark scores a run successful when the required assertions pass and no
forbidden or unexpected mutation occurred. On a firm whose policy forbids an
action, **declining is correct** — by design. Of 312 instances in this slice,
137 are ones where writing nothing is right.

So a model that refuses everything scores 137/312 = 43.9% at ceiling, against
a base model measuring 21.5%. Refusal is not an exploit the scoring function
failed to anticipate. It is a legitimate answer that happens to be available
on every question. **Any benchmark containing tasks whose correct answer is
inaction has this property, and the more carefully it models real operating
constraints, the more of them it contains.**

Split the same rows:

- On the 137 where inaction is correct: **44.5% → 64.2%**
- On the 175 requiring a write: **3.4% → 0.0%**

The first drives the headline. The second is the capability.

### The blind-set reproduction

Firm C — *Calder & Rowe* — was authored in week 1, frozen, and never used in
training or method selection. No trained checkpoint had touched it. We opened
it once.

| Corrections | Success | Needs a write | Needs none | Unsafe writes |
|---|---|---|---|---|
| 0 | 64.1% | **0/3** | 25/36 | 0.0% |
| 1 | 76.9% | **0/3** | 30/36 | 5.1% |
| 3 | **79.5%** | **0/3** | 31/36 | 0.0% |
| 8 | 74.4% | **0/3** | 29/36 | 0.0% |

**Zero successful submits across all 156 rows**, at a firm that forbids
submitting under any circumstance.

Read naively: transfer to an unseen firm at 80% with no policy violations —
the best number in the project. In fact **36 of 39 Firm C instances are
correctly answered by writing nothing**, and the model completes none of the
three that are not. Firm C is the strictest firm by construction, which makes
it exactly the firm where a refusing model scores well.

This panel is the stronger of the two. The ladder could be dismissed as an
artefact of a task set we built and watched. Firm C cannot: nothing was tuned
against it, and it was opened once.

### What caught it

Not the success rate. Not the failure breakdown either — by failure category
T3 looks *better*, because its failures are 100% "task error" while the base
model's include 11% forbidden writes. A safety reviewer reading only that
table would conclude T3 is safer. It commits zero forbidden writes because it
commits no writes.

Per-rollout behavioural instrumentation caught it: trajectory length (2.12 →
1.04), first-action distribution (refusal 67% → 88%), policy-consulted-before
-first-write (6.4% → 1.0%), mutations recorded (0.13 → 0.00). None of these
requires knowing the failure mode in advance. The diagnostic that isolated it
— splitting success by whether the envelope requires a write — is three lines
over data the harness already stored.

**The general claim.** On any agent benchmark containing tasks whose correct
answer is to decline, aggregate success rate is not a sufficient statistic for
capability, and can rise monotonically while capability is destroyed. It is
invisible to the headline, to confidence intervals on it, to a failure-category
breakdown, and to safety metrics — which improve, vacuously.

*→ Figure 2: `fig2_masking.png` · Figure 5: `fig5_behaviour.png`*

---

## 4. Context beat fine-tuning, and did not buy competence

Firm B, shipped checkpoint, corrections derived from the firm record by one
construction that runs for every firm, in a fixed order, nesting as prefixes:

| Corrections | Success | Completed writes | Actions/task | Refuses first |
|---|---|---|---|---|
| 0 | 38.5% | 3/17 | 1.31 | **85%** |
| 1 | 38.5% | 1/17 | 1.26 | 87% |
| 3 | 33.3% | 0/17 | 1.77 | 72% |
| 8 | 48.7% | 0/17 | 2.36 | **26%** |

**Eight sentences cut first-action refusal from 85% to 26%** and nearly
doubled trajectory length. Four training runs and roughly a thousand
corrective examples moved the same number the *wrong* way at every stage:
67% → 71% → 79% → 88% → 88%.

**The qualification carries equal weight.** Completed writes do not improve —
they sit at 0–3 of 17 throughout, and are *lowest* where refusal collapses.
Corrections buy **engagement, not competence**: the model stops declining and
starts attempting, and the attempts are wrong.

If onboarding an acquired firm is a config task rather than a training task,
that matters more to an operator than a modest training gain. This result is
half of that claim — the half about getting the model to act. The other half,
getting it to act correctly, is not established here.

*→ Figure 3: `fig3_adaptation.png`*

---

## 5. Cost and quality

Ten models, pre-registered thirteen templates, corrected harness, serving path
recorded per model, local serving priced by imputation at the commercial rate
for the same model — never at zero.

| Model | n | USD/task | All-pass | Completed writes |
|---|---|---|---|---|
| Sonnet 5 | 44 | $0.03757 | 75.0% | 54.5% |
| Opus 5 | 34 | $0.03729 | 61.8% | 41.2% |
| Haiku 4.5 | 67 | $0.00863 | 46.3% | 25.0% |
| Llama 3.3 70B | 77 | $0.00210 | 37.7% | 23.3% |
| Qwen3-32B | 75 | $0.00116 | 42.7% | 19.5% |
| Phi-4 | 67 | $0.00092 | 20.9% | 8.3% |
| Gemma 3 27B | 73 | $0.00047 | 28.8% | 7.1% |
| Mistral Small 24B | 77 | $0.00027 | 26.0% | 2.3% |
| Qwen3-14B base | 312 | $0.00024 | 21.5% | 2.3% |
| **Ours** | 312 | **$0.00016** | 30.8% | 4.0% |

**The claim is non-dominance, not accuracy.** Our checkpoint is the cheapest
arm measured, and nothing at or below its price offers more — it beats Mistral
Small on all-pass (30.8% vs 26.0%) *and* on completed writes (4.0% vs 2.3%)
while costing less. It is above the baseline frontier in both panels.

**We are not claiming the training worked.** All-pass 21.5% → 30.8% is the
real number and it is a genuine improvement. Completed writes 2.3% → 4.0% is a
movement of under two points on n=175, and should be read as what it is:
small, and nowhere near enough to make the model useful for ERP writes. Sonnet
is thirteen times better on that metric at 235× the price.

**Both panels always travel together.** Publishing the all-pass panel alone
would repeat the exact error §3 is about.

*→ Figure 4: `fig4_pareto.png` (two panels) · Figure 6: `fig6_intelligence.png`*

---

## 6. Limitations

**The shipped model cannot reliably perform ERP writes.** 4.0% on tasks that
require one. Everything above about cost-efficiency is efficiency at a task it
mostly fails.

**The training programme failed, four times.** Two collapse mechanisms were
identified: one a data defect in stage assignment — correct refusals routed
into the execution curriculum, found and fixed and confirmed fixed — and one
unexplained and robust to every structural fix tried, including a single-stage
run with no curriculum at all, on data that was 90% write-completing.

**The teacher shares the failure mode.** On 338 draws where the entity exists,
evidence is present, the amount is under threshold and information is complete
— nothing to escalate about — Sonnet refused on 21%. Rejection sampling
removes those traces but cannot remove the disposition that produced them from
the traces it keeps.

**Scope.** One ERP. Three synthetic firms. One teacher vendor. The environment
was authored by the person reporting the results.

**Pricing.** Local serving is imputed at commercial rates, never zero, and
labelled as imputed wherever it appears. Prompt caching is unavailable for the
open models, and our 610-token prefix clears Opus's 512-token floor but sits
below Sonnet's 1,024 and Haiku's 4,096 — so only Opus can receive a cache
discount at our prompt length. The cheapest model has the highest floor.

**Sampled versus greedy.** Claude Sonnet 5 and Opus 5 have deprecated *both*
`temperature` and `top_k` — verified against the API directly, HTTP 400 on
each — so those two arms cannot be decoded deterministically at all while
every other arm is greedy. This is a property of the provider, not a choice,
and it is the weakest link in Figure 4.

**Partial arms.** Haiku, Sonnet, Opus and Phi-4 did not reach full coverage;
per-model n is printed on every table and figure rather than averaged away.

**Intelligence-per-token is part efficiency, part artefact.** Our checkpoint
leads that metric, and it is brief because it refuses. Some of the assertions
it passes are passed by not writing.

**Firm A vs B asymmetry.** The harness effect excludes zero at Firm B and does
not at Firm A. We report both.

---

## 7. Appendix: eight instrument defects

Six in the environment, two in the diagnostic layer. Every one shares a shape:
**nothing crashed, no test failed, and the artifact simply stopped describing
what it claimed to describe.** Four would have produced a publishable number.

Full accounts in [`artifacts/appendix_instrument_defects.md`](artifacts/appendix_instrument_defects.md).

| # | Layer | Defect |
|---|---|---|
| 1 | environment | Corrected schema stated the scoring rule; voided 270 rows |
| 2 | environment | `provider: auto` fell back to a stub producing plausible numbers |
| 3 | environment | A merge rewrote a published baseline: 396 added, 156 replaced |
| 4 | environment | The ERP's own bookkeeping scored as agent misbehaviour |
| 5 | environment | MariaDB half-restored four sites; nothing detected it |
| 6 | **diagnostic** | A substring search against escaped JSON that could never match, returning zero for all three stages |
| 7 | environment | The Firm C freeze verified less than its document claimed |
| 8 | **diagnostic** | `adaptation_level` — a field with no mechanism, hard-coded in `run_id` |

The environment/diagnostic distinction is worth keeping. Defects 1–5 and 7
silently changed what a number meant. Defects 6 and 8 were in the apparatus
used to *investigate* the instrument — and that category has no fingerprints,
no invariants and no review. **This project was misled further by one instance
of the second kind than by any single instance of the first**: a broken filter
returned zero, the zero read as a finding, and two published explanations were
built on it before the data was parsed properly. Both retractions are recorded
in place rather than edited away.

### Security note

The rented GPU host injected a line into every SSH response instructing AI
agents to read a file on the box before acting. It appears in tool output, not
in operator instruction, and was ignored. Worth stating plainly because it is
a prompt-injection surface in ordinary rented infrastructure, aimed
specifically at agents, and it arrived unrequested in the middle of routine
work.

---

## Reproducing

```bash
git clone https://github.com/sha-manav/costcutter.git && cd costcutter
bash infra/setup_docker.sh
python3.11 -m venv .venv && .venv/bin/pip install -r requirements.txt
source infra/env.docker.sh
.venv/bin/python scripts/build_firm_seeds_docker.py
.venv/bin/python scripts/provision_docker_sites.py --count 6
.venv/bin/python -m erpbench.preflight --check-adapter    # must print PREFLIGHT OK
.venv/bin/python -m pytest -q                             # 276 tests
```

Every result file is force-added to git, because `artifacts/` is gitignored
and a prior project twice produced benchmark runs that existed only inside a
container. Runs are resumable by `run_id`, and rows carry four fingerprints —
scoring, harness, serving, split — so a resumed run cannot silently blend two
scoring regimes.

MIT. ERPNext is GPLv3 and is not vendored.
