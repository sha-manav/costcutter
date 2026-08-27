# firm-c-frozen

Firm C — **Calder & Rowe** — is the blind set. Authored in full in week 1 and
frozen here. It appears in **no training data** and in **no method selection**
(SPEC §10.6).

**What "blind" means here, stated precisely**, because the looser phrasing this
file used to carry ("evaluated once in week 5") is not what happened and a
reader checking the artifacts would catch it. Firm C carries 2,694 rows, all
of them **untrained base models** — Qwen3 8B, 14B and 32B — from the week-2
baseline and the calibration gate. That is deliberate and necessary: transfer
to C is measured *against* a base-model baseline on C, which has to exist.

What is preserved, and what the claim rests on:

- **No trained checkpoint has ever been evaluated on Firm C.** Verified by
  scanning every result file: zero C rows for any `erpbench-*` model.
- **No Firm C data is in any training corpus.** Teacher generation is Firm A
  only (`run_teacher.py` hard-codes `get_firm("A")`).
- **No Firm C data informed method selection.** The harness comparison and
  the pre-registered thirteen were run on firms A and B.

So the single blind pass is the *trained model's* first and only contact with
this firm. That is the property the transfer claim needs; "evaluated once,
ever" was never the property and should not have been written.

    tag          firm-c-frozen  (local; the remote rejects tag refs with
                 HTTP 403, so this file is the durable record)
    fingerprint  31b4c8a90ab9754a7b9f3fafd0aad8a3fd9a3415
    seed image   artifacts/firm_seeds/firm_C.sql   (7,856,385 bytes)
    policy sha   d426ca469419dc10
    manifest     artifacts/firm_seeds/firm_C.json

The fingerprint covers the policy text and the **entity set** — client,
supplier and item names, the ambiguous pair, the absent records, the
threshold, the autonomy level and the evidence rule. `test_firm_c_frozen.py`
fails if any of those change, and the correct response to that failure is to
revert the change, never to update the constant.

**It does not cover the SQL image**, and this file previously claimed it did.
Corrected 2026-08-27, along with the byte count, which was stale:

| | At freeze (8dfffef) | Now (since 35c42d2) |
|---|---|---|
| seed image | 8,297,696 bytes | **7,856,385 bytes** |
| policy sha | d426ca469419dc10 | d426ca469419dc10 |
| entity set | unchanged | unchanged |

The image was rebuilt when the project moved from a bare-metal ERPNext to a
containerised one. That migration rebuilt **all three firms** from the same
base dump on the same day, and the firm's semantics — policy text, entities,
threshold, autonomy — are provably identical across it. It is a change of
substrate, not a change of firm, and it happened before any model was run
against C.

It is still a gap worth naming: the freeze was verified on the manifest and
not on the database, so a substrate change could pass silently, and did. The
test now also pins `firm_C.sql` to the byte count its own manifest records, so
a future rebuild has to be acknowledged rather than absorbed.

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
