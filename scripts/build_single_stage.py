"""Single-stage SFT set — no curriculum, aimed at the refusal prior.

Three chained fine-tunes produced two collapses, both at the stage boundary,
and the corrected-composition retrain showed the same collapse at T3 on data
that was 65% write-containing. Whatever that is, a single stage cannot have
it: there is no chain depth and no final stage to concentrate policy in.

**Mixture.** The target is ~70% write-completion / 20% recovery / 10% policy.
The corpus cannot hit that exactly, and the reason is structural rather than
an accident of sampling: a hard-recovery trace that survives rejection has,
by definition, failed an action and then succeeded on the same subgoal, so it
*ends in a completed write*. Recovery traces are a subset of write-completing
traces, not a disjoint category. Of 380 accepted write-completing traces, 265
carry a recovery event and only 115 are clean execution.

So the split that is actually controlled here is the one that matters for the
thing being fought: **90% of examples end in a completed write, 10% end in a
refusal.** Within the 90%, the clean/recovery ratio falls where the corpus
puts it, and is reported rather than forced. Forcing it would mean discarding
most of the recovery traces to hit a ratio whose purpose the recovery traces
already serve.
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

from erpbench.teacher import (                                 # noqa: E402
    accept, accept_write_completion, is_hard_recovery, terminal_action)

sys.path.insert(0, str(REPO / "scripts"))
from build_sft_datasets import to_conversation                 # noqa: E402

POLICY_SHARE = 0.10


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--traces", type=Path,
                    default=REPO / "artifacts" / "teacher_traces.jsonl")
    ap.add_argument("--out", type=Path,
                    default=REPO / "artifacts" / "sft" / "single.jsonl")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--scale", type=float, default=1.0,
                    help="multiply the corpus size; attempt 2's dose-response "
                         "test doubles it")
    args = ap.parse_args()

    rows = [json.loads(l) for l in
            args.traces.read_text().splitlines() if l.strip()]
    accepted = [r for r in rows if accept(r)[0]]

    writes = [r for r in accepted if accept_write_completion(r)[0]]
    policy = [r for r in accepted
              if terminal_action(r) in ("escalate", "abstain")]

    rng = random.Random(args.seed)
    rng.shuffle(writes)
    rng.shuffle(policy)

    keep_writes = writes[:max(1, int(len(writes) * args.scale))]
    n_policy = min(len(policy),
                   round(len(keep_writes) * POLICY_SHARE / (1 - POLICY_SHARE)))
    batch = keep_writes + policy[:n_policy]
    rng.shuffle(batch)

    convs = [c for c in (to_conversation(r) for r in batch) if c]
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("\n".join(json.dumps(c) for c in convs) + "\n")

    recovery = sum(1 for r in keep_writes if is_hard_recovery(r))
    summary = {
        "total": len(convs),
        "ends_in_completed_write": len(keep_writes),
        "ends_in_refusal": n_policy,
        "of_the_writes_recovery": recovery,
        "of_the_writes_clean": len(keep_writes) - recovery,
        "target_was": "70/20/10 write/recovery/policy",
        "actual": "90/10 write-ending/refusal; recovery is a subset of the "
                  "writes, not a disjoint third",
    }
    (args.out.parent / "single_manifest.json").write_text(
        json.dumps(summary, indent=1) + "\n")
    for k, v in summary.items():
        print(f"  {k:26} {v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
