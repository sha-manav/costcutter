"""T1 -> T2 -> T3 sequential SFT on Together — SPEC §6.

One adapter, trained forward through the curriculum: each stage starts from
the previous stage's output rather than from the base. SPEC §6 is explicit --
"continue training one adapter; do not stack" -- because three independently
trained adapters cannot be composed and the scaling curve would measure three
unrelated models.

Stages are sequential by necessity, so this polls. It records every job id
and checkpoint name as it goes, so a run interrupted at any point can be
resumed from the manifest rather than restarted.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
MANIFEST = REPO / "artifacts" / "curriculum.json"
STAGES = ("T1", "T2", "T3")
TERMINAL_OK = {"completed"}
TERMINAL_BAD = {"failed", "cancelled", "error"}


def load() -> dict:
    return json.loads(MANIFEST.read_text()) if MANIFEST.exists() else {"stages": {}}


def save(state: dict) -> None:
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")


def wait_for(client, job_id: str, label: str, timeout_s: float) -> str:
    """Poll until terminal. Returns the fine-tuned model name."""
    started = time.time()
    last = None
    while True:
        job = client.fine_tuning.retrieve(job_id)
        status = str(getattr(job, "status", "")).lower().replace("finetunejobstatus.", "")
        if status != last:
            print(f"  [{label}] {status}  ({time.time()-started:.0f}s)", flush=True)
            last = status
        if any(s in status for s in TERMINAL_OK):
            return getattr(job, "output_name", "") or ""
        if any(s in status for s in TERMINAL_BAD):
            raise SystemExit(f"[{label}] job {job_id} ended {status}")
        if time.time() - started > timeout_s:
            raise SystemExit(f"[{label}] job {job_id} exceeded {timeout_s:.0f}s; "
                             "not cancelled — check the console")
        time.sleep(30)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base", default="Qwen/Qwen3-14B")
    ap.add_argument("--epochs", type=int, default=3)
    ap.add_argument("--stage-timeout", type=float, default=10800,
                    help="3h per stage, per the time-box")
    args = ap.parse_args()

    if not os.environ.get("TOGETHER_API_KEY"):
        print("TOGETHER_API_KEY is not set", file=sys.stderr)
        return 1
    from together import Together

    client = Together()
    state = load()
    previous = args.base

    for stage in STAGES:
        rec = state["stages"].get(stage, {})
        if rec.get("model"):
            print(f"[{stage}] already trained -> {rec['model']}", flush=True)
            previous = rec["model"]
            continue

        if not rec.get("file"):
            path = REPO / "artifacts" / "sft" / f"{stage.lower()}.jsonl"
            up = client.files.upload(file=str(path), check=True)
            rec["file"] = up.id
            print(f"[{stage}] uploaded {path.name} -> {up.id}", flush=True)

        if not rec.get("job"):
            # Continued training uses `from_checkpoint`, not `model`. Passing a
            # fine-tuned name as `model` fails: the SDK looks it up in the base
            # model catalogue, where a checkpoint does not appear. `model` stays
            # the original base throughout, and the checkpoint carries the
            # accumulated adapter forward -- which is what SPEC §6 means by
            # continuing one adapter rather than stacking three.
            kwargs = dict(training_file=rec["file"], n_epochs=args.epochs,
                          lora=True, suffix=f"erpbench-{stage.lower()}")
            # Exactly one of model / from_checkpoint, never both.
            if previous == args.base:
                kwargs["model"] = args.base
            else:
                kwargs["from_checkpoint"] = previous
            job = client.fine_tuning.create(**kwargs)
            rec["job"] = job.id
            rec["from"] = previous
            print(f"[{stage}] job {job.id} from {previous}", flush=True)
        state["stages"][stage] = rec
        save(state)

        wait_for(client, rec["job"], stage, args.stage_timeout)
        # output_name is empty on the SDK object; the REST record carries it.
        import httpx as _httpx
        _r = _httpx.get(f"https://api.together.xyz/v1/fine-tunes/{rec['job']}",
                        headers={"Authorization":
                                 f"Bearer {os.environ['TOGETHER_API_KEY']}"},
                        timeout=60)
        rec["model"] = _r.json().get("model_output_name", "")
        state["stages"][stage] = rec
        save(state)
        print(f"[{stage}] done -> {rec['model']}", flush=True)
        previous = rec["model"]

    print("\ncurriculum complete")
    for s in STAGES:
        print(f"  {s}: {state['stages'][s].get('model')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
