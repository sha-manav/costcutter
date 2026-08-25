# STOPPED — week 1 complete, NO-GO, awaiting three decisions

**Reason.** Nothing is broken. The calibration gate completed: 810 rows, three trials per cell, $1.007 of the $8.00 line item, 15 infrastructure errors (1.9%) excluded from their denominators. Week 1 exits **NO-GO** -- no model cleared the S2 band, so per SPEC §2 no data generation starts. The full result, the failure-mode breakdown and the limitations are in artifacts/environment.md. This file records the stop so a fresh session does not mistake a finished week for an interrupted one; the previous contents were a stale ERPNext-unreachable record from a Ctrl-C that landed mid-reset, since fixed.

| | |
|---|---|
| Line item | `calibration_gate` |
| Reserved | $8.00 |
| Spent | $1.01 |
| Remaining | $6.99 |
| Rows completed | 810 |
| Rows remaining | 0 |

## Resume

```bash
# Week 2, once the decisions above are made:
source infra/env.docker.sh
export OPENROUTER_API_KEY=...
bash scripts/run_gate_pool.sh 6 --require-model --resume --trials 3

# If ERPNext is not running (after a reboot):
bash infra/setup_docker.sh
.venv/bin/python scripts/provision_docker_sites.py --count 6
```

## Needs a human decision

Three decisions, none of which code or the spec can settle.

1. **Proceed to week 2 despite NO-GO?** The go/no-go blocks *data generation*
   (weeks 3-4), not authoring and measurement, and publishing the environment
   standalone needs no model. SPEC §2's remedy for NO-GO is "the harness is the
   problem" -- the failure-mode breakdown says it is not: step-budget exhaustion
   is 0-2%, and failures are genuine task errors and policy non-compliance.
   Retuning difficulty is forbidden (SPEC §10.3) and should stay forbidden.

2. **Base model: 32B by the rule, or 14B by the evidence?** The precommitted
   order selects 32B, which has no detectable harness gain (+6.2%, spans zero)
   and the worst violation rate (19.4%). 14B is the only model with a
   significant positive effect (+13.7% [+3.4, +23.6]) and the highest S2
   (31.1%). Following the rule means training the model the corrected harness
   demonstrably does not help. Overriding it is defensible but must be a
   documented, deliberate exception rather than a quiet preference.

3. **Figure 1 becomes per-model with intervals.** A single averaged S1->S2 bar
   reports roughly zero and hides all three results. Not blocking week 2's
   measurement -- the same rows are collected either way -- but it changes what
   the figure claims.

Infrastructure is ready: six-site pool provisioned and isolation-verified,
process-per-site driver, provider pinning, wall-clock request bound,
interrupt-safe reset. Week 2 should run in ~10 hours rather than ~60.


Per SPEC §12.4 no workaround was attempted: no model was substituted, no
provider was swapped, and no offline or simulated provider was used.
