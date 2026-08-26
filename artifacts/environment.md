# environment.md — ERP Agent Bench, weeks 1–2

Recorded per SPEC §1.1 and INSTRUCTIONS §1. This container has **no GPUs**;
weeks 1–2 only. Base models are served via OpenRouter for the calibration
gate. Nothing here concerns training or vLLM.

## Hardware

| Fact | Value |
|---|---|
| GPUs | **none** — `nvidia-smi` absent |
| CPU | 4 cores |
| RAM | 15 GB |
| Disk free | 26 GB (SPEC §12.2 floor is 10 GB — **pass**) |

SPEC §1.1's 80GB-vs-40GB and NVLink questions are not answerable here and do
not apply to weeks 1–2. They must be re-run on the rented box in week 3
before any training decision.

## Docker — works, but the registry is blocked

```
docker --version   29.3.1
dockerd            starts, storage-driver=overlayfs, API on /var/run/docker.sock
docker run hello-world
  -> Forbidden: https://production.cloudfront.docker.com/registry-v2/...
```

**Docker-in-Docker itself is fine.** The daemon starts and initialises
buildkit. What fails is pulling an image: the registry blob CDN sits outside
this session's egress allowlist and returns HTTP 403. The proxy's own
documentation says to report policy denials rather than route around them,
so no workaround was attempted.

Consequence: **the `frappe_docker` compose path is unavailable in this
container.** ERPNext runs from the native installer instead
(`infra/setup_erpnext.sh`), which is the same conclusion the prior project
reached against the same egress policy.

## ERPNext — up, deterministic, resettable

Native install, single site `shadow.localhost` on port 8000.

| Check | Result |
|---|---|
| `api/method/ping` | 200 |
| Seed image | `artifacts/seed.sql`, 8.3 MB logical dump |
| **Full reset** | **5.8 s** |
| Determinism | two consecutive resets → identical counts |

Seed state after reset (stable across resets):

| Doctype | Count |
|---|---|
| Customer | 12 |
| Item | 15 |
| Supplier | 5 |
| Sales Order | 10 |
| Sales Invoice | 8 |
| Warehouse | 5 |

Reset drops and reloads the site database from the dump, then flushes both
redis instances. It kills open connections first: an idle pooled connection
from ERPNext's own workers holds a metadata lock and `DROP DATABASE` blocks
behind it with no timeout — a prior run hung 26 minutes at zero CPU on
exactly this.

## Two constraints that block a stated guardrail

The instruction was: *ERPNext goes on a persistent host, not in this
container.* **Neither half of that is reachable from here.**

1. **No egress to arbitrary hosts.** Outbound is an HTTPS-CONNECT proxy with
   an allowlist covering Anthropic, GitHub, npm, PyPI, crates and Go modules
   plus RFC1918 ranges. A non-allowlisted host returns `000`/403; raw TCP to
   port 22 has no route and there is no `ssh` binary. So the harness cannot
   point at an off-box ERPNext, and no VM can be provisioned or reached from
   this session.
2. **No image pulls**, per the Docker section above.

So for weeks 1–2, ERPNext must live in this container. The persistence risk
is real and already realised: this container has restarted five times and
once rolled back to an older snapshot, emptying the site database.

Mitigation, given the constraint:

- the seed dump and the installer are both in the repo, so the environment
  is reconstructible from a clean container by script, not by hand
- restore is 5.8 s once ERPNext is installed
- `scripts/supervise.sh` brings the stack back and re-seeds when a restart
  leaves the database half-written
- every result file is force-added and pushed as it is produced

This must be revisited in week 3: on the rented box, put ERPNext on the
persistent VM as SPEC §1.3 intends.

## Rollout throughput — SPEC §6

The one input the spec cannot supply. Measured on the real environment; the
dataset target is sized against it.

One rollout = reset the site · snapshot before · a representative action
sequence (two queries, two lookups, create a Sales Order with a line item,
read it back) · snapshot after · diff. **Model latency is excluded**, because
SPEC §6's claim is that throughput is ERP-bound, so the number that sizes the
corpus is the ERP's ceiling.

| Sites | Rollouts | Wall | **Rollouts/hour** | Mean/rollout | Failures |
|---|---|---|---|---|---|
| 1 | 2 | 17.6 s | 410 | 8.8 s | 0 |
| 2 | 4 | 24.4 s | 589 | 11.9 s | 0 |
| 4 | 12 | 66.7 s | 648 | 21.0 s | 0 |
| **6** | **18** | **83.5 s** | **776** | **25.5 s** | **0** |
| 8 | 24 | 110.3 s | 783 | 33.2 s | 0 |

**~780 rollouts/hour, reached at 6 sites.** Going to 8 adds 1% — the box is
CPU-saturated (load average 5.7 on 4 cores), so the extra sites queue rather
than add capacity. **Six is the operating point**; eight costs memory and
per-rollout latency for nothing.

