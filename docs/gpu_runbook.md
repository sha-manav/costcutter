# Serving the trained checkpoints and measuring S3

Together fine-tunes Qwen3-14B and will not serve it, so evaluation runs
against a GPU you rent. This is the whole path from a bare box to Figure 1's
third stage.

## What you need

One 80GB card. Qwen3-14B in bf16 is ~28GB and vLLM wants headroom for the KV
cache; an A100-80GB or H100-80GB at $1–2/hour is ample. A 40GB card would
need quantisation, which changes the serving path and therefore the
comparison — avoid it.

## 1. On the GPU box

```bash
pip install vllm
# copy artifacts/checkpoints/*.tar.zst across, then:
mkdir -p /models/t3 && tar --zstd -xf t3.tar.zst -C /models/t3
```

The archives contain merged weights, not a LoRA adapter, so vLLM serves them
directly with no base model needed alongside.

```bash
python -m vllm.entrypoints.openai.api_server \
    --model /models/t3 --served-model-name erpbench-t3 \
    --max-model-len 16384 --port 8000
```

`--max-model-len 16384` matters. Rollouts run 20+ turns and the corrected
schema is 619 tokens; the default can truncate a long recovery trace, which
would depress S3 for a reason that has nothing to do with training.

## 2. Point the harness at it

```bash
export OPENAI_API_KEY=dummy                 # vLLM ignores it, litellm requires it
export OPENAI_API_BASE=http://<gpu-host>:8000/v1
```

litellm routes `openai/erpbench-t3` to any OpenAI-compatible server.

## 3. Measure S2 and S3 on the same path

**Both arms must run against this endpoint.** S1/S2 were measured through
OpenRouter, and comparing a Together-trained, vLLM-served S3 against an
OpenRouter-served S2 would confound training with serving path — the same
class of defect this project has already found four times. Re-measuring S2
here costs one extra run and removes the confound entirely.

```bash
source infra/env.docker.sh
# S2: the untrained base, this endpoint
bash scripts/run_gate_pool.sh 12 --split evaluation --bucket template_holdout \
    --templates "$(cat artifacts/fixed_thirteen.txt)" \
    --models openai/qwen3-14b-base --line-item contingency \
    --require-model --resume --trials 12

# S3: the trained checkpoint, same endpoint, same templates, same trials
bash scripts/run_gate_pool.sh 12 --split evaluation --bucket template_holdout \
    --templates "$(cat artifacts/fixed_thirteen.txt)" \
    --models openai/erpbench-t3 --line-item contingency \
    --require-model --resume --trials 12
```

Serving the base alongside the checkpoint needs a second vLLM process on
another port, or sequential runs on one box.

## 4. The stage ladder, if budget allows

T1 and T2 the same way gives Figure 5 — the full metric set after each
curriculum stage, including the behavioural metrics. Order of value if the
clock runs out: S3 first, then T1 (recovery is the largest bucket and the
strongest prediction), then T2.

## What to expect, and what would be suspicious

The behavioural metrics are the sharper instrument here. Recovery rate should
rise most — 116 of 316 traces were recovery, and the base model had almost
none of that behaviour. Watch `recovery_events`, `repeated_ineffective_calls`
and `policy_consulted_before_first_mutation` in the row output.

A jump in success with no movement in the behavioural metrics would be
suspicious rather than good: it would suggest the model learned the answer
distribution rather than the behaviour, and the template-level holdout is
what would catch that.
