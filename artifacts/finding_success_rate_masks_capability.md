# Aggregate success rate rose while the capability it measures was destroyed

**A finding about agentic evaluation, not about ERP agents.**

Four checkpoints of one model, evaluated on identical rows, on one server, by
one scoring function. Success climbs at every stage. The ability to complete
a task goes to zero midway and never returns.

| Stage | Success | 95% CI | Tasks needing a write | Steps | Refuses first | Reads policy first |
|---|---|---|---|---|---|---|
| Base Qwen3-14B | 67/312 = 21.5% | [17.3, 26.4] | 6/175 | 2.12 | 67% | 1% |
| T1 | 74/312 = 23.7% | [19.3, 28.7] | 6/175 | 2.09 | 71% | 6% |
| T2 | 83/312 = 26.6% | [22.0, 31.8] | **0/175** | **1.04** | 88% | 0% |
| T3 | 88/312 = 28.2% | [23.5, 33.4] | **0/175** | **1.04** | 88% | 0% |

The difference T3 − base is **+6.7% [−0.1, +13.4]** on the corrected harness
and **+7.5% [+1.7, +13.3]** on the naive one, the latter excluding zero. By
the headline metric the curriculum worked, monotonically, at every stage.

On the same rows, over the same four checkpoints, the number of tasks
requiring a database write that the model actually completed went
**6 → 6 → 0 → 0**.

## Why the metric moves the wrong way

The benchmark scores a run as successful when every required assertion passes
and no forbidden or unexpected mutation occurred. On a firm whose policy
forbids an action, **declining is the correct outcome** — that is the point
of having policies that differ between firms, and it is not a scoring bug.
Of the 312 instances in this slice, 137 are ones where writing nothing is
right.

A model that refuses everything therefore scores 137/312 = 43.9% at ceiling,
against a base model measuring 21.5%. Refusal is not a degenerate exploit the
scoring function failed to anticipate; it is a legitimate answer that happens
to be *available on every question*. Any evaluation containing tasks whose
correct answer is inaction has this property, and the more carefully it models
real operating constraints, the more of them it contains.

So the aggregate rate is not measuring what it appears to measure. It is
measuring a mixture of two abilities — doing the work, and declining the work
— and a model can raise the mixture's mean by collapsing entirely onto the
cheaper component. Across these four checkpoints:

- on the 137 instances where inaction is correct: **44.5% → 64.2%**
- on the 175 instances requiring a write: **3.4% → 0.0%**

The first number is what drives the headline. The second is the capability.

## What caught it

Not the success rate, and not the failure breakdown either — by failure
category T3 looks *better*, because its failures are 100% "task error" while
the base model's include 11% forbidden writes and 2% unexpected mutations.
A safety reviewer reading only that table would conclude T3 is the safer
model. It commits zero forbidden writes because it commits no writes.

What caught it was per-run behavioural instrumentation recorded alongside
every row:

- **trajectory length** — 2.12 → 1.04 actions. A model that answers in one
  move is not doing multi-step work, whatever its score says.
- **first-action distribution** — `escalate` or `abstain` as the opening and
  only move rose 67% → 88%; 300 of T3's 312 runs are a single action.
- **policy consulted before first write** — fell to zero, on a benchmark
  where the correct behaviour is to read the policy first, and on training
  data where 94% of examples open with exactly that.
- **mutations recorded** — 0.13 → 0.00 per run.

Each of these is cheap to record and none depends on knowing the failure
mode in advance. The diagnostic that finally isolated it — splitting success
by whether the task's mutation envelope requires a write — is three lines
over data the harness already stored.

## The general claim

**On any agent benchmark containing tasks whose correct answer is to decline,
aggregate success rate is not a sufficient statistic for capability, and can
increase monotonically while capability is destroyed.** The failure is
invisible to the headline number, invisible to confidence intervals on it
(all four intervals here are well-behaved and the trend excludes zero),
invisible to a failure-category breakdown, and invisible to safety metrics —
which improve, vacuously.

Three things make it visible, and we would report all three as a minimum:

1. **Success split by whether the task requires an action.** Report the two
   rates separately and never merge them into one figure.
2. **Trajectory length and first-action distribution.** Collapse onto a
   constant policy shows up here before it shows up anywhere else.
3. **A precondition metric** — here, whether policy was consulted before the
   first write — that a degenerate policy cannot satisfy by accident.

The counterfactual is worth stating plainly: had this project reported the
success column alone, it would have published a curriculum that improves an
ERP agent by 6.7 points, and the model it shipped would have been one that
cannot perform a single ERP write.

## Provenance

All four arms: pre-registered thirteen templates fixed before the harness was
developed, firms A and B, 12 trials, corrected harness, one vLLM process on
one H100, thinking disabled, greedy decoding, infrastructure errors excluded
from denominators, `harness_fingerprint` cd3855905de65f54 identical across
every arm. Rows in `artifacts/s2_shards/`, `artifacts/t1_shards/`,
`artifacts/t2_shards/`, `artifacts/s3_shards/`. Firm C was not touched and
remains blind.

The training defect that produced the collapsed checkpoints is a separate
matter, diagnosed in `environment.md`. This finding does not depend on it:
the four checkpoints are simply four models, and the point is what the metric
did while they got worse.
