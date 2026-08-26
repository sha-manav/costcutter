# Environment for running the benchmark against a containerised ERPNext.
#
#   source infra/env.docker.sh
#
# Contains no secrets and never should. The OpenRouter key is exported by
# hand in the shell that runs the gate (SPEC §10.13: keys never in a file,
# config, log, or commit).
#
# The stack this matches is frappe_docker's pwd.yml pinned to ERPNext v15
# plus infra/pwd.override.yml, which publishes the MariaDB and redis ports
# that oracle/reset.py needs from the host.

# ERPNext v15, not v16. The adapter, the seeds and the assertions were all
# built against 15; frappe_docker's pwd.yml now ships v16 by default and
# silently drifting a major version under a fixed set of assertions is how a
# benchmark starts measuring the environment instead of the agent.
export ERPBENCH_BASE_URL="${ERPBENCH_BASE_URL:-http://localhost:8080}"
export ERPBENCH_SITE="${ERPBENCH_SITE:-frontend}"
export SHADOW_SITE="${SHADOW_SITE:-$ERPBENCH_SITE}"

# oracle/reset.py reads the site's database credentials from a bench layout
# on the host. Under frappe_docker the real bench is inside the containers,
# so this is a one-file mirror of it, populated by infra/setup_docker.sh.
export BENCH_ROOT="${BENCH_ROOT:-$HOME/.erpbench/bench}"

# Homebrew keeps the MariaDB client off the default PATH.
if [ -d /opt/homebrew/opt/mariadb/bin ]; then
  export PATH="/opt/homebrew/opt/mariadb/bin:$PATH"
fi

# A site is owned by exactly one worker. `erp01..erpNN` belong to the pool
# driver whenever it is running; touching one from another shell resets a
# database out from under a rollout mid-snapshot, which is the failure the
# process-per-site design exists to prevent. It has happened once, by hand,
# during a throughput measurement.
#
# For ad-hoc work while a pool run is in flight, use the scratch site:
export ERPBENCH_SCRATCH_SITE="${ERPBENCH_SCRATCH_SITE:-frontend}"

echo "erpbench env: site=$ERPBENCH_SITE url=$ERPBENCH_BASE_URL bench=$BENCH_ROOT"
if pgrep -f "erpbench.gate --site erp" >/dev/null 2>&1; then
  echo "  WARNING: a pool run is in flight. Do not touch erp01..erpNN;"
  echo "           use ERPBENCH_SCRATCH_SITE=$ERPBENCH_SCRATCH_SITE for ad-hoc work."
fi
