# A curriculum that taught refusal, assembled entirely from correct traces

**Mechanism.** Every example in the corpus was correct. The rejection sampler
worked. The stage-assignment rule destroyed the model anyway, by inferring a
trace's *purpose* from its *outcome*.

## The rule

```python
def restage(row, planned):
    if is_hard_recovery(row): return "T1"    # an action failed, a later one succeeded
    if planned == "T1":       return "T2"    # <- the defect
    return planned
```

Read plainly: *a trace planned as recovery that contains no recovery event is
ordinary execution.* That is the entire bug, and it looks reasonable.

## Why it is not reasonable

Hard-recovery instances are not produced by asking a model to fail. They are
produced by drawing parameters where the first plausible action cannot
succeed — `entity=MISSING`, `evidence=STALE`, `entity=AMBIGUOUS` — and keeping
the trajectories where the model read the error and changed approach.

But on many of those draws **recovery is not the correct outcome.** If the
referenced customer genuinely does not exist and the firm's policy forbids
creating one, the right answer is to escalate and write nothing. The model
that does so has behaved perfectly.

And that trace passes rejection sampling *cleanly*. Acceptance requires every
required assertion to pass, no forbidden mutation, and no unexpected one. A
correct refusal satisfies all three — **declining an impossible task is a full
success**, which is a deliberate property of this benchmark, not an oversight.

So the trace is: correct, accepted, and contains no recovery event. `restage`
sees only the last of those three facts and files it under execution.

## What that did to the corpus

Of T2's 129 own examples, **84 arrived by this route** — demoted
hard-recovery traces, which is to say correct refusals. They outnumbered the
23 replayed T1 examples 3.6 to 1. The result, by stage:

| Stage | n | ends `done` | ends refusal | contains a write | mean turns |
|---|---|---|---|---|---|
| T1 | 116 | **116 (100%)** | 0 | **116 (100%)** | 9.81 |
| T2 | 152 | 59 (39%) | **93 (61%)** | **52 (34%)** | 4.03 |
| T3 | 120 | 45 (38%) | 75 (63%) | 43 (36%) | 4.53 |

The stage whose declared purpose is *ordinary execution — CRUD, child tables,
document linkage* is 61% refusal-terminating, and two thirds of its examples
contain no write at all.

## What it did to the model

Evaluated on identical rows, one server, one scoring function:

| Stage | Success | Tasks needing a write | Steps | Refuses first |
|---|---|---|---|---|
| Base | 21.5% | 6/175 | 2.12 | 67% |
| T1 | 23.7% | 6/175 | 2.09 | 71% |
| **T2** | 26.6% | **0/175** | **1.04** | 88% |
| T3 | 28.2% | 0/175 | 1.04 | 88% |

**T1 is intact and T2 is where it breaks** — exactly the stage whose data the
rule corrupted. T1 trains on 100% write-completing traces and preserves the
base model's write ability exactly (6/175 → 6/175). T2 trains on 34%
write-containing traces and the ability goes to zero, permanently. T3 inherits
a model that has stopped acting.

The dose-response is the argument. Three alternative explanations were checked
and eliminated: sequence truncation (longest example ~1,400 tokens, far under
any limit), loss masking (all three stages used one API call with identical
defaults, and T1 is the control that came through intact), and replay (applied
as specified, `round(116 × 0.2) = 23`).

## The general claim

**Rejection sampling on task success does not license using the accepted set
as-is, because "correct" and "demonstrates the lesson" are different
properties.** A filter that keeps only correct trajectories will happily keep
a correct refusal, and a correct refusal teaches refusal regardless of which
stage's file it is written into.

The failure needs three ingredients, all of which are good practice
individually:

1. **A benchmark where declining is sometimes correct** — necessary to model
   real operating constraints, and the reason this environment has three firms
   with conflicting policies.
2. **Rejection sampling on full task success** — the standard way to keep a
   synthetic corpus clean, and it did its job.
3. **A stage rule that reads the trajectory** to decide what a trace teaches.

Any two are fine. All three, and a curriculum stage silently fills with the
opposite of its purpose while every individual example remains defensible.

## The fix

Assign stage from the **parameter draw**, never from the trajectory. Whether
the correct outcome is a write, a recovery or a refusal is fixed by the
template, the firm and the parameters *before the model is invoked*:

```
policy-sensitive template     -> T3  policy, either way it resolves
no write required             -> T3  the answer is to decline
write required, obstructed    -> T1  recovery: the first action must fail
write required, clean         -> T2  execution: ordinary completion
```

Nothing consults what the model did. A rollout that was meant to teach
execution and instead refused is not reclassified — it is a rollout that
failed to demonstrate its lesson, and rejection sampling already drops it.

Two invariants now hold this in place, and both are stated as rules rather
than as patches to the observed instance:

- **Stage is a property of the task, not the attempt.** Two rollouts on the
  same drawn instance must land in the same stage. Tested across the whole
  corpus.
- **No stage may be dominated by traces that contradict its purpose.** T1 and
  T2 must be ≥80% write-completing. T3 is held to a *band*, 25–75%, not a
  floor — a policy stage made almost entirely of refusals teaches a reflex
  rather than a decision, which is the same failure one stage later.

The second invariant is the one that catches the class rather than the case.
Applying only the stage-assignment fix moves the refusal mass out of T2 and
into T3 — from 61% refusal in the execution stage to 90% refusal in the final
stage before shipping. That is not a fix; it is the same defect relocated to
where it would do more damage. The band forbids both.

## Provenance

Corpus `artifacts/teacher_traces.jsonl`, 505 rollouts, 259 accepted, every
rollout retained with its rejection reason. Evaluation rows in
`artifacts/s2_shards/`, `t1_shards/`, `t2_shards/`, `s3_shards/`, 312 per
stage, `harness_fingerprint` cd3855905de65f54 throughout.

One reporting detail a reader will check: the specified "~20% replay" is
implemented as 20% *of the prior pool*, which is 15% of the resulting batch.
A defensible reading, but not the only one, and at that ratio replay was never
going to outweigh 84 counter-examples.
