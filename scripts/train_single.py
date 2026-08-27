"""Single-stage fine-tune from the base model. No curriculum, no chaining."""
from __future__ import annotations
import json, os, sys, time
from pathlib import Path
REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
STATE = REPO / "artifacts" / "single_stage.json"

def main() -> int:
    if not os.environ.get("TOGETHER_API_KEY"):
        print("TOGETHER_API_KEY is not set", file=sys.stderr); return 1
    from together import Together
    client = Together()
    path = REPO / "artifacts" / "sft" / "single.jsonl"
    up = client.files.upload(file=path, check=True)
    print(f"uploaded {path.name} -> {up.id}", flush=True)
    job = client.fine_tuning.create(
        training_file=up.id, model="Qwen/Qwen3-14B", n_epochs=3,
        lora=True, suffix="erpbench-single")
    print(f"job {job.id} from Qwen/Qwen3-14B", flush=True)
    t0 = time.time()
    while True:
        j = client.fine_tuning.retrieve(job.id)
        st = str(j.status).split(".")[-1].lower()
        print(f"  [single] {st}  ({time.time()-t0:.0f}s)", flush=True)
        if st in ("completed", "error", "cancelled"):
            break
        if time.time() - t0 > 10800:
            print("stage timeout", file=sys.stderr); return 1
        time.sleep(30)
    if st != "completed":
        print(f"training ended {st}", file=sys.stderr); return 1
    # output_name is empty on the SDK object; the REST record carries it.
    # run_curriculum.py already knew this and this script did not reuse it.
    import requests
    rest = requests.get(f"https://api.together.xyz/v1/fine-tunes/{job.id}",
                        headers={"Authorization":
                                 f"Bearer {os.environ['TOGETHER_API_KEY']}"},
                        timeout=60).json()
    name = rest.get("model_output_name", "")
    STATE.write_text(json.dumps(
        {"job": job.id, "model": name,
         "examples": sum(1 for _ in path.open())}, indent=1))
    print(f"[single] done -> {name}", flush=True)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
