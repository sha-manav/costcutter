# harness-v1 — the frozen harness for the model ladder

Every run in the model ladder uses this exact tree.

    commit 1dc905b3d3cfc9e38476ffe68e6e1156876de102
    tag    harness-v1 (local; the remote rejects tag refs with HTTP 403,
           so this file is the durable record)

The only variable across ladder runs is `models.agent`. `ACTION_SCHEMA`, the
composite actions, the save handler, retrieval and every prompt string are
fixed at this commit. Tuning the harness per model would make the models
incomparable, which is the one thing the ladder cannot survive.

## What is true at this commit

- both conditions score 100% on all six held-out templates
- prompt caching is engaged and verified against the provider in both
  conditions: 54/54 runs report non-zero cache reads
- the composite actions (`field`, `link`, `select_field`, `grid`, `save`)
  are documented in `ACTION_SCHEMA`, so the model can name them
- `save` reports the real outcome instead of returning unconditionally
- a blocking modal is dismissed before any action that touches the page
- the browser fallback is capped at one invocation per task

## Verifying a tree matches

    git rev-parse HEAD          # must equal the commit above
    git diff harness-v1 --stat  # must be empty for harness paths

The harness paths that must not move during a ladder:
`shadow/route/actions.py`, `shadow/route/agent.py`,
`shadow/route/browser_agent.py`, `shadow/bench/tasks.py`,
`shadow/bench/indist.py`, `oracle/checks.py`.
