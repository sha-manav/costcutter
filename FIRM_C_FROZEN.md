# firm-c-frozen

Firm C — **Calder & Rowe** — is the blind set. Authored in full in week 1,
frozen here, evaluated **once** in week 5. It appears in no training data and
in no method selection (SPEC §10.6).

    tag          firm-c-frozen  (local; the remote rejects tag refs with
                 HTTP 403, so this file is the durable record)
    fingerprint  31b4c8a90ab9754a7b9f3fafd0aad8a3fd9a3415
    seed image   artifacts/firm_seeds/firm_C.sql   (8,297,696 bytes)
    manifest     artifacts/firm_seeds/firm_C.json

The fingerprint covers the policy text **and** the seeded world. Either
drifting means the blind pass measures a different firm than the one frozen,
and nothing downstream would notice. `tests/test_firm_c_frozen.py` fails if
it changes; the correct response to that failure is to revert the change,
never to update the constant.

## What is frozen

| | |
|---|---|
| Terminology | client (customer) · engagement (sales order) |
| Approval threshold | **$1,000** — at or above, write nothing and report why |
| Autonomy | **draft_only** — may never submit, under any circumstance |
| Missing entity | **abstain** — may not create a client or item |
| Evidence | an invoice must cite a **Delivery Note** |
| Chart of accounts | custom codes **and cost centres** (Advisory, Audit) |

World: 6 clients, 3 suppliers, 4 items, one ambiguous pair
(*Rowan Estates* / *Rowan Estate*), and two entities referenced by
instructions but deliberately absent (*Sedgwick Mills*, `CR-VAL-99`).

The entity set is **disjoint from Firm A's and Firm B's**, verified by test.
A name shared with a training firm would let recall substitute for policy,
which is precisely what the blind pass exists to rule out.

## Why C is the strictest firm

C is the only firm where the correct answer is most often *no write at all*:
above $1,000 it abstains, a missing record makes it abstain, a missing
delivery note makes it abstain, and it may never submit. An agent trained on
Firm A — where creating the missing customer and submitting is correct — has
learned the opposite reflex on every one of those axes. That is the transfer
the study is trying to measure.

## Unfreezing

Do not. If a defect in C is discovered before week 5, record it in the
write-up as a limitation and evaluate against C as frozen. Editing C after
seeing any model behaviour turns the blind pass into a tuned one.
