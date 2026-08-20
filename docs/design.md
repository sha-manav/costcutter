# Design notes

## Why the load-bearing trim exists

A naive signature over every request in an episode fragments badly: the same
user task produces slightly different chatter each time (notification polls,
sidebar counts, list-settings saves), so two runs of "look up a customer"
rarely produce the identical request sequence. Grouping on that signature
yields many groups of support 1 and no tools.

`distill/induce.py` first reduces an episode to the steps that carry the
task:

* any non-GET request (it changes something), plus
* any GET whose response supplies a value a later step uses, plus
* the final meaningful request (for a read, that *is* the result).

Everything else is dropped. This does two jobs at once: signatures become
stable across repetitions, and the emitted tool makes the minimum number of
calls rather than replaying a page load.

## Why cross-episode stability gates provenance

Matching a value to an earlier response is easy; matching it to the *right*
earlier response is not. A customer name will appear in a dozen places in a
desk boot payload. `provenance.py` scores candidates by match quality, then
recency, then path depth — but the decisive filter is in `induce.py`: a
`from_response` binding survives only if the same `(step, json_path,
transform)` explained the value in a majority of the group's episodes.
A coincidental match in one episode does not repeat at the same path in the
others, so it is demoted to a parameter instead of being baked into a tool.

## Why the executor never replays credentials

The capture addon strips `Authorization`, `Cookie`, `Set-Cookie` and Frappe's
CSRF header into a separate store and never writes them into a record. The
executor logs in itself and reads a live CSRF token out of the desk boot
payload. Replaying a recorded `sid` would work for a few minutes and then
fail confusingly; worse, it would make the synthesized tools depend on the
session of whoever was observed.

## Why writes are verified against a reset instance

A write tool that returns HTTP 200 has not been verified. `verify/replay.py`
resets the database, snapshots the row counts of the doctypes the tool can
address, runs it, and requires an observable change — then resets again.
`--allow-writes` is never the default, in the verifier or the MCP server.

## Two coverage numbers, and why both are reported

Achieved coverage (from `results.jsonl`) is the fraction of agent actions on
held-out tasks that a synthesized tool served. It confounds catalog quality
with router quality. `bench/attainable.py` measures only catalog quality: it
is allowed to use the oracle to build arguments, so it reports the ceiling a
perfect router would hit. Reporting one without the other would either
undersell synthesis or oversell the agent.

## Enum induction and transfer

`induce.max_enum` turns a low-cardinality parameter into a JSON-Schema enum.
That is right for status fields and wrong for identifiers: a `doctype`
parameter observed with three values becomes a three-value enum, which is
exactly the constraint that blocks the tool from being pointed at a fourth
doctype on a held-out template. The executor does not enforce the schema, so
this affects a schema-obeying model rather than the mechanism — but it is a
real limitation and is reported as one.
