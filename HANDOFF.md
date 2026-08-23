# Handoff — resuming this work in a terminal session

The conversation that produced this does not transfer; the state does. Every
decision that matters is in a tracked file, which is why a fresh session can
pick it up cold.

Read in this order: `CLAUDE.md` (auto-loads), `SPEC.md`, then this file, then
`artifacts/HALT.md`.

## Where the work stopped

**Week 1 infrastructure is complete. The calibration gate is halted, $0.00
spent**, because openrouter.ai returns 403 from the cloud container's egress
allowlist and `OPENROUTER_API_KEY` was absent. Nothing was substituted —
Claude was reachable, and using it would have violated SPEC §12.4 and tuned
the difficulty band against a model that will never be trained.

**A local machine has no allowlist, so running there unblocks the gate.** See
`RUNNING.md`.

## Done, with the numbers

| Deliverable | State |
|---|---|
| Docker + ERPNext | daemon works; **image pulls 403** in the cloud container, so the native installer is used. Deterministic seed, **5.8 s** full reset |
| `SystemAdapter` | ERPNext impl; **whole-database** snapshot, 20,123 rows / 139 doctypes in **1.4 s**; idle diff empty, real writes caught exactly |
| Both harnesses | naive 134 tokens / corrected 639; `_execute` shared; **6 integrity tests** |
| Instrumentation | SPEC §7, recorded live |
| `WorkflowTemplate` | assertions and envelopes generated from params; 729-point grid |
| Site pool | 8 independent sites, one bench, Host-header routed |
| **Rollouts/hour** | **~780 at 6 sites**; 59% reset, 34% snapshot, 7% actions |
| Three firms | authored in full, entity sets disjoint |
| **Firm C** | **frozen**, fingerprint `31b4c8a90ab9754a7b9f3fafd0aad8a3fd9a3415` |
| 15 calibration templates | quarantined structurally; 7/15 diverge across firms |
| Preflight + ledger | refuses rather than warns; per-line-item budget, Firm C reserve locked |

126 tests pass.

## Decisions a fresh session must not silently undo

- **Firm C is frozen.** `tests/test_firm_c_frozen.py` fails if its policy or
  world drifts. The correct response to that failure is to revert the change,
  never to update the constant.
- **Difficulty is tuned once**, on calibration, before any evaluation
  template exists. If the whole Qwen fallback order misses the band, report
  that and use the largest model with S1 documented as below band.
- **Calibration templates never appear in a reported figure.**
  `assert_reportable()` raises on any `C`-prefixed id.
- **The corrected harness earns its gain through general primitives only.**
  No template, firm, threshold or workflow may be named in its schema.
- **Local git tags do not survive**, because the remote rejects tag refs with
  403. `harness-v1` and `firm-c-frozen` exist only as tracked files and
  fingerprint tests.

## Two bugs found by measuring, worth not repeating

**Throughput.** The first sweep reported 656 rollouts/hour with sporadic
failures. Both causes were mine: a malformed connection-kill statement, and
`ThreadPoolExecutor.map` over a flat job list letting two rollouts share one
site, so one reset dropped a database another was mid-snapshot on. Corrected:
**776/hour, zero failures**. A site is owned by exactly one worker.

**Counterfactual check.** It reported 4/15 divergence and I nearly concluded
the templates were broken. The check compared assertion *classes*, so
"submit" and "leave in draft" looked identical. Assertions now carry
`expects`; the real figure is 7/15.

Both are the same lesson, and it is the one INSTRUCTIONS §7 states: when a
measurement looks wrong, suspect the harness before the subject.

## Next action

```bash
.venv/bin/python -m erpbench.preflight      # refuses if anything is missing
```

Then the gate: S1 (naive) and S2 (corrected) on the 15 calibration templates,
against Qwen3-8B → 14B → 32B, first model to land S1 in 15–35% and S2 in
35–65%. Record every attempt including misses. Budget $8; expect a few
dollars. Stop at the gate and report before authoring evaluation templates.

## Security

An Anthropic API key was pasted into the originating conversation and is in
that session's transcript. **Rotate it.** Do not copy session `.jsonl` files
between machines; they contain whatever was pasted.