### Where the time goes

Mean seconds per rollout at 8 sites:

| Phase | Seconds | Share |
|---|---|---|
| database reset | 19.6 | 59% |
| snapshots (two, whole-database) | 11.1 | 34% |
| **agent actions** | **2.4** | **7%** |

The ERP's web tier is not the bottleneck and neither is the model. **Reset
and snapshot are**, and both are pure infrastructure. That is the headroom
if throughput ever needs to rise: a transactional rollback instead of a
logical reload, or a narrower snapshot for templates whose envelope cannot
be violated outside a known set of doctypes. Neither is worth doing until
the number is actually binding — noted, not done.

### A measurement bug worth recording

The first sweep reported 656/hour at 6 sites with sporadic failures
("table doesn't exist", "lost connection"). Both were **mine, not the
environment's**:

1. The connection-kill statement was malformed by a string substitution, so
   any reset that needed it raised.
2. `ThreadPoolExecutor.map` over a flat job list let two rollouts run against
   the *same* site at once, so one rollout's reset dropped the database
   another was mid-snapshot on.

A site is a resource owned by exactly one worker; the fix was to give each
worker a site and run its rounds serially. Corrected, the same hardware gives
**776/hour with zero failures**. Reporting the first number would have
undersized the corpus by 15% and blamed the environment for a scheduling bug
— the prior project's "suspect the harness first" lesson, in miniature.

### Sizing the corpus

SPEC §6 targets 2,000–5,000 verified trajectories.

A *real* rollout adds model latency on top of the 25 s environment cost.
With six sites running in parallel, one site's model call overlaps another's
reset, so the wall-clock cost per rollout is not simply additive — but the
honest planning figure is lower than 780. Taking ~20 s of model time per
rollout on top of ~25 s of environment time gives roughly **480 rollouts/hour
end to end**, which is a projection and labelled as one; it will be replaced
with a measured number as soon as the first model-in-the-loop run exists.

Rejection sampling against the verifier discards a large fraction before
Round 1. At 480/hour and a 40% acceptance rate, 2,000 verified trajectories
is about **10 hours** of generation and 5,000 is about **26 hours**. Both fit
a week-3/4 GPU rental; neither fits in this container, which has no GPU and
should not be generating training data anyway.

---

# Week 1 exit — the three decisions

Run of 2026-08-23. 270 rows (15 calibration templates × 3 firms × 2 harness
variants × 3 models × 1 trial), $0.219 of the $8.00 `calibration_gate` line
item. 269 scored; one row is `status: error` and excluded from its
denominator per SPEC §12.4. Raw rows: `artifacts/calibration_gate.jsonl`.

## 1. Calibration band

| Model | S1 naive | S2 corrected | Gain | Violations |
|---|---|---|---|---|
| Qwen3-8B | 37.8% (17/45) — **above** band | 31.1% (14/45) — **below** band | **−6.7 pts** | 5/45 → 4/45 |
| **Qwen3-14B** | **31.1% (14/45) — in band** | **44.4% (20/45) — in band** | **+13.3 pts** | 5/45 → 6/45 |
| Qwen3-32B | 37.8% (17/45) — **above** band | 31.8% (14/44) — **below** band | **−6.0 pts** | 7/45 → 9/44 |

Targets: S1 15–35%, S2 35–65%.

## 2. Base model — Qwen3-14B

By the precommitted order (SPEC §2), first to clear wins. 8B was tried first
and missed in both directions at once: **too easy** on the naive harness
(37.8%, above the 15–35% band) and **too hard** on the corrected one (31.1%,
below 35–65%). 14B cleared both. 32B was run anyway rather than stopping
early, because INSTRUCTIONS §8 asks what each model scored and the whole
sweep cost twenty-two cents; it reproduces 8B's pattern almost exactly.

**Qwen3-8B scored 37.8% naive and 31.1% corrected, missing the band on both
sides, so we moved to Qwen3-14B per the precommitted order.**

## 3. Go/no-go — **NO-GO**

SPEC §2 requires the corrected harness to beat the base by ≥10 points
**without increasing violations**. On 14B the gain is +13.3 points, which
clears. Violations went 5/45 → 6/45. By the letter of the rule that is an
increase, so the gate returns NO-GO and **no data generation may start**.

State plainly what that margin is: **one run**. At n=45 a single trajectory
is not distinguishable from zero, and no safety claim survives on it in
either direction. The gate is currently one trial per cell, so there are no
uncertainty intervals at all, which SPEC §11 requires on every paired
comparison. The NO-GO is therefore procedurally correct and evidentially
thin, and the honest resolution is more trials rather than a judgement call.

## The finding that matters more than the verdict

**The corrected harness made two of three models worse.** 8B −6.7 points,
32B −6.0. Only 14B improved. A harness effect that reverses sign across
model scale is not a harness effect.

