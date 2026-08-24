# STOPPED — week 1 gate complete, NO-GO

**Reason.** Not a failure. The calibration gate ran to completion -- 270 rows, one lost to a provider error and excluded from its denominator -- and week 1 ends where SPEC §2 says it must when the harness does not earn its gain: **NO-GO, no data generation**. Qwen3-14B is the base model by the precommitted order (S1 31.1%, S2 44.4%, both in band). The harness gain is +13.3 points, which clears the bar; violations went 5/45 to 6/45, and SPEC §2 requires the gain without increasing them, so the rule returns NO-GO. That margin is one run and at n=45 is not distinguishable from zero. The substantive finding is separate and larger: the corrected harness made Qwen3-8B worse by 6.7 points and Qwen3-32B worse by 6.0, and a harness effect that reverses sign across model scale is not a harness effect. Full numbers and the diagnosis are in artifacts/environment.md under 'Week 1 exit'.

| | |
|---|---|
| Line item | `calibration_gate` |
| Reserved | $8.00 |
| Spent | $0.23 |
| Remaining | $7.77 |
| Rows completed | 270 |
| Rows remaining | 0 |

## Resume

```bash
# Nothing is broken; this is a decision point, not a fault.
# To re-run the gate after a harness change (rows scored under changed
# assertions are re-run automatically; a harness change is not detected,
# so archive the old results file first):
source infra/env.docker.sh
export OPENROUTER_API_KEY=...
mv artifacts/calibration_gate.jsonl artifacts/calibration_gate_harness_v1.jsonl
.venv/bin/python -m erpbench.gate --require-model --resume

# To add trials instead:
.venv/bin/python -m erpbench.gate --require-model --resume --trials 3
```

## Needs a human decision

Three decisions, none of which I should make alone. All are cheap:
the entire 270-row gate cost $0.22, and $7.78 of the line item remains.

1. **Strip the scoring hint from the corrected schema, then re-run.** Its GUIDANCE
   ends "Escalating or abstaining when the policy requires it counts as success;
   writing anyway does not." That is a statement about the scoring function, not
   about the system under test, and the naive harness never sees it. SPEC §2 says
   any hint voids Figure 1. The behaviour matches what a reward hint would produce:
   abstentions per 45 runs go 0->36 on 8B and single-step runs go 1->30, which is
   why S2 lands below S1 for the two models that are not 14B. This is the one I
   would do first; Figure 1 does not survive as it stands. Cost ~$0.22, ~2h.

2. **Raise trials from 1 to 3.** The gate currently has no uncertainty intervals at
   all, and SPEC §11 requires them on every paired comparison. The go/no-go turning
   on a single run is the immediate symptom. This is not a difficulty change, only
   n. Cost ~$0.66 total, ~6h.

3. **Decide, on the record, whether `abstain` and `escalate` belong in the naive
   schema.** They are absent, and were used zero times across all 135 naive runs.
   Leaving them out is defensible -- SPEC §2 defines naive as "undocumented
   actions" -- but it means the variants are not compared on the same action space,
   and 29 of 45 calibration instances have "write nothing" as the correct outcome.
   Whichever way it goes, it should be a decision rather than an accident.

Nothing is blocked on infrastructure. ERPNext is up, the seeds are built, preflight
passes every check when a key is exported, and 196 tests pass.


Per SPEC §12.4 no workaround was attempted: no model was substituted, no
provider was swapped, and no offline or simulated provider was used.
