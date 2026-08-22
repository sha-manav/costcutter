# HALT

**Reason.** The calibration gate cannot run from this container. OpenRouter is not reachable: https://openrouter.ai returns 403 Forbidden from the session egress proxy, which is an organization policy denial. OPENROUTER_API_KEY is also absent from the environment. api.openai.com and api.fireworks.ai are blocked the same way; api.anthropic.com is reachable but returns 401 with no key set, and substituting Claude for the precommitted Qwen fallback order would violate SPEC §12.4.

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
# once OPENROUTER_API_KEY is set AND openrouter.ai is on the egress allowlist:
export OPENROUTER_API_KEY=...
python -m erpbench.gate --models qwen/qwen3-8b,qwen/qwen3-14b,qwen/qwen3-32b \
    --firms A,B,C --harnesses naive,corrected --trials 1 \
    --line-item calibration_gate --require-model --resume
```

## Needs a human decision

Two things, either of which unblocks the gate:

1. **Egress.** openrouter.ai must be added to this session's allowlist. That is an Anthropic/workspace-admin setting; it cannot be changed from inside the container, and routing around it is explicitly forbidden by the proxy documentation.

2. **Where to run.** If the allowlist cannot change, the gate has to run somewhere with open egress -- the week-3 GPU box would serve Qwen locally via vLLM and need no OpenRouter at all, which is the arrangement SPEC §1.2 already assumes.

A third option exists and I did **not** take it: running the gate against Claude, which is reachable. SPEC §2 fixes the fallback order as Qwen3-8B → 14B → 32B, and §12.4 forbids silently substituting a model. A Claude calibration number would not be the measurement the spec asks for, and would set the difficulty band against a model that will never be trained.

Per SPEC §12.4 no workaround was attempted: no model was substituted, no
provider was swapped, and no offline or simulated provider was used.