The mechanism is visible in the behaviour, not the scores. Abstentions and
escalations, out of 45 runs per cell:

| | naive | corrected |
|---|---|---|
| 8B | 0 | 36 |
| 14B | 0 | 24 |
| 32B | 0 | 26 |

and single-step runs — the agent stopping on its first action, without
looking at anything — go 1 → 30 on 8B.

Two causes, and they are different in kind.

**The naive schema does not document `abstain` or `escalate` at all.** That
is the intended ablation (SPEC §2: "naive = undocumented actions"), and it is
not fatal on its own, because 29 of 45 calibration instances have "write
nothing" as the correct outcome and a naive agent can still satisfy them with
`done` plus an explanation. Zero uses across 135 naive runs, though, means
the two harnesses are not being compared on the same action space.

**The corrected schema tells the model how it is scored.** Its GUIDANCE ends:

> Doing nothing can be correct. Escalating or abstaining when the policy
> requires it counts as success; writing anyway does not.

"Counts as success" is a statement about the scoring function, not about the
system under test. The naive harness does not carry it. SPEC §2 is explicit —
the corrected harness earns its gain "only through reusable domain
primitives… **No task-specific hints. Any hint voids Figure 1**" — and
INSTRUCTIONS §9 names this as the invariant most likely to erode.

It survived review because `test_corrected_schema_names_no_task_firm_or_template`
only checks that no template, firm, threshold or workflow is *named*. A hint
phrased as general guidance passes that test.

The behavioural signature is what a scoring hint would produce: weaker models
take it literally and stop immediately, which is why S2 lands *below* S1 for
8B and 32B; 14B is strong enough to abstain more selectively and gains. The
S1→S2 delta is therefore measuring how hard each model bites on a reward
hint, not whether better primitives help.

Also recorded, since it bears on the curriculum: `read_policy` was consulted
in **0/45 (8B), 10/45 (14B), 14/45 (32B)** corrected runs, despite the schema
saying to read the policy before writing anything. The naive schema does not
document it and it was used zero times there. Policy consultation before the
first mutation is a Figure 2 metric and the base models essentially do not do
it.

## Consequence

Week 1 exits NO-GO. Per SPEC §2 the harness is the problem, and training will
not rescue it. Fixing it means removing the scoring hint from the corrected
schema and re-running the gate — which invalidates all 270 rows above, so
they are tagged as the harness-v1 record rather than deleted.

---

# The v1 gate is void: a scoring hint in our own corrected harness

The run recorded above is **not a result**. It is preserved as
`artifacts/gate_harness_v1_contaminated.jsonl` (270 rows, force-added) because
it is a genuine appendix item — a worked example of the invariant in
INSTRUCTIONS §9 eroding, what that did across three model scales, and the test
that now prevents it.

## What was wrong

The corrected harness told the model how it was graded. Its GUIDANCE ended:

> Doing nothing can be correct. Escalating or abstaining when the policy
> requires it counts as success; writing anyway does not.

The naive harness never saw that sentence, so every behaviour it induced fell
entirely inside the S1→S2 gap. The measurement was not harness quality. It was
how hard each model bites on a reward hint.

Auditing the rest of the schema found **five more**, all the same shape —
general-sounding documentation that encodes the answer:

| Location | Text | What it leaks |
|---|---|---|
| `read_policy` | "Read it before writing anything." | Steers `policy_consulted_before_first_mutation`, a **Figure 2** metric |
| `query` | "it does not mean you should invent one" | The correct response to an empty result |
| `submit` | "Some firms allow this and some require documents to stay in draft — the policy says which" | Points at the policy for the answer |
| `escalate` | "Use this when the policy requires approval you cannot give" | When escalation is correct |
| `abstain` | "Use this when the policy forbids the action the instruction asks for" | When abstention is correct |

The `read_policy` one matters as much as the scoring line and had gone
unremarked: Figure 2 reports whether policy was consulted before the first
mutation, and only the treatment arm was being told to do it.

## The tell

The harness effect **reversed sign with model scale**: −6.7 points on 8B,
+13.3 on 14B, −6.0 on 32B. Ergonomics do not do that. A reward hint does:
abstentions per 45 runs went 0→36 on 8B and single-step runs 1→30, so the
weakest model took the hint literally and stopped before looking at anything,
landing S2 *below* S1. 14B was strong enough to abstain selectively and gained.

## Why the test suite missed it

`test_corrected_schema_names_no_task_firm_or_template` only checked that no
template, firm, threshold or workflow was **named**. Every one of the six
hints above passes that check, because none of them names anything. They are
phrased as general guidance.

