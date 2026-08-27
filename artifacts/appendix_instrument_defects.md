# Appendix: nine instrument defects — seven in the environment, two in the ruler

Every defect below shares one shape. **Nothing crashed. No test failed. The
artifact simply stopped describing what it claimed to describe.** Each was
caught by an invariant rather than by an exception, and each is now guarded
by a test that fails on the original defect.

They are recorded here in full, including the ones that invalidated published
numbers, because the measure of an instrument is not that it never breaks but
whether it can tell you when it has.

---

## #1 — The corrected harness told the model how it was scored

The corrected action schema's GUIDANCE section ended with the sentence
*"Escalating or abstaining when the policy requires it counts as success."*

That is a statement about the scoring function, not about what the actions do.
The naive variant never saw it, so the comparison between the two harnesses
was no longer a comparison of harness quality — one arm had been told the
answer. **270 rows were voided**, not discounted.

The invariant now enforced: the corrected schema may describe **capabilities
and error semantics**; it may never describe **objectives, scoring, or
procedure**. `test_harness_integrity.py` encodes that rule over three phrase
families rather than blacklisting the one sentence, and fails on the exact
original string. Five further hints were found and removed under the same
rule once it was written down.

## #2 — `provider: auto` fell back to a stub that produced plausible numbers

The provider resolution path could silently fall back to the offline
deterministic provider when credentials were absent. It emitted well-formed
rows with realistic token counts and costs. **270 rows of simulated output
would have entered the record as model measurements**, and nothing in the
output distinguishes them.

The fix is that `--require-model` **refuses** rather than warns, and every
credential the pipeline can use is enumerated in `CREDENTIAL_ENV_VARS` with a
test parameterised over the whole list — `OPENROUTER_API_KEY` was missing from
it, which is how a Qwen run could have been stubbed.

## #3 — A merge rewrote a published baseline

The pool driver runs `merge_gate_shards.py` when the last shard exits. Invoked
without `GATE_DEST` it defaulted to `evaluation_run.jsonl`, the published
2,160-row baseline. A holdout run's shards were merged into it: **396 rows
added and 156 existing rows silently replaced** by re-runs carrying the same
`run_id`.

An existing guard refused shards from more than one *split* — but both files
were the `evaluation` split. The guard was checking the wrong granularity: a
split is not a run. Nothing errored; the file grew, which reads as more data
rather than as corruption. It was found only because `git status` showed a
file modified that should not have been.

A merge now refuses any destination whose contents the incoming shards would
materially change. The baseline was restored from git and verified byte
identical (sha256 `e011786d1c005a03`).

## #4 — The ERP's own bookkeeping was scored as agent misbehaviour

ERPNext writes rows as a consequence of a permitted action — creating an
`Item` also creates its default row and a UOM conversion. Those were counted
as unexpected mutations, which is to say as the agent doing something it was
not asked to do.

**What makes this one distinct is where it hid.** It was 1.2% of evaluation
rows and invisible. It became the single largest rejection cause only when
Round 0 drove the harness into a regime the evaluation had never reached — *an
agent that mostly succeeds*. Evaluation rollouts largely failed or abstained,
so they wrote little and dragged little bookkeeping behind them. On
hard-recovery traces, 94 rollouts recovered and 4 survived rejection.

The general lesson is about evaluation coverage: **a benchmark exercised only
by models that mostly fail is not exercised in the region where a good agent
operates**, and defects can hide there indefinitely.

The fix is provenance-based rather than a name list. `derive_bookkeeping.py`
performs known-good primary writes against a seeded site with nothing else
acting and records what appears alongside; a derived row is excused only when
the write that causes it is present and was itself permitted. Rows that look
like bookkeeping but whose provenance is unaccounted for are still excused —
failing on an unmodelled side effect is the defect being fixed — but recorded
in `unclassified_derived`, so the next thing ERPNext starts writing is visible
rather than silent.

Correcting it moved the full-baseline gains by 0.2–0.3 points and the
pre-registered holdout figure from +8.8% to +7.5% [+0.5, +14.5].

## #5 — A database half-restored and said nothing

Mid-run, MariaDB aborted itself: `innodb_fatal_semaphore_wait_threshold was
exceeded for dict_sys.latch`. Six shards each dropping and recreating ~700
tables contend on one InnoDB dictionary structure until the watchdog fires —
the watchdog working as designed, not a bug in it. Raising the threshold would
only convert the crash into a stall.

It left four sites holding **146–532 of their 698 tables**. Nothing detected
the truncation; `reset()` had already returned success. The symptom surfaced
two layers away as `ERPNext site unhealthy`, and a `tabCustomer` count — the
obvious health check — passed on a site missing 166 other tables.

Three fixes: `reset()` serializes its DDL across processes with a file lock
(nearly free, since the conditional reset already skips ~86% of rollouts);
it verifies the restored table count against the dump's own `CREATE TABLE`
count and raises `ResetIncomplete` when short; and the restore gained a
timeout, having had none, so a wedged import hung a shard indefinitely rather
than halting it.

