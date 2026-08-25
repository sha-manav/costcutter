# INSTRUCTIONS.md — how to operate on this project

**Read `SPEC.md` first. It is authoritative.** This file carries context that is expensive or impossible to rediscover, plus the working agreements.

**Also copy this file to `CLAUDE.md` in the project root** so it loads automatically every session.

---

## 1. Setup, in order

Do not skip step 1. The entire project depends on it.

```bash
# 1. VERIFY DOCKER WORKS. vast.ai instances often run inside a container
#    and cannot nest Docker without a docker-enabled image or --privileged.
docker run --rm hello-world

# 2. Environment detection -> artifacts/environment.md
nvidia-smi --query-gpu=name,memory.total --format=csv
nvidia-smi topo -m
df -h

# 3. Source repo: harness, ERPNext setup, instrumentation, and the evidence
#    for the carried-forward findings below. Keep it. Do not delete it.
git clone https://github.com/sha-manav/costcutter.git ~/costcutter

# 4. Keys from the environment, never from a file
export ANTHROPIC_API_KEY=...      # direct, NOT via OpenRouter
export OPENROUTER_API_KEY=...
export FIREWORKS_API_KEY=...
```

**If Docker-in-Docker does not work**, run ERPNext on a separate small always-on VM and point the harness at it over the network. This is the preferred arrangement anyway — see §2.

---

## 2. The machine is ephemeral

vast.ai instances can be reclaimed. Treat local disk as scratch.

- **ERPNext should live off-box** on a cheap persistent VM (2 vCPU / 8GB). Stateful ERP data must survive GPU rental.
- `git push` after every meaningful artifact, not at end of session.
- Push trained adapters to HuggingFace or object storage **immediately** after each checkpoint.
- Nothing uncommitted is assumed to survive the night.
- Snapshot and commit every 25 rows on any run over 20 minutes.

**GPU allocation:** GPU 0 trains, GPUs 1–3 serve vLLM for parallel rollouts. Do not shard training across cards — 8B QLoRA does not need it and A100 PCIe makes it communication-bound. A100 is Ampere, so **no FP8**: bf16 for training, bf16 or AWQ/GPTQ for serving.

**Rollout throughput is bounded by ERPNext sites, not GPUs.** Run 6–8 with independent databases. Measure rollouts/hour in week 1 and size the dataset target to the measured number — this is the one input the spec cannot give you.

---

## 3. Repo provenance

The harness, ERPNext setup, and instrumentation originate from `github.com/sha-manav/costcutter`, a prior project whose tool-synthesis hypothesis was tested and lost. Carried forward:

- ERPNext docker compose, deterministic seed, full reset
- Corrected harness with composite actions: `field`, `link`, `select_field`, `grid`, `save`
- Tool/MCP path — fast rollouts, which is what makes training feasible
- Token / cost / cache instrumentation
- Oracle-check pattern
- The **naive** harness variant, now the frozen S1 ablation

The old repo also holds the commit-level evidence for the findings below. Cite it in the write-up.

---

## 4. Findings carried forward — do not rediscover, do not contradict

**Prompt caching has a silent minimum prefix length that is model-dependent and non-monotonic.** Opus 512 · Sonnet 1024 · Haiku 4096 tokens. Below the floor, the request returns uncached with **no error**. A prior run reported zero cached tokens across 97 rows because prefixes were 230 and 606 tokens. Verify non-zero `cached_input_tokens` on a two-call probe before every full run. Note the consequence: the cheapest model has the highest floor, so part of a large model's cost advantage is a discount the small model structurally cannot get.

**An action that cannot fail is one the agent cannot recover from.** A `save` action pressed Ctrl+S and returned unconditionally, so validation errors reported success and the model pressed save twenty times until its budget ran out. Every action in the corrected harness must fail with a typed reason.

**Actions absent from the action schema are actions the model cannot use.** Five composite actions existed in `perform()` but were undocumented, producing a false conclusion that the model could not handle child-table workflows. It was a harness defect. Documenting them moved the same model from 65% to 100% and cut cost ~5×.

