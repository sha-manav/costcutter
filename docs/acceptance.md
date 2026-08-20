# Acceptance criteria

Status of each criterion from the build spec, with where to check it.

| criterion | status | evidence |
| --- | --- | --- |
| `distill/` provably cannot import `oracle/` | met | `tests/test_isolation.py` — AST inspection of every module under `shadow/distill/` and `shadow/capture/`, plus a check that importing the pipeline pulls no `oracle` module into `sys.modules` |
| Traffic generator rejects EVAL template ids | met | `tests/test_heldout.py` — asserts `HeldOutViolation` for every held-out template, and that the generator's source calls the guard |
| Baseline (A) numbers exist and are reproducible before M4 | met | condition A was built and run at commit `M2:` before provenance or induction existed; `artifacts/results.jsonl` |
| ≥15 verified tools with support ≥3 | **not met — 8** | see finding 7 in `FINDINGS.md`. The observation set contains eight task types; reaching fifteen would mean lowering `min_support` below the point where a signature is evidence of anything, or observing more kinds of work. All 8 are verified against the live instance. |
| Provenance trace printable and human-auditable for any episode | met | `python -m shadow.cli trace ep0009` (add `--verbose` for literals and constant path segments) |
| Full benchmark runs unattended from a clean ERPNext with one command | met | `bash scripts/run_all.sh 4 3` |
| All three charts generated from `results.jsonl` | met | `artifacts/charts/` — coverage vs sessions, cost per successful task, latency distribution |
| `FINDINGS.md` written, including what failed to synthesize and why | met | seven findings, with the failure taxonomy as the longest section |

## Deviations from the spec, and why

**ERPNext installed natively rather than from containers.** The sandbox's
egress policy blocks container-registry blob CDNs. `infra/pwd.yml` (the
official compose file) is kept and is the preferred path;
`infra/setup_erpnext.sh` installs the same version from source. See
`docs/environment.md`.

**No model in the loop.** No API credentials were available, so both agent
conditions ran on deterministic stand-in policies. Success is oracle-checked,
token counts are measured from the real prompts, and latency excludes
inference. `FINDINGS.md` states which numbers this affects and in which
direction. Every result row carries `usage.simulated: true`.

**Retention is 67%, not "well under 20%".** The capture is host-scoped and one
browser context serves all 96 demonstrations, so assets are fetched once. The
compression the spec was pointing at happens at the load-bearing trim
instead: 195 of 1,020 episode records, or 12.1% of raw flows.
