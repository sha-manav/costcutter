"""Turn verified teacher traces into T1/T2/T3 SFT datasets — SPEC §6.

Each trajectory becomes one multi-turn conversation in the shape the harness
actually presents: system prompt (preamble + corrected schema), the
instruction, then the alternating action/observation exchange the model
produced. Training on anything else would teach a format the harness never
shows.

**Replay.** Each stage carries ~20% of the prior stages' data. SPEC §6 calls
it the forgetting guard: T3 trains on a policy-concentrated mixture so the
policy signal is not drowned out, and without replay that concentration is
exactly what makes it forget T1's recovery behaviour.

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

from erpbench.gate import SYSTEM_PREAMBLE                      # noqa: E402
from erpbench.harness import CORRECTED_SCHEMA                  # noqa: E402

REPLAY_FRACTION = 0.20
STAGES = ("T1", "T2", "T3")


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
        by_stage.setdefault(r.get("stage", "T2"), []).append(r)

    rng = random.Random(args.seed)
    args.out.mkdir(parents=True, exist_ok=True)
    prior: list[dict] = []
    summary = {}
    for stage in STAGES:
        own = by_stage.get(stage, [])
        n_replay = round(len(prior) * REPLAY_FRACTION)
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
        {"replay_fraction": REPLAY_FRACTION, "stages": summary,
         "source": str(args.traces.relative_to(REPO)),
         "verified_traces": len(kept)}, indent=2) + "\n")
    print(f"\n{len(kept)} verified traces -> {sum(v['written'] for v in summary.values())} "
          f"training conversations across {len(STAGES)} stages")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