**Small models fail by exhausting the step budget, not by answering wrongly.**
*Corrected 2026-08-24 by the week-1 calibration gate: this does not reproduce.*
*Across 810 rows and three model scales, step-budget exhaustion was 0-2% of*
*failures. Failures were genuine task errors (34-57%) and writing when policy*
*forbade it (23-33%). The likely reason is that the corrected harness documents*
*`abstain` and `escalate`, so a model that cannot proceed stops cleanly instead*
*of looping -- which makes the original finding harness-dependent rather than a*
*property of small models. Note also that unsafe writes rose monotonically with*
*scale (7% / 13% / 26%), so the safety risk grows with capability rather than*
*shrinking. T1's place at the head of the curriculum rests on the original*
*claim and should be revisited.* The original finding follows: Haiku scored 0/4 on ERP tasks while scoring in the 90s on tau-bench — every failure a step-ceiling timeout. This is behavioural, which is why T1 (tool use and error recovery) leads the curriculum.

**`provider: auto` silently fell back to offline stubs** and produced numbers that looked like model numbers. Always pass `--require-model`, and it must **refuse**, not warn.

---

## 5. Working agreements

**`artifacts/*` is gitignored. Force-add every result file.** This bit the prior project twice — an entire benchmark run existed only inside a container and was unreproducible from the repo. `git add -f artifacts/...` for every results file, figure, ledger, and frontier JSON.

**Anthropic models go direct, not via OpenRouter**, so cached-token counts are readable.

**Archive, never overwrite.** Prior results get a descriptive suffix, never replacement.

**The assembler must refuse stale inputs.** Compare table mtimes against the results file and fail rather than building a report from mismatched data. A prior write-up described a pipeline that no longer existed.

**Keys never written to any file, config, log, or commit.**

---

## 6. Halting — the default is stop and wait

Budget is capped at $100 and allocated by line item in SPEC.md Part 12. **The Firm C reservation is locked and may not be borrowed against**; it funds a one-shot, unrecoverable blind test in week 5.

On any budget ceiling, auth failure, quota exhaustion, provider error, OOM, container death, or verifier exception:

1. Flush completed rows to disk
2. `git add -f` and commit with the row count in the message
3. Write `artifacts/HALT.md` with the reason, balance, rows done and remaining, and **the exact resume command**
4. Print the summary to stdout
5. **Stop and wait for the user**

Never substitute a model, never fall back to a stub, never retry an auth failure, never continue past a hard ceiling, and never record an infrastructure error as an agent failure — those are `status: error` and are excluded from success-rate denominators.

---

## 7. Failure modes from the prior project

- Numbers reported from a harness that had since changed.
- A conclusion about model capability that was actually a harness defect.
- A benchmark run that existed only inside a container.
- Silent fallback to a stub provider producing plausible fake numbers.
- Results committed without force-add, so the repo could not reproduce them.

**When a model fails, suspect the harness first.** Check the action schema, check whether the failing action can report failure, check the step budget — before concluding anything about capability.

---

## 8. Week 1 exits on three decisions, not deliverables

Record all three, with numbers, in `artifacts/environment.md` and the commit log:

1. **Calibration band.** Did S1 land in 15–35% and S2 in 35–65% on the calibration split?
2. **Base model**, chosen by the precommitted order Qwen3-8B → 14B → 32B. First to clear wins. Write down what each scored.
3. **Go/no-go.** Did the corrected harness beat the base by ≥10 points without increasing violations?

"Qwen3-8B scored 6% on the naive harness across three difficulty settings, so we moved to 14B per the precommitted order" is a methods sentence that costs nothing now and is impossible to reconstruct later.

**If calibration fails, change the base model — never the task difficulty.**

---

## 9. The invariant most likely to erode

The corrected harness must earn its gain through **general primitives only**. Every time it is tempting to add something that makes a specific task easier, that is the harness effect leaking into task-specific tuning, and Figure 1 — the spine of the whole project — silently breaks.
