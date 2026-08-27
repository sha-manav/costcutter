# HALT — Anthropic credit exhausted mid write-drive (2026-08-27)

## Reason

Not an infrastructure fault and not retryable:

```
HALTED: quota or credit exhaustion calling claude-sonnet-5:
litellm.BadRequestError: AnthropicException -
{"type":"error","error":{"type":"invalid_request_error",
 "message":"Your credit balance is too low ...
```

All six shards halted on it. Per INSTRUCTIONS §6 no model was substituted, no
provider was swapped, and nothing was retried. The teacher is Sonnet on the
Anthropic API direct (INSTRUCTIONS §5: never via OpenRouter, so cached-token
counts stay readable), so there is no alternative route that preserves the
measurement.

## Balance

| Line | Spent | Allocated |
|---|---|---|
| write_drive | $2.97 | $10.00 |
| **total** | **$43.86** | **$85.00** ceiling |

`firm_c_blind` $15.00 untouched and locked. The halt is a *provider* credit
limit on the Anthropic account, not the project budget — $41 of project
budget remains unspendable through this route until the account is topped up.

## Rows done and remaining

- **117 of 1,800 planned rollouts** (300 per shard × 6), **47 accepted** at a
  40% write-completion acceptance rate.
- Rejections: 28 refusals (24 escalate, 4 abstain), 23 assertion failures,
  8 unexpected mutations, 5 step-budget.
- Everything generated is on disk in `artifacts/writedrive/traces_*.jsonl`,
  accepted and rejected alike, and is committed.

**Total write-completing traces now available across the whole corpus: 281.**

## What the partial run already shows

Worth recording independently of whether the run resumes: **Sonnet refuses on
24% of these draws** (28 of 117) — on instances where the entity exists, the
evidence is present, the amount is under every threshold and the information
is complete. There is nothing to escalate about. The refusal prior this phase
exists to fight is not only a small-model artifact; the teacher has it too,
which is a constraint on how much any corpus distilled from this teacher can
correct it.

## Exact resume command

Top up the Anthropic account, then:

```bash
cd ~/costcutter/costcutter
source infra/env.docker.sh
export ANTHROPIC_API_KEY=...          # from the environment, never a file
for i in 1 2 3 4 5 6; do
  nohup .venv/bin/python -u scripts/run_teacher.py --write-drive 300 \
    --site "erp0$i.localhost" --shard "$i/6" \
    --out artifacts/writedrive/traces_$i.jsonl \
    --line-item write_drive --resume \
    > artifacts/writedrive/shard_$i.log 2>&1 &
done
```

`--resume` skips by `run_id`, so completed rollouts are not repeated and the
$2.97 already spent is not spent again. The `write_drive` line stops the run
at $10.00 on its own.

## What proceeds without it

Phase 2 needs no Anthropic credit — the Firm B adaptation sweep and the Firm C
blind pass run the shipped checkpoint on local vLLM against local ERPNext.
Phase 3's open-model anchors run through OpenRouter; only its Haiku, Sonnet
and Opus anchors are blocked by this, and those are three points on the Pareto
figure rather than the figure itself.