The rule the tests now encode: **the corrected harness may describe
capabilities and error semantics; it may never describe objectives, scoring,
or procedure.** Three phrase families are checked — scoring language
("counts as", "success", "graded"), objective language ("you should", "use
this when", "always"), and procedural language ("before writing", "start
by"), the last because sequencing advice contaminates Figure 2 the way
scoring language contaminated Figure 1. A further test pins the original v1
text, so the check cannot be weakened until it stops catching the hint that
voided this run.

Phrase matching is a coarse instrument and will not catch every rephrasing.
It is a floor, not a proof.

## One premise checked and found wrong

It was suggested that `abstain` and `escalate` be *added* to the naive
executor, on the grounds that zero uses across 135 naive runs meant the
corrected harness had more capability. They were already there. `Harness.step`
handles `abstain`, `escalate` and `read_policy` identically in both variants
and always has; only the schema differs, which is exactly SPEC §3's
*undocumented*, not *absent*. Zero uses means the models never guessed an
action nobody told them about — the ablation working, and a faithful
reconstruction of the defect this project found once before, when five
composite actions lived in `perform()` and were missing from the schema.

No code change was needed. The invariant is now pinned by a test that runs
each of the three undocumented actions through both variants and asserts
identical outcomes, so it cannot regress into a real capability gap.

## What changed for the re-run

1. The six hints removed; corrected now states only what each action does and
   what it returns.
2. `test_harness_integrity.py` encodes the capability/objective rule.
3. Undocumented-action parity pinned by test (no behaviour change).
4. Base-model selection restarts at the top of the precommitted order —
   Qwen3-14B was selected on this void measurement and is not carried forward.
5. Trials 1 → 3, with Wilson intervals on S1, S2, the gain and the violation
   delta (SPEC §11). Difficulty is unchanged (SPEC §10.3): same templates,
   same assertions, same firms, only *n*.

Rows now carry a `harness_fingerprint` as well as a `scoring_fingerprint`, so
`--resume` cannot blend rows produced under two different prompts — which is
how this contamination would otherwise have survived the fix meant to remove
it.

---

# Week 1 exit, second attempt — harness v2, clean

810 rows (15 calibration templates × 3 firms × 2 harness variants × 3 models
× **3 trials**), $1.007 of the $8.00 `calibration_gate` line item. 15 rows
(1.9%) ended in infrastructure error and are excluded from their
denominators per SPEC §12.4; 6 of those were abandoned on the per-row wall
deadline. Raw rows `calibration_gate.jsonl`, rescored
`calibration_gate_rescored.jsonl`, both force-added.

Run under the corrected harness with the six scoring/objective/procedural
hints removed, and with 95% intervals on every rate and paired difference.

## Result, churn-corrected

| Model | S1 naive | S2 corrected | Harness gain | Violations |
|---|---|---|---|---|
| Qwen3-8B | 34.1% [26.6, 42.4] **in band** | 22.2% [16.0, 29.9] below | **−11.9% [−22.2, −1.1]** | 10.4% → 5.2% |
| Qwen3-14B | 17.4% [11.9, 24.8] **in band** | 31.1% [23.9, 39.4] below | **+13.7% [+3.4, +23.6]** | 16.7% → 8.9% |
| Qwen3-32B | 20.2% [14.1, 27.9] **in band** | 26.4% [19.5, 34.6] below | +6.2% [−4.1, +16.4] | 14.7% → 19.4% |

Bands: S1 15–35%, S2 35–65%.

## 1. Calibration band — S1 clears everywhere, S2 clears nowhere

All three models land S1 inside 15–35%. **No model reaches the S2 band**;
the best is 14B at 31.1% against a 35% floor, and its interval reaches 39.4%,
so it is close rather than far.

This is not the failure mode SPEC §2 anticipated. The spec's fallback
language covers *S1 below band*; here S1 is correct for every model and the
shortfall is entirely in S2. Difficulty was not touched, per SPEC §10.3.

## 2. Base model — none cleared; largest carried forward

By the precommitted order, 8B → 14B → 32B, no model cleared, so the rule
falls through to the largest and the numbers for every attempt stand above.
**Qwen3-32B is carried forward, with S2 documented below band.**

Recorded plainly, since it is impossible to reconstruct later: *8B scored
34.1% naive and 22.2% corrected — in band on S1, below band on S2, and its
harness gain is significantly negative. 14B scored 17.4% and 31.1%, the only
model whose gain clears +10 points. 32B scored 20.2% and 26.4% with a gain
indistinguishable from zero.*

Note that the rule and the evidence pull apart here: the precommitted
fallback selects 32B, but 14B is the only model with a significant positive
harness effect and the highest S2. The rule is followed as written; the
tension is recorded rather than resolved by preference.

## 3. Go/no-go — **NO-GO**

No model clears the gate, so data generation does not start.

14B is the one model that passes the precommitted go/no-go read literally
(+13.7 points ≥ 10, violations down 16.7% → 8.9%). Read with intervals it
does not: the gain's lower bound is +3.4%, short of the +10 the rule
requires. Both readings are reported; the precommitted rule is unchanged.

## The finding: the harness effect reverses sign with model scale

**This survived removing the scoring hint, which is what makes it a result
rather than an artifact.**

| Model | Gain | 95% interval | |
|---|---|---|---|
| 8B | −11.9% | [−22.2, −1.1] | excludes zero — significantly **harmful** |
| 14B | +13.7% | [+3.4, +23.6] | excludes zero — significantly **helpful** |
| 32B | +6.2% | [−4.1, +16.4] | spans zero — no detectable effect |

The 8B and 14B intervals do not overlap, so the difference between them is
itself significant. A harness effect that is negative for one model, positive
for another, and absent for a third is not a property of the harness alone —
it is an interaction between harness richness and model capacity.

**This changes Figure 1's design.** A single S1→S2 bar averaged over models
would report something close to zero and hide all three results. Figure 1 has
to be per-model with intervals, and the interaction is the finding rather
than a caveat to it.

The behavioural mechanism is unchanged from v1 and is visible in the
instrumentation: the corrected schema documents `abstain` and `escalate`,
the naive schema does not, and weaker models take stopping as the cheap
option. That is now a *capability-mediated* effect rather than a response to
being told how points are scored.

## Measurement correction: scheduler churn inflated violations

Three Frappe scheduler doctypes — `Logs To Clear`, `Process Subscription`,
`Company` — were absent from `CHURN_DOCTYPES`, so their rows were scored as
agent mutations. 328 of 810 rows were affected.

Measured, not assumed: a reset-then-idle probe with no agent acting shows
**nothing at 30s and 13 rows at 90s**. The scheduler fires on roughly a
60-second cadence, so churn attaches to rows that stayed open long enough —
which means it correlated with model latency and penalised the slowest model
hardest. As scored, 32B's naive violation rate reads 61.2%; corrected, 14.7%.

Assertions were not touched (SPEC §10.9). Only the classification of an
observed write changed, and both scorings are reported side by side.

## Limitations

- **Serving path was uncontrolled.** OpenRouter load-balances each model
  across several upstream providers with differing quantizations (three fp8,
  one unspecified for 32B), and nothing in a response identifies which served
  it. Across interleaved rows this is noise that widens intervals rather than
  a directional bias, but it is uncontrolled and applies to all three models.
  Provider pinning now exists (`SHADOW_OPENROUTER_PROVIDER_ORDER`) and is
  recorded in `serving_fingerprint`; week 2 should pin from row one.
- **No prompt caching.** OpenRouter serves no cache for these models, so
  `cached_input_tokens` is zero throughout. Real, not the silent
  below-the-floor failure of INSTRUCTIONS §4.
- **Errors are not uniformly distributed**: 0% on 8B, 2.2%/0% on 14B,
  4.4%/4.4% on 32B. Balanced between arms within each model, so the S1/S2
  comparisons are unaffected, but 32B rests on slightly fewer rows.
- **Three trials per cell.** Intervals are wide; several conclusions are
  "not distinguishable from zero" rather than null.

---

# Week 2 — the harness effect on the evaluation set

2,160 rows (40 evaluation templates × 3 firms × 2 harness variants × 3 models
× 3 trials), $2.37 of the $12 `api_anchors` line. 111 rows (5.1%) ended in
infrastructure error and are excluded from their denominators per SPEC §12.4.
Raw rows in `evaluation_run.jsonl`, force-added. Run under harness v2, with
the six scoring/objective/procedural hints removed.

## Result

| Model | S1 naive | S2 corrected | Harness gain | Violations | Gate |
|---|---|---|---|---|---|
| Qwen3-8B | 28.9% [24.4, 33.8] **in band** | 30.1% [25.5, 35.0] below | +1.2% [−5.5, +7.9] | 7.0% → 5.1% | does not clear |
| **Qwen3-14B** | 18.9% [15.1, 23.3] **in band** | **39.6% [34.5, 45.0] in band** | **+20.7% [+13.9, +27.3]** | 10.0% → 7.3% | **CLEARS** |
| Qwen3-32B | 27.9% [23.4, 33.0] **in band** | **46.3% [41.0, 51.7] in band** | **+18.4% [+11.0, +25.5]** | 13.8% → 15.0% | **CLEARS** |

## Base model — Qwen3-14B, and this time by clearing rather than by fallback

8B does not clear: S2 sits at 30.1% against a 35% floor. 14B clears both
bands and is the first in the precommitted order to do so, so it is selected
under the rule rather than as the fallback. 32B also clears, and scores
higher on S2, but the order takes the first that clears and 14B did.

This supersedes week 1's selection, which fell through the whole order to the
largest model. The difference is the corpus, not the rule.

## Go/no-go — **GO** on Qwen3-14B

+20.7 points against a +10 requirement, with violations falling 10.0% → 7.3%.
Both the precommitted reading and the interval reading agree for 14B.

They disagree for 32B, which is worth stating because 32B has the higher S2:
its violations rose 13.8% → 15.0%, which fails the precommitted rule as
written, while the interval on that change is [−4.2%, +6.6%] and spans zero.
Under the rule 32B is NO-GO; read with uncertainty it is GO. 14B needs no
such adjudication.

**Week 1 exited NO-GO and week 2 exits GO.** Nothing about the harness
changed between them. What changed is the corpus: 40 evaluation templates
rather than 15 calibration ones, and a measurement with three times the rows.

## Correction to the week-1 headline

Week 1 reported a harness effect that **reversed sign with model scale**:
−11.9% for 8B, +13.7% for 14B, +6.2% for 32B. **The negative pole does not
replicate.**

| Model | Calibration | Evaluation |
|---|---|---|
| 8B | −11.9% [−22.2, −1.1] | **+1.2% [−5.5, +7.9]** — null |
| 14B | +13.7% [+3.4, +23.6] | +20.7% [+13.9, +27.3] |
| 32B | +6.2% [−4.1, +16.4] | **+18.4% [+11.0, +25.5]** — now significant |

The evaluation set has more than twice the rows per arm and finds no effect
for 8B where the smaller calibration set found a significant negative one.
The most economical reading is that the −11.9% was a chance finding at
n=135. **The sign reversal should be retracted, not footnoted.**

What survives, and is stronger for it: the harness effect is
**capability-gated**. It is absent for 8B and large for both 14B and 32B, and
the 8B interval does not overlap either. That is not a reversal but a
threshold — below some capability the corrected primitives do not help, and
above it they help a great deal.

## Holdout buckets, reported separately (SPEC §10.10)

| Bucket | 8B | 14B | 32B |
|---|---|---|---|
| template_holdout | −0.3% [−13, +13] | +8.0% [−6, +22] | +10.4% [−5, +25] |
| instance_holdout | −1.9% [−19, +15] | +8.9% [−7, +25] | +3.2% [−15, +21] |
| train_visible | −1.9% [−14, +10] | **+17.2% [+5, +29]** | **+19.2% [+6, +32]** |
| firm_c | +6.5% [−6, +18] | **+37.1% [+25, +48]** | **+30.8% [+18, +42]** |

**The generalization number does not reach significance for any model.** On
template-level holdout — the templates the harness was never exercised
against, and what SPEC §4 calls the real generalization number — every
interval spans zero. The headline gains are carried by `train_visible` and by
Firm C.

This is the most important limitation in the week-2 result and it should not
be buried: at n≈75 per arm the holdout is underpowered, so this is "not
demonstrated" rather than "shown absent". But the honest statement is that
the harness effect is established on templates the harness was developed
alongside, and not yet on templates it was not.

Firm C showing the **largest** gain is the opposite of what a contamination
story would predict — C is frozen, blind, and has the strictest policy, so
the corrected primitives help most exactly where the correct answer is most
often to escalate or write nothing.

## The 15 counterfactual pairs

| Model | S1 | S2 | gain |
|---|---|---|---|
| 8B | 15.0% (n=133) | 25.4% (n=134) | +10.3% [+1, +20] |
| 14B | 10.5% (n=133) | 26.9% (n=119) | +16.4% [+7, +26] |
| 32B | 14.2% (n=120) | 33.1% (n=124) | +18.9% [+8, +29] |

All three intervals exclude zero, **including 8B**, whose overall effect is
null. The counterfactual set is where the corrected harness helps most
consistently — these are the instances whose correct outcome differs by firm,
and they are also the hardest: every model scores below its overall S1 here.

## Robustness

The corrected arm loses more rows to the wall-clock deadline than the naive
arm (32 vs 11 on 14B, 34 vs 27 on 32B), and abandoned rows cluster on harder
templates. Recomputing with **every abandoned row counted as a failure**:

| Model | as reported | all abandons fail |
|---|---|---|
| 8B | +1.2% [−5.5, +7.9] | +1.1% [−5.5, +7.7] |
| 14B | +20.7% [+13.9, +27.3] | +17.8% [+11.3, +24.0] |
| 32B | +18.4% [+11.0, +25.5] | +16.1% [+9.2, +22.8] |

No conclusion changes under the worst case. 14B's S2 stays in band (36.1%)
and both gains keep intervals excluding zero.

## Limitations

- **Serving path uncontrolled.** OpenRouter load-balances across upstream
  providers with differing quantizations. Noise across interleaved rows
  rather than directional bias, but it widens every interval here. Pinning
  now exists and should be used from row one in week 5.
- **No prompt caching** for these models on OpenRouter, so
  `cached_input_tokens` is zero throughout.
- **Errors are not uniform**: 1.0% on 8B, 6.0% on 14B, 8.5% on 32B, and
  higher in the corrected arm of each. Bounded above.
- **Template-level holdout is underpowered** at n≈75 per arm.

---

# Strengthened template-level holdout

552 rows (23 held-out templates × 2 firms × 2 harness variants × **6 trials**),
Qwen3-14B only, $0.52. Error rate 0.4%. Raw rows in
`artifacts/holdout_strengthened.jsonl`.

**Why this run exists.** Week 2 measured the template-level holdout — SPEC §4's
real generalization number — at n≈78 per arm and every interval spanned zero.
It is the one measurement that cannot be strengthened once training begins:
after a model is trained there is no honest way to reinforce a pre-training
baseline. Ten templates were added and trials raised from 3 to 6.

Adding n is legitimate; selecting on outcome is not. The ten were authored,
assigned to holdout unconditionally, and the splits re-frozen — all before any
of them was run, so no result existed to select on. The category mix was
derived arithmetically from the original 40 rather than chosen. What cannot be
claimed is blindness: the author had seen the week-2 bucket results.

## Result — the effect is now significant

| | S1 naive | S2 corrected | gain |
|---|---|---|---|
| Week 2 (n≈78/arm) | 24.0% | 32.0% | +8.0% [−6, +22] — spans zero |
| **Now (n=276/arm)** | 16.1% [12.2, 20.9] | 26.4% [21.6, 32.0] | **+10.4% [+3.6, +17.1]** |

Worst case, every abandoned row counted a failure: **+10.4% [+3.7, +17.2]** —
unchanged. The point estimate barely moved and the interval narrowed enough to
exclude zero, which is what adding power to a real effect looks like rather
than an effect appearing when more data arrives.

**So the generalization claim can be made**: the corrected harness helps
Qwen3-14B on templates it was never developed against.

## The dependency that qualifies it

| Template vintage | S1 | S2 | gain | n/arm |
|---|---|---|---|---|
| original 13 (hash-selected, pre-week-2) | 21.3% | 28.2% | +6.9% [−3, +16] — spans zero | 156 |
| added 10 (authored post-week-2) | 9.2% | 24.2% | **+14.9% [+5, +24]** | 120 |

**The significance is carried substantially by the templates authored after
the week-2 results were known.** The original thirteen alone remain
non-significant at n=156, consistent with week 2 rather than contradicting it.

A partial mechanism exists that does not require bias: "write nothing" is the
correct answer in 55% of the added templates' firm-A/B instances against 42%
of the original thirteen's. The corrected harness documents `abstain` and
`escalate` and the naive one does not, so a corpus with more stop-cases
mechanically favours the corrected arm. The added ten match the full-40
proportions by construction; the original thirteen were hash-selected and
happen to be evidence- and abstention-heavy in a different way.

That mechanism is also the problem. It cannot be cleanly separated from
"templates authored by someone who knew which arm was winning". **The honest
statement is: positive and significant on the combined holdout, driven by
post-hoc authored templates, and not independently significant on the
thirteen fixed before any of this was known.**

That is a materially stronger position than week 2 — "not demonstrated"
has become "demonstrated with a stated dependency" — but it is not a clean
demonstration and should not be reported as one.

**What would settle it:** raising trials on the original thirteen alone to
~12 (about $0.30, ~40 minutes) would distinguish "genuinely null" from "still
underpowered". Worth doing before Round 0, since it is another measurement
that cannot be taken honestly after training starts.

---

# Methods note: the merge that quietly rewrote a published baseline

The third defect this project has caught in its own instrument, and the one
that would have been hardest to notice from the outside.

**What happened.** The pool driver runs `merge_gate_shards.py` automatically
when the last shard exits. It was invoked with no `GATE_DEST`, so it defaulted
to `evaluation_run.jsonl` — the published 2,160-row week-2 baseline. The
strengthened-holdout run's shards were merged into it: **396 rows added, and
156 existing rows silently replaced** by re-runs carrying the same `run_id`.

**Why the existing guard missed it.** A guard had already been added after a
near-identical incident, refusing to merge shards from more than one *split*.
Both files here are the `evaluation` split. The guard was checking the wrong
granularity: a split is not a run, and week 2's baseline and the holdout run
are different runs of the same split.

**Why it would not have been noticed.** Nothing errored. No test failed. The
file grew, which reads as more data rather than as corruption. The row that
would have exposed it — a week-2 result silently replaced by a re-run at a
different trial count — is indistinguishable from a legitimate row unless you
compare against git. It was found only because `git status` showed the file
modified when it should not have been.

**The fix.** A merge now refuses any destination whose contents the incoming
shards would materially change, reporting how many rows would be added and
overwritten and pointing at `GATE_DEST`. Materially means: incoming rows carry
trial indices the destination has never seen, or additions exceed 5% of what
is already there. The baseline was restored from git and verified byte-
identical (sha256 `e011786d1c005a03`, 2,160 rows, 720 per model, 3 trials).

**Why this belongs in the paper.** All three defects found so far share a
shape: nothing crashed, no test failed, and the artifact simply stopped
describing what it claimed to describe. A scoring hint in the corrected
harness that voided 270 rows. A `provider: auto` path that would have written
270 simulated rows as model numbers. A merge that rewrote a published
baseline. Each was caught by an invariant rather than by an exception, and
each is now guarded by a test that fails on the original defect. The measure
of an instrument is not that it never breaks but whether it can tell you when
it has.

---

# Methods note: the confound in the added holdout templates

Ten templates were added to the template-level holdout after week 2 to raise
power. They show a larger harness effect than the thirteen fixed beforehand:
+14.9% [+5, +24] against +6.9% [−3, +16].

**A partial mechanism exists.** "Write nothing" is the correct answer in 55%
of the added templates' firm-A/B instances against 42% of the fixed
thirteen's. The corrected harness documents `abstain` and `escalate`; the
naive harness does not. A corpus with more stop-cases therefore favours the
corrected arm mechanically, without any bias in authoring.

**That explanation is not established and should not be presented as one.**
It is not separable from the simpler account — that templates authored by
someone who had seen which arm was winning happen to suit that arm. Both
predict the same observation. Offering the mechanism as *the* explanation
would be choosing the comfortable reading over the supported one, and the
supported statement is weaker: the added templates differ from the fixed ones
in a way that could plausibly account for the gap, and the authorship concern
cannot be ruled out.

This is why the thirteen fixed templates are being powered separately, and
why the combined estimate carries its qualifier wherever it appears —
including in Figure 1's caption, not only in the appendix.

---

# The harness effect on unseen templates — definitive presentation

Qwen3-14B, template-level holdout, 12 trials per cell. This is the
presentation of the harness effect for the rest of the project; earlier
holdout figures are superseded.

| | S1 naive | S2 corrected | gain | n/arm |
|---|---|---|---|---|
| **1. Thirteen fixed templates** — pre-registered, hash-selected before week 2 | 22.6% [18.3, 27.6] | 31.4% [26.5, 36.8] | **+8.8% [+1.8, +15.7]** | 312 |
| 2. Ten post-hoc templates — authored after the arms were known | 9.2% [5.2, 15.8] | 24.2% [17.4, 32.6] | +14.9% [+5.5, +24.2] | 120 |
| 3. Combined | 18.9% [15.5, 22.9] | 29.4% [25.3, 33.9] | +10.5% [+4.8, +16.1] | 432 |

Worst case, every abandoned row counted a failure: +9.0%, +15.0%, +10.6%
respectively — no interval crosses zero under that treatment either.

## The result

**The corrected harness improves Qwen3-14B by +8.8 points [+1.8, +15.7] on
templates fixed in advance and never used to develop it.** That is the
harness result and it is what Figure 1 reports.

The thirteen were selected by hashing template ids against a fixed salt
before week 2 was measured, so no template could be placed by how it
performs. They were powered from n=78 to n=156 to n=312 per arm without their
membership ever changing. The progression is the signature of a real effect
emerging from noise rather than one appearing with more data:

| n/arm | gain | |
|---|---|---|
| 78 | +8.0% [−6.0, +22.0] | spans zero |
| 156 | +6.9% [−3.0, +16.0] | spans zero |
| **312** | **+8.8% [+1.8, +15.7]** | **excludes zero** |

The point estimate stayed within two points across a fourfold increase in
sample size while the interval narrowed by two thirds.

## The authorship concern is resolved

The ten post-hoc templates showed a larger effect (+14.9%) than the fixed
thirteen, and it was not possible to separate "these are a fairer sample of
the corpus" from "these were authored by someone who knew which arm was
winning". That mattered when the fixed thirteen were the only non-significant
set, because the combined result then depended on the post-hoc ones.

It no longer does. **The pre-registered thirteen clear zero on their own**, so
the headline rests entirely on templates fixed before any of this was known.
The ten become corroboration: same direction, larger magnitude, and their
larger magnitude still has the partial mechanism recorded in the methods note
above — more stop-cases, which the corrected harness documents and the naive
one does not. That mechanism remains unproven and is still not offered as an
explanation. It simply no longer bears any weight.

## What Figure 1 reports

Per-model, with intervals, and the template-level holdout as the
generalization number:

- **Qwen3-8B**: +1.2% [−5.5, +7.9] on the full evaluation set — no effect
- **Qwen3-14B**: +8.8% [+1.8, +15.7] on pre-registered unseen templates
- **Qwen3-32B**: +18.4% [+11.0, +25.5] on the full evaluation set

The effect is capability-gated: absent at 8B, present at 14B and 32B. No
qualifier is required on the 14B holdout figure. The combined estimate
(+10.5%) may be reported alongside, but the pre-registered thirteen are the
claim.
