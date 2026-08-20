# Design notes

Why the pipeline is shaped the way it is. Each of these came out of running
it against a real application, not from planning.

## The load-bearing trim

A signature over every request in an episode fragments badly: the same task
produces slightly different chatter each time — notification polls, sidebar
counts, list-settings saves — so two runs of "look up a customer" rarely
produce an identical request sequence. Grouping on that yields many groups of
support 1 and no tools.

`distill/induce.py` first reduces an episode to the steps that carry the task:
any request that changes state, any request whose response supplies a value a
later step uses, and the final meaningful request (for a read, that *is* the
result). Everything else is dropped. Signatures become stable across
repetitions, and the emitted tool makes the minimum number of calls rather
than replaying a page load.

"Changes state" is decided by the mutation classifier, not by HTTP method.
Frappe's desk issues POSTs for list queries; using the method would classify
half a read-only workload as a write.

## Chrome detection by document frequency

Rather than a hand-written list of endpoints to ignore, `distill/endpoints.py`
finds them the way stop words are found: an endpoint present in ≥ 80% of
episodes carries no task signal. Two things follow — it leaves the signature,
and its responses stop being offered as provenance sources. Every episode
still keeps a terminal step, so a workload uniform enough that its only
endpoint counts as chrome does not collapse to nothing.

## Echo suppression

Matching a value to an earlier response is easy; matching it to the *right*
earlier response is not. Endpoints that reflect their input back — settings
saves, link validation — look like the origin of everything the user typed,
and "most recent source wins" then binds tools to them. A response leaf that
repeats its own request's input is therefore not a candidate source. This one
change removed most spurious bindings and shrank the induced tools
noticeably.

## Cross-episode stability

The decisive filter on provenance is not in `provenance.py` but in
`induce.py`: a `from_response` binding survives only if the same
`(step, json_path, transform)` explained the value in a majority of the
group's episodes. A coincidental match in one episode does not repeat at the
same path in the others, so it is demoted to a parameter rather than baked
into a tool.

## Fields that stay whole, and parameters that merge

A list field is kept as one value. Exploding `filters` into `filters_0_0`,
`filters_0_1` produced tools nobody could call. An object field is still
walked, because a saved document's fields really are separate parameters.

Two value sites with the same field name and the same observed values across
the group become one parameter — a list tool that repeats `doctype` for its
count call should not ask the caller for it twice.

## Required and optional

Presence rate decides which parameters are required. That distinction has
teeth at execution time: an unsupplied *required* parameter falls back to the
observed value so the request stays well-formed, and an unsupplied *optional*
one is omitted. Defaulting optionals is how a generalised tool ends up
sending one record type's fields while creating another.

## Enum induction and transfer

Low cardinality alone is not a closed domain: three customer names in three
episodes is an identifier that happened to be sampled three times. An enum is
emitted only when the value set saturates — the values must repeat. This is
also what stopped an unrelated tool from winning a goal because a customer
name from its capture appeared in the text.

The tension does not go away. A `doctype` parameter observed with three
record types becomes a three-valued enum, which is exactly the constraint
that tells a schema-obeying model it may not ask for a fourth. Any
closed-domain signal is also a transfer barrier.

## Backoff on sparse signatures

When a full request sequence is too rare to induce a tool, its episodes are
regrouped on their state-changing steps alone. Three different "find a record
and save it" flows differ in how the record was found, not in the save; any
value that differs across those cores becomes a parameter.

## The executor never replays credentials

The capture addon strips `Authorization`, `Cookie`, `Set-Cookie` and Frappe's
CSRF header into a separate store and never writes them into a record. The
executor logs in itself and reads a live CSRF token out of the desk boot
payload — which, it turns out, publishes it as an assignment rather than a
boot key, so both forms are matched. Replaying a recorded `sid` would work
for a few minutes and then fail confusingly; worse, it would make every
synthesized tool depend on the session of whoever was observed.

## Writes are verified against a reset instance

A write tool that returns HTTP 200 has not been verified. `verify/replay.py`
resets the database, snapshots the row counts of the record types the tool
can address, runs it, and requires an observable change — then resets again.
`--allow-writes` is never the default, in the verifier or the MCP server or
the agent.

## Retrieval: BM25, not embeddings

The catalog is a fixed tax on every prompt, paid whether or not a tool is
used, and that is what made the tool-first agent lose on the tasks no tool
covered. `route/agent.py` scores each eligible tool against the goal with
BM25 over its name, description, parameter names and enum values, sends the
top `bench.tool_k`, and sends *nothing* when the best score fails to clear
`bench.tool_score_floor`.

BM25 rather than embeddings, for three reasons. The corpus is a handful of
tools, where lexical overlap is not the weak signal it is at scale — a
synthesized tool's name and parameters are drawn from the same vocabulary as
the goal, because both describe the same records. It adds no dependency, no
model call and no index to keep in sync with the catalog. And it is legible:
`retrieve_tools()` reports the score it gave every tool, so a coverage
failure can be attributed to retrieval or to the caller rather than guessed
at. An embedding retriever would be the right answer for a catalog of
hundreds; at eight it would mostly add a second thing that can be wrong.

The mutation gate runs *before* retrieval, in `eligible_tools()`. A write
tool excluded by `allow_writes: false` must not consume one of the k slots
and then be filtered out afterwards — that would spend the budget on nothing.

## Two coverage numbers, and why both are reported

Achieved coverage is the fraction of agent actions on held-out tasks that a
synthesized tool served. It confounds catalog quality with router quality.
`bench/attainable.py` measures only the catalog: it is allowed to use the
oracle to build arguments, so it reports the ceiling a perfect router would
hit. Reporting one without the other would either undersell synthesis or
oversell the agent. A third number — the fraction of runs that finished on
tools alone — separates useful tool calls from ones that merely succeeded.
