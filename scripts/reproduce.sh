#!/usr/bin/env bash
# One command, from a clean clone, to a verified environment.
#
# Stages are ordered so the cheapest checks fail first: a syntax error or a
# missing dependency should not cost you an ERPNext build. Nothing here calls
# a paid API -- running the benchmark is a separate, explicit step, printed at
# the end.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

step() { printf '\n\033[1m== %s\033[0m\n' "$1"; }

step "1/5  Python environment"
python3.11 -m venv .venv 2>/dev/null || python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt
echo "    $(.venv/bin/python --version)"

step "2/5  Test suite (no Docker, no network, no keys)"
.venv/bin/python -m pytest -q

step "3/5  Frozen artifacts still match the code"
.venv/bin/python - <<'PY'
import json, pathlib
import erpbench.evaluation           # noqa: F401  populates the registry
import erpbench.evaluation_extra     # noqa: F401
from erpbench.splits import fingerprint
frozen = json.loads(pathlib.Path("artifacts/splits_frozen.json").read_text())
here = fingerprint()
assert here == frozen["fingerprint"], (
    f"split fingerprint {here} != frozen {frozen['fingerprint']}")
print(f"    split fingerprint {here} matches")
cal = json.loads(pathlib.Path("artifacts/calibration_gate_decision.json").read_text())
print(f"    week-1 fallback order: {cal['fallback_order']}")
PY

step "4/5  ERPNext (Docker required; ~10 min on first run)"
if docker info >/dev/null 2>&1; then
  bash infra/setup_docker.sh
  # shellcheck disable=SC1091
  source infra/env.docker.sh
  .venv/bin/python scripts/build_firm_seeds_docker.py
  .venv/bin/python scripts/provision_docker_sites.py --start 1 --count 6
  .venv/bin/python -m erpbench.preflight --check-adapter
else
  echo "    Docker is not available; skipping. Steps 1-3 and 5 still verify"
  echo "    the code, the frozen splits and the recorded results."
fi

step "5/5  Regenerate every figure from the committed rows"
for f in harness masking adaptation pareto behaviour intelligence; do
  .venv/bin/python "scripts/figure_${f}.py" >/dev/null && echo "    fig ${f}"
done
.venv/bin/python scripts/build_demo.py >/dev/null && echo "    demo"

cat <<'DONE'

Verified. The figures in artifacts/charts/ were just rebuilt from the row
files in this repository -- if they render, the published numbers are
reproducible from committed data without spending anything.

To run the benchmark itself (this costs money and needs a key):

  export OPENROUTER_API_KEY=...
  bash scripts/run_gate_pool.sh 6 --split evaluation \
      --models openrouter/qwen/qwen3-14b --require-model --trials 3
DONE
