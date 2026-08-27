# HALT

**Reason.** adapter failed during 83e62d63c68456e342d9: login to http://localhost:8080 failed: 500

| | |
|---|---|
| Line item | `contingency` |
| Reserved | $18.00 |
| Spent | $0.09 |
| Remaining | $17.91 |
| Rows completed | 38 |
| Rows remaining | 66 |

## Resume

```bash
python -m erpbench.gate --require-model --resume
```

## Needs a human decision

Nothing beyond the reason above.

Per SPEC §12.4 no workaround was attempted: no model was substituted, no
provider was swapped, and no offline or simulated provider was used.
