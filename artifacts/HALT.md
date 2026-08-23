# HALT

**Reason.** The calibration gate cannot start. Preflight refuses on two checks. This is a different halt from the one it replaces: the original blocker -- openrouter.ai returning 403 from the cloud container's egress proxy -- is GONE. From this local machine https://openrouter.ai returns HTTP 200. What remains is (1) no OPENROUTER_API_KEY in the environment and (2) no ERPNext: the site pool needs a MariaDB-backed Frappe bench and `mariadb` is not installed, Docker is not installed, and artifacts/seed.sql -- which site provisioning restores from -- is untracked and absent. Two further gaps were found that preflight does not cover: erpbench/gate.py, named by the previous resume command, has never existed in this repository's history, so the gate has no runner and no agent loop; and shadow.llm._credentials_present() omitted OPENROUTER_API_KEY, so `provider: auto` resolved to the offline stub with an OpenRouter key set -- the gate would have produced 270 simulated rows tagged as Qwen numbers. That last one is fixed and regression-tested in this commit; the other three need a human.

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
# 1. Key into the environment only -- never a file (SPEC §12.13).
export OPENROUTER_API_KEY=...

# 2. Stand ERPNext up (see "Needs a human decision" below for the options),
#    then confirm the pool answers:
.venv/bin/python -m erpbench.preflight --line-item calibration_gate --sites 6

# 3. Preflight must print PREFLIGHT OK before anything else runs.
#    The gate runner itself still has to be built -- see item 3 below.
```

## Needs a human decision

Three things, in the order they block:

1. **`OPENROUTER_API_KEY`.** Not in the environment. Export it in the shell that
   runs the gate -- never into a file, config, or commit (SPEC §12.13). Egress is
   already open, so the key is the only thing standing between here and a live
   Qwen call.

2. **Where ERPNext runs.** This machine has no Docker and no MariaDB, and
   `infra/setup_erpnext.sh` is Linux-only (it assumes `/home/frappe`, a `frappe`
   unix user, `/opt/benchtools`, and `sudo -u frappe`). `erpbench/sites.py`
   additionally restores each pooled site from `artifacts/seed.sql`, which is
   gitignored and was never committed -- so even a working bench would not
   reproduce the seeded world without regenerating it. The options are: install
   Docker Desktop and use the frappe_docker path RUNNING.md describes; stand
   ERPNext up on the persistent VM SPEC §1.3 already assumes; or defer the gate to
   the week-3 GPU box, which per SPEC §1.2 would serve Qwen locally through vLLM
   and need no OpenRouter at all.

3. **The gate runner does not exist.** The harness, adapter, verifier, envelope
   diff, templates, firms, site pool and instrumentation are all present and
   tested (134 passing). What is missing is the loop that binds them: reset site,
   render instruction, call the model to a step budget, execute each action
   through `Harness.step`, snapshot-diff, score assertions and envelope, write the
   result row and the spend ledger entry. That is a deliberate build, not a
   detail, and it should not be written blind -- its site-pool configuration
   depends on decision 2.

Note also that the previous resume command passed bare `qwen/qwen3-8b`. litellm
routes OpenRouter models under an `openrouter/` prefix; unprefixed, those ids do
not reach OpenRouter at all. The preflight defaults now carry the prefixed form.


Per SPEC §12.4 no workaround was attempted: no model was substituted, no
provider was swapped, and no offline or simulated provider was used.