**The 267 rows collected around the crash were archived and re-run, not
defended.** Rows carry no site or timestamp, so they cannot be attributed to
the crash window; the argument that they were clean rested on `reset` being
atomic-or-raise, which is precisely the property that had just failed.

---

## #6 — in the diagnostic layer: a search that could not match, reporting zero

The five preceding entries are defects in the *environment* — the harness, the provider
path, the merge, the scorer, the database. This one is different in kind and
is numbered separately for that reason: **the tooling written to investigate a
defect was itself defective, and it sent the investigation the wrong way for
two rounds.**

Asked whether the training corpus was teaching refusal, the check was:

```python
n_ab = sum(1 for r in rows if '"abstain"' in json.dumps(r))
```

`json.dumps` of a row whose message content is the string `{"action":
"abstain"}` escapes the inner quotes, so the serialized text contains
`{\"action\": \"abstain\"}`. The pattern `'"abstain"'` cannot occur in it.
The check returned **0 for all three stages**, and zero read as a finding.

On the strength of that zero I published, in order: that the corpus contained
no refusal examples at all; that the trained model therefore "does the reverse
of its training set"; and that the cause must lie in the serving path rather
than the data. The first was false, the second followed from it, and the third
sent an afternoon into verifying checkpoints, chat templates and prompt
fingerprints — all of which came back clean, because nothing was wrong with
them. The actual answer was in the file the whole time: T2 is **61%
refusal-terminating and 34% write-containing**.

What makes it worth its own entry rather than a footnote:

- **A zero from a broken filter is indistinguishable from a real zero.** A
  crash would have been better. The check "worked" on every stage and agreed
  with itself three times, and that consistency read as corroboration when it
  was just the same bug running three times.
- **It inverted the conclusion rather than blurring it.** Not a
  wrong-by-a-margin measurement — the corpus was reported as containing the
  exact opposite of what it contains, and the retraction had to be published
  twice because the first correction inherited the bad premise.
- **The environment's own safeguards could not have caught it.** Fingerprints,
  refusal-on-ceiling, force-added artifacts, the 269-test suite — none of them
  cover a one-off analysis script. Every practice this project relies on
  protects the instrument, and this was a defect in the ruler used to inspect
  the instrument.

The lesson is narrow and practical: **a diagnostic that returns "none" should
be made to return "some" on a case known to contain some, before its zero is
believed.** The corrected check parses the assistant turns and reports the
terminal action distribution, which cannot silently return zero — an empty
distribution is visibly empty, and a parse failure shows up as
`<unparseable>` rather than as absence.

Counting the two families separately: **five defects in the environment, one
in the diagnostic layer.** The second category has no tests, no fingerprints
and no review, and this project has now been misled further by one instance
of it than by any single instance of the first.

## #7 — a freeze that did not cover what its own document claimed

`FIRM_C_FROZEN.md` stated that the fingerprint covers "the policy text **and**
the seeded world". It does not. `_fingerprint()` hashes the policy text, the
entity set, the threshold, the autonomy level and the evidence rule — all read
from the *manifest*. The SQL image is not in it.

And the image had changed: **8,297,696 → 7,856,385 bytes**, when the project
moved from a bare-metal ERPNext to a containerised one. `zstd`-level identity
was never checked and nothing failed.

**What the change actually was, stated precisely because it bears on whether
the blind pass is valid:** the `policy_sha` is identical across it
(`d426ca469419dc10`), the entity set is identical, and all three firms were
rebuilt from the same base dump on the same day. It is a change of *substrate*,
not of firm, and it predates any model being trained or run against C. The
narrower property the blind-pass claim needs — that C's policy and world are
what were frozen — does hold.

That is a real defect anyway, and the reason is the shape shared with every
other entry here: **it passed silently.** A freeze whose verification covers
less than its documentation claims is a freeze that can drift in the
uncovered region indefinitely, and the only reason this instance is benign is
luck about which region drifted. The test now pins `firm_C.sql` to the byte
count its own manifest records, so the next rebuild has to be acknowledged.

The same document also said Firm C is "evaluated **once** in week 5". It is
not, and a reader checking the artifacts would find 2,694 Firm C rows. Those
are all untrained base models — Qwen3 8B, 14B, 32B — from the week-2 baseline
and the calibration gate, and they exist **by design**: transfer to C is
measured against a base-model baseline on C, which has to exist to subtract
from. The property the transfer claim rests on is narrower and does hold: no
trained checkpoint had ever been evaluated on C, no C data appears in any
training corpus, and no C data informed method selection. The document now
says that instead, and a test enforces the first clause by scanning every
result file.

## #8 — in the diagnostic layer: a field with no mechanism behind it

Second entry in the diagnostic category, and the same shape as #6.

