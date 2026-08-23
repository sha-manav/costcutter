# HALT

**Reason.** The calibration gate is built and refuses to start. Two of the four blockers in the previous halt are now cleared: openrouter.ai is reachable (HTTP 200), and erpbench/gate.py -- which had never existed in this repository's history -- is written, with 34 tests covering the precommitted rule, the error/denominator split, and the refusal paths. `python -m erpbench.gate --require-model --resume` runs, prints its 270-row scope, and stops on preflight without resetting a site or spending anything. What remains is external to the code: OPENROUTER_API_KEY is absent from the environment, and there is no ERPNext for the harness to operate.

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
# 1. Key into the environment only -- never a file (SPEC §10.13).
export OPENROUTER_API_KEY=...

# 2. Docker, interactively (it needs a sudo password):
brew install --cask docker && open -a Docker

# 3. ERPNext, then seed and dump so pooled sites can reset:
git clone https://github.com/frappe/frappe_docker.git /tmp/frappe_docker
docker compose -f /tmp/frappe_docker/pwd.yml up -d
export ERPBENCH_BASE_URL=http://localhost:8080 ERPBENCH_SITE=frontend
.venv/bin/python -m shadow.cli seed

# 4. Preflight must print PREFLIGHT OK before the gate is allowed to run:
.venv/bin/python -m erpbench.preflight --line-item calibration_gate --sites 6

# 5. The gate. Refuses on its own preflight; --resume skips completed rows.
.venv/bin/python -m erpbench.gate --require-model --resume
```

## Needs a human decision

Two things, both requiring a human at a terminal:

1. **`OPENROUTER_API_KEY`.** Not in the environment. Export it in the shell that
   runs the gate -- never into a file, config, or commit (SPEC §10.13). Egress is
   open and all three Qwen models were confirmed present on OpenRouter's model
   list, so the key is the only thing between here and a live call.

2. **Docker Desktop, which must be installed interactively.** `brew install
   --cask docker` failed and rolled itself back: it runs `sudo mkdir -p
   /usr/local/bin` and sudo cannot read a password from a non-interactive shell.
   Run it yourself, then start Docker.app once so the daemon is running.

   After that, ERPNext still needs standing up and seeding. `erpbench/sites.py`
   provisions its pool by restoring `artifacts/seed.sql`, which is gitignored and
   was never committed, so the seed has to be regenerated on the new instance
   (`python -m shadow.cli seed`, then dump) before a pooled site can reset. The
   site pool also shells out to `mariadb` and expects a Frappe bench layout; under
   the frappe_docker path those live inside the containers, so `BENCH_ROOT`,
   `ERPBENCH_BASE_URL` and `ERPBENCH_SITE` need pointing at them. This is the one
   remaining piece of week-1 work that is genuinely unfinished rather than blocked.

Not a blocker, but worth knowing before reading the first numbers: OpenRouter does
not serve a prompt cache for these Qwen models, so `cached_input_tokens` will be
zero for the whole gate. That is a real property of the provider, not the silent
below-the-floor failure INSTRUCTIONS §4 describes -- config.yaml prices their
cached rate identically to input so no discount is banked that does not exist.


Per SPEC §12.4 no workaround was attempted: no model was substituted, no
provider was swapped, and no offline or simulated provider was used.
