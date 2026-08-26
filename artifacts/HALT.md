# COMPLETE — week 2 done, GO on Qwen3-14B

**Reason.** Nothing is halted. Week 2's evaluation run completed: 2,160 rows across 40 templates x 3 firms x 2 harness variants x 3 models x 3 trials, $2.37 of the $12 api_anchors line, 111 infrastructure errors (5.1%) excluded from their denominators. Qwen3-14B clears both bands and the go/no-go is **GO**, so data generation is unblocked. Full results, the retraction of week 1's sign-reversal headline, the holdout breakdown and the limitations are in artifacts/environment.md under 'Week 2'. This file previously held a genuine mid-run halt -- a transient 403 from ERPNext tripped the verifier guard on E28 -- which resume recovered; all 40 templates have their full 54 rows and nothing was corrupted.

| | |
|---|---|
| Line item | `api_anchors` |
| Reserved | $12.00 |
| Spent | $2.39 |
| Remaining | $9.61 |
| Rows completed | 2160 |
| Rows remaining | 0 |

## Resume

```bash
# Week 2's remaining deliverable — publish the environment standalone.
# It needs no model and stands on its own regardless of weeks 3-5.

# Week 3 begins with teacher traces ($25 line), now unblocked by the GO:
source infra/env.docker.sh
export OPENROUTER_API_KEY=...   # type it; do not paste from a doc
# (teacher-trace runner is not yet written)
```

## Needs a human decision

Nothing blocking. Three things to decide before week 3 starts.

1. **The template-level holdout is not significant for any model.** That is SPEC
   §4's real generalization number, and every interval spans zero at n~75 per
   arm. Options: accept and report it as a stated limitation, or raise trials on
   the holdout templates specifically to narrow it before training begins. The
   second is cheap (~$1) and would make the week-6 claim much stronger, because
   after training there is no clean way to go back and strengthen a pre-training
   baseline.

2. **32B scores higher than 14B on S2 (46.3% vs 39.6%) but the rule selects
   14B**, first to clear. Following the rule is correct and is what was done.
   Worth deciding deliberately whether weeks 3-5 train 14B as selected, or 32B
   as the stronger model with the deviation documented.

3. **Pin the serving provider from row one in week 5.** Weeks 1-2 ran on
   OpenRouter's default routing across upstream providers with differing
   quantizations. That is noise rather than bias here, but the Firm C blind pass
   is one-shot and should not carry an uncontrolled variable.

Infrastructure is ready: six-site pool, process-per-site driver, thread-based
request bound, split-aware merge, frozen holdouts, 252 tests passing.


Per SPEC §12.4 no workaround was attempted: no model was substituted, no
provider was swapped, and no offline or simulated provider was used.
