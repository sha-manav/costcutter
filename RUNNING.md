# Running this in Claude Code on your own machine

The short version: clone, stand up ERPNext, install the Python deps, then
start `claude` in the repo root. `CLAUDE.md` loads automatically and carries
the operating rules.

**Your machine unblocks what this container cannot do.** The cloud container
runs behind an egress allowlist that returns 403 for Docker registries and
for openrouter.ai, which is why `artifacts/HALT.md` exists. Locally there is
no allowlist: `docker pull` works and OpenRouter is reachable, so the
calibration gate can actually run.

## 1. Clone

```bash
git clone https://github.com/sha-manav/costcutter.git
cd costcutter
git checkout claude/shadow-http-tool-synthesis-upjv6m
```

## 2. ERPNext

Docker is the easy path and it works locally.

```bash
git clone https://github.com/frappe/frappe_docker.git /tmp/frappe_docker
cd /tmp/frappe_docker
docker compose -f pwd.yml up -d          # ~5 min on first run
```

That serves a site on `http://localhost:8080`. Point the harness at it:

```bash
export ERPBENCH_BASE_URL=http://localhost:8080
export ERPBENCH_SITE=frontend            # whatever pwd.yml names the site
```

The native installer (`infra/setup_erpnext.sh`) is the fallback for machines
where Docker is unavailable; it is what this container used, and it takes
considerably longer.

Seed and snapshot once, so resets are fast:

```bash
python -m shadow.cli seed                # deterministic data
python -c "from oracle.reset import snapshot; print(snapshot())"
```

## 3. Python

```bash
python3.11 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/playwright install chromium    # only for the browser surface
.venv/bin/python -m pytest -q            # expect ~126 passing
```

## 4. Keys — environment only, never a file

```bash
export OPENROUTER_API_KEY=...            # Qwen, for the calibration gate
export ANTHROPIC_API_KEY=...             # direct, not via OpenRouter, so
                                         # cached-token counts stay readable
```

## 5. Start Claude Code

```bash
cd /path/to/costcutter
claude
```

Then, as the first message:

> Read CLAUDE.md and SPEC.md, then artifacts/HALT.md. We are resuming week 1
> at the calibration gate. Run preflight first.

`CLAUDE.md` is the INSTRUCTIONS file and loads every session, so the
operating rules — halt behaviour, the budget ledger, the carried-forward
findings — do not have to be restated.

## What to expect at the gate

```bash
.venv/bin/python -m erpbench.preflight   # refuses if anything is missing
```

Preflight is a refusal, not a warning. It checks the budget line, key
presence, provider reachability, the model-dependent cache floor, disk, and
that every ERPNext site is healthy and resettable.

The gate itself measures S1 (naive harness) and S2 (corrected harness) on
the 15 calibration templates, against the precommitted fallback order
Qwen3-8B → 14B → 32B, taking the first model that lands S1 in 15–35% and S2
in 35–65%. Budget is $8; expect a few dollars.

## The one thing not to do

Do not adjust task difficulty to reach the band. SPEC §10.3 tunes difficulty
once, on the calibration split, before any evaluation template exists. If the
whole fallback order misses, that is reported and the largest model is used
with S1 documented as below band.

## Rollout throughput

`artifacts/environment.md` records ~780 rollouts/hour at six ERPNext sites on
4 cores, with 59% of each rollout spent on database reset. More cores raise
that; more sites past six do not.

```bash
.venv/bin/python -m erpbench.throughput --sites 6 --rounds 3
```

Re-measure on your hardware before sizing anything — it is the input the
spec cannot supply.
