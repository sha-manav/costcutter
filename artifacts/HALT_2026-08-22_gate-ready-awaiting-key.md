# HALT

**Reason.** Everything the calibration gate needs is in place except the OpenRouter key, which is deliberately not in my environment -- you chose to run the gate yourself so the key never enters my shells or this transcript. ERPNext v15.99.1 is up under frappe_docker and verified: the adapter snapshots 19,326 rows across 118 doctypes in 0.5s, the idle diff is empty, real writes are caught exactly, and a firm reset restores in a median 4.4s over six runs. All three firm seed images are built. Preflight passes every check except `key:openrouter`. The gate itself is verified end-to-end against the live instance with a scripted agent -- correct writes score clean, forbidden writes are caught, abstention and escalation pass -- and verified to halt on row 1 with zero rows written when handed an invalid key. 175 tests pass.

| | |
|---|---|
| Line item | `calibration_gate` |
| Reserved | $8.00 |
| Spent | $0.00 |
| Remaining | $8.00 |
| Rows completed | 0 |
| Rows remaining | 270 |

## Resume

```bash
source infra/env.docker.sh
export OPENROUTER_API_KEY=...
.venv/bin/python -m erpbench.preflight --check-adapter   # expect PREFLIGHT OK
.venv/bin/python -m erpbench.gate --require-model --resume

# If ERPNext is not running (after a reboot, say):
bash infra/setup_docker.sh
```

## Needs a human decision

Only one thing: run the gate. It is your key, so it is your shell.

    source infra/env.docker.sh
    export OPENROUTER_API_KEY=...
    .venv/bin/python -m erpbench.gate --require-model --resume

Four things worth knowing before you start it.

**It takes roughly two hours.** 270 rows, serial against one site, ~4.4s reset plus
~1s of snapshots plus model latency per row. It commits every 25 rows and `--resume`
skips anything already in artifacts/calibration_gate.jsonl, so interrupting it is
safe and restarting is cheap.

**Cost should be about $0.70 of the $8.00 line item.** Roughly 5.4M input and 160k
output tokens across the three models at OpenRouter's current Qwen rates. The soft
ceiling warns at 85% and the hard ceiling stops at the next task boundary, so it
cannot overrun the line item even if that estimate is wrong by an order of magnitude.

**It runs all three models rather than stopping at the first that clears.** SPEC §2
selects the first to clear, and `decide()` does exactly that -- but INSTRUCTIONS §8
also asks for what each model scored, and at this price the full set costs cents and
is impossible to reconstruct later. If you would rather stop early, pass `--models`
with a single id.

**Expect `cached_input_tokens` to be zero throughout.** OpenRouter serves no prompt
cache for these Qwen models. That is a provider property, not the silent
below-the-floor failure INSTRUCTIONS §4 describes; config.yaml prices their cached
rate identically to input so no discount is banked that does not exist.

When it finishes it writes artifacts/calibration_gate_decision.json and prints the
S1/S2 bands, the harness gain, the violation rates and the go/no-go for each model.
Hand me that file and I will write up the three week-1 decisions.


Per SPEC §12.4 no workaround was attempted: no model was substituted, no
provider was swapped, and no offline or simulated provider was used.
