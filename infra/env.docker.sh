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

echo "erpbench env: site=$ERPBENCH_SITE url=$ERPBENCH_BASE_URL bench=$BENCH_ROOT"
