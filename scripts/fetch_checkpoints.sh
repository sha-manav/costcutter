#!/usr/bin/env bash
# Pull every trained checkpoint from Together.
#
# Together fine-tunes Qwen3-14B and will not serve it, so the weights have to
# come local to be evaluated at all. ~8.6GB per stage. Gitignored; the job ids
# in artifacts/curriculum.json are the durable record.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
: "${TOGETHER_API_KEY:?export TOGETHER_API_KEY first}"
mkdir -p artifacts/checkpoints
.venv/bin/python - <<'PY' | while read -r stage job model; do
import json, pathlib
d = json.loads(pathlib.Path("artifacts/curriculum.json").read_text())
for s, r in sorted(d["stages"].items()):
    if r.get("job") and r.get("model"):
        print(s, r["job"], r["model"])
PY
  out="artifacts/checkpoints/${stage,,}.tar.zst"
  if [ -s "$out" ]; then echo "  $stage already present"; continue; fi
  echo "  fetching $stage ($model)"
  curl -sL -o "$out" --max-time 1800 -H "Authorization: Bearer $TOGETHER_API_KEY" \
    "https://api.together.xyz/v1/finetune/download?ft_id=${job}&model_name=${model}&checkpoint_type=adapter"
  echo "    $(du -h "$out" | cut -f1)"
done
