"""Turn verified teacher traces into T1/T2/T3 SFT datasets — SPEC §6.

Each trajectory becomes one multi-turn conversation in the shape the harness
actually presents: system prompt (preamble + corrected schema), the
instruction, then the alternating action/observation exchange the model
produced. Training on anything else would teach a format the harness never
shows.

**Replay.** Each stage carries prior-stage data as a forgetting guard: T3
trains on a policy-concentrated mixture, and without replay that
concentration is exactly what makes it forget how to execute.

The fraction is now stated as a share **of the resulting batch**, which is the
natural reading of "20% replay". It was previously 20% *of the prior pool* --
`round(len(prior) * 0.20)` -- which for T2 meant 23 examples in a batch of
152, or 15%. Defensible, but not what a reader checks for, and at that ratio
replay was never going to outweigh 84 counter-examples.

**Stage assignment is recomputed here from the parameter draw.** Rows carry a
`stage` field written when they were generated, and for the Round 0 rows that
field came from the defective rule that read the trajectory's terminal state.
Recomputing means the corpus does not have to be regenerated to be corrected.

**T3 is balanced.** Policy means choosing between complying and declining, so
a T3 assembled from whichever side the draws happened to favour teaches a
reflex rather than a decision. The larger side is downsampled toward parity.

**Only accepted trajectories.** Rejection sampling already removed anything
that reached its goal by violating policy. Admitting those here would teach
the behaviour T3 exists to remove.
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from erpbench import teacher                                  # noqa: E402
from erpbench.gate import SYSTEM_PREAMBLE                      # noqa: E402
from erpbench.harness import CORRECTED_SCHEMA                  # noqa: E402

# Share of the final batch, not of the prior pool. See the module docstring.
REPLAY_FRACTION = {"T2": 0.25, "T3": 0.40}
# T3 inherits a model that must still be able to act; its own data is
# policy-concentrated, so the guard is deliberately heavier there.
STAGES = ("T1", "T2", "T3")
WRITE_ACTIONS = {"create", "update", "submit", "save", "grid", "field",
                 "link", "select_field", "cancel", "delete"}


def _has_write(row: dict) -> bool:
    for a in row.get("actions") or []:
        v = a.get("action")
        v = v.get("action") if isinstance(v, dict) else v
        if v in WRITE_ACTIONS:
            return True
    return False


def _balanced(rows: list[dict], rng: random.Random) -> list[dict]:
    """Downsample the larger of {completes a write, does not} toward parity."""
    wrote = [r for r in rows if _has_write(r)]
    other = [r for r in rows if not _has_write(r)]
    n = min(len(wrote), len(other))
    if n == 0:
        return rows
    return rng.sample(wrote, n) + rng.sample(other, n)


def to_conversation(row: dict) -> dict | None:
    """Rebuild the exchange as the harness presented it.

    The observation text is not stored per step, so the assistant turns are
    reconstructed from the recorded actions and the user turns carry the
    typed outcome. That is faithful to what the model was trained to emit --
    one JSON object per turn -- which is the part that matters.
    """
    actions = row.get("actions") or []
    if not actions:
        return None
    messages = [{"role": "system", "content": SYSTEM_PREAMBLE + CORRECTED_SCHEMA},
                {"role": "user", "content": row["instruction"]}]
    for a in actions:
        act = a.get("action") or {}
        if not act.get("action"):
            continue
        messages.append({"role": "assistant", "content": json.dumps(act)})
        outcome = a.get("outcome", "success")
        detail = (a.get("detail") or "").strip()
        messages.append({"role": "user",
                         "content": detail if detail else f"[{outcome}]"})
    if len(messages) < 4:
        return None
    # The final turn is the model's, not the environment's.
    if messages[-1]["role"] == "user":
        messages.pop()
    return {"messages": messages}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--traces", type=Path,
                    default=REPO / "artifacts" / "teacher_traces.jsonl")
    ap.add_argument("--out", type=Path, default=REPO / "artifacts" / "sft")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    rows = [json.loads(l) for l in args.traces.read_text().splitlines() if l.strip()]
    kept = [r for r in rows if r.get("training")]
    by_stage: dict[str, list[dict]] = {s: [] for s in STAGES}
    for r in kept:
        # From the draw, not from the `stage` the row was written with: the
        # Round 0 rows carry a stage assigned by the defective rule.
        stage = (teacher.stage_for_params(r) if r.get("axes")
                 else r.get("stage", "T2"))
        by_stage.setdefault(stage, []).append(r)

    rng = random.Random(args.seed)
    args.out.mkdir(parents=True, exist_ok=True)
    prior: list[dict] = []
    summary = {}
    for stage in STAGES:
        own = by_stage.get(stage, [])
        if stage == "T3":
            own = _balanced(own, rng)
        # A share of the final batch: own / (1 - f) * f, capped by what exists.
        f = REPLAY_FRACTION.get(stage, 0.0)
        n_replay = round(len(own) * f / (1 - f)) if f else 0
        replay = rng.sample(prior, min(n_replay, len(prior))) if prior else []
        batch = own + replay
        rng.shuffle(batch)
        convs = [c for c in (to_conversation(r) for r in batch) if c]
        dest = args.out / f"{stage.lower()}.jsonl"
        dest.write_text("\n".join(json.dumps(c) for c in convs) + "\n")
        summary[stage] = {"own": len(own), "replay": len(replay),
                          "written": len(convs),
                          "turns_median": sorted(len(c["messages"]) for c in convs)[len(convs)//2] if convs else 0}
        print(f"  {stage}: {len(own):3} own + {len(replay):3} replay "
              f"-> {len(convs):3} conversations ({dest.name})")
        prior += own

    (args.out / "manifest.json").write_text(json.dumps(
        {"replay_fraction": REPLAY_FRACTION,
         "replay_is_share_of": "final batch",
         "stages": summary,
         "source": str(args.traces.relative_to(REPO)),
         "verified_traces": len(kept)}, indent=2) + "\n")
    print(f"\n{len(kept)} verified traces -> {sum(v['written'] for v in summary.values())} "
          f"training conversations across {len(STAGES)} stages")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