`adaptation_level` was in the row schema, in SPEC §12.5's `run_id` definition,
and in the results of every run ever recorded. It always read `"none"`,
because **nothing ever set it** — there was no adaptation mechanism at all.
The field described a dimension of the experiment that did not exist, and
three months of result files carry it.

Worse, `Job.rid` hard-coded the slot:

```python
return run_id(..., self.harness_variant, "none", self.trial_idx)
```

So the same task at 0, 1, 3 and 8 corrections produces **one** `run_id`. Had
the sweep been run against this, `--resume` would have written the first level
and then skipped the other three as already done, and the adaptation curve
would have been one measurement plotted four times — flat, at whatever the
first level happened to score, with nothing in the output indicating it.

This is diagnostic-layer rather than environment: no published number was
wrong, because the dimension was never exercised. What it would have corrupted
is the *next* measurement, and it would have corrupted it into a shape that
looks like a result — "adaptation has no effect" is a publishable sentence.

The distinction from #1–#5 and #7 is worth keeping. Those are defects in the
instrument: they silently changed what a number meant. #6 and #8 are defects
in the apparatus used to *investigate and extend* the instrument — a search
that could not match, and a field with no mechanism. That category has no
fingerprints, no invariants and no review, and both instances were found by
looking at something else.

**Running tally: seven defects in the environment (#1-#5, #7, #9), two in
the diagnostic layer (#6, #8).**

## #9 — the gate decision file, overwritten by every later run

Found during the final push, by reading a `git diff` that showed 184 deleted
lines in a file nothing should have touched.

`gate.py` wrote its decision to one fixed path, `calibration_gate_decision.json`,
on **every** invocation. That path holds the week-1 artifact recording which
base model the precommitted fallback order selected — the answer to "why
Qwen3-14B and not 8B", which SPEC §8 says is impossible to reconstruct later.

It had been overwritten twice: first by a Pareto anchor run, whose
`fallback_order` was `[haiku, sonnet, opus]`, and then by a phi-4 completion,
whose order was `[phi-4]` and whose verdict was "no model cleared". Both are
valid decision records for the run that produced them. Neither is the
calibration gate, and the file is named for the calibration gate.

Nothing errored. The file still parsed, still validated, still had the right
shape — it had simply stopped being the decision it was named for, and the
only reason it was caught is that `artifacts/` is force-added to git, so an
unexpected modification showed up in a diff.

**Same shape as #3**, which was a merge overwriting a published baseline, and
the same fix: decision files are now named after the run that produced them,
and a write that would change an existing file's `fallback_order` takes a
numbered suffix instead. The week-1 decision was restored from history, and
both overwriting versions are kept alongside it rather than discarded.

This is the second time a published artifact in this project was silently
replaced by a later run of the same tool. Once is a bug; twice is a category,
and the category is **tools that write to a fixed path regardless of what they
were asked to do.**

### Adjacent, unnumbered: truncated checkpoint downloads reported as success

Found while preparing the stage-ladder runs and recorded here because the
shape is identical. Both local checkpoint archives were corrupt — `zstd -t`
reports "premature end", and `t1.tar.zst` unpacks to 3 of its 8 weight shards
with no tokenizer files at all. `fetch_checkpoints.sh` runs curl under `set
-euo pipefail` and then prints the size it downloaded, which reads as a
successful fetch; both files sat on disk for a day looking finished. The
replacement verifies shard count against the count declared in the filename
and refuses to serve an incomplete checkpoint.

It is not numbered with the others because it corrupted no published result —
it was caught before use.

---

## What the set implies for methodology

Nine defects, and **not one announced itself**. Four of them would have
produced a publishable number: a harness comparison with the answer leaked
into one arm, 270 simulated rows presented as model output, a baseline quietly
overwritten by a later run, and an agent penalised for its ERP's internal
writes. The fifth would have scored rollouts against a database missing three
quarters of its tables.

The practices that actually caught them, in order of how much they earned:

1. **Invariants that fail loudly**, not warnings. `--require-model` refuses.
   A short restore raises. A merge that would disturb a destination refuses.
   Every one of these was previously a path that returned success.
2. **Fingerprints on every row** — scoring, harness, serving, split. Two of
   these defects were confirmed or excluded in a single comparison because
   rows carry what produced them.
3. **Force-adding results to git.** Defect #3 was found only because a
   tracked file showed as modified.
4. **Encoding the rule, not the instance.** After #1, the test forbids the
   *category* of statement; that is what surfaced the five further hints.
5. **Keeping rejected data.** Every Round 0 rollout is retained with its
   rejection reason, which is why the acceptance rate is measured rather
   than claimed — and why #4 could be localised to hard-recovery traces.

And one that has to be stated as a limitation rather than a practice: **#4 was
only visible once a competent agent ran the benchmark.** Coverage of the
success regime is not something the other four practices provide.
