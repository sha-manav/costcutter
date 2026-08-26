# HALT

**Reason.** 5 consecutive rows ended in an infrastructure error; last was 'APIError: litellm.APIError: APIError: OpenrouterException - [Errno 8] nodename nor servname provided, or not known'. Individual errors are expected and excluded from the denominator, but a streak means something systemic and the remaining rows would measure nothing

| | |
|---|---|
| Line item | `api_anchors` |
| Reserved | $12.00 |
| Spent | $2.52 |
| Remaining | $9.48 |
| Rows completed | 9 |
| Rows remaining | 14 |

## Resume

```bash
python -m erpbench.gate --require-model --resume
```

## Needs a human decision

Nothing beyond the reason above.

Per SPEC §12.4 no workaround was attempted: no model was substituted, no
provider was swapped, and no offline or simulated provider was used.
