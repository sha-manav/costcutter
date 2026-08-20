#!/usr/bin/env bash
# End-to-end: clean ERPNext -> demonstrations -> tools -> benchmark -> report.
#
#   bash scripts/run_all.sh [SESSIONS] [TRIALS]
#
# Assumes ERPNext is installed (infra/setup_erpnext.sh). Everything after
# that is unattended.
set -euo pipefail
cd "$(dirname "$0")/.."

SESSIONS="${1:-4}"
TRIALS="${2:-3}"
PY="${PY:-.venv/bin/python}"

echo "== bringing up ERPNext =="
bash infra/start_erpnext.sh

echo "== seeding deterministic data and snapshotting =="
$PY -m shadow.cli seed

echo "== observing $SESSIONS demonstration sessions (OBSERVE templates only) =="
$PY -m shadow.cli observe --sessions "$SESSIONS" --fresh

echo "== synthesizing tools =="
$PY -m shadow.cli distill --show-dataflow

echo "== verifying tools against the live instance =="
$PY -m shadow.cli verify --allow-writes

echo "== benchmarking conditions A and B on EVAL ($TRIALS trials) =="
$PY -m shadow.cli bench --trials "$TRIALS" --allow-writes --fresh

echo "== attainable coverage (router-independent ceiling) =="
$PY -m shadow.bench.attainable --allow-writes

echo "== coverage vs observation volume =="
$PY -m shadow.bench.sweep --verify --bench

echo "== report =="
$PY -m shadow.cli report
