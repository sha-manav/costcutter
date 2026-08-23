#!/usr/bin/env bash
# Stand up ERPNext for the benchmark using frappe_docker, on a machine where
# Docker works. Idempotent: safe to re-run.
#
# This is the macOS/laptop path. infra/setup_erpnext.sh is the native Frappe
# bench installer, used where a container registry is unreachable; it assumes
# a Linux host with a `frappe` user and does not run here.
#
#   bash infra/setup_docker.sh
#   source infra/env.docker.sh
#   .venv/bin/python scripts/build_firm_seeds_docker.py
#
# Prerequisites this script does NOT install, because both need a password or
# a GUI and would fail unattended:
#   Docker Desktop   brew install --cask docker && open -a Docker
#   MariaDB + redis clients   brew install mariadb redis
set -euo pipefail

FRAPPE_DOCKER="${FRAPPE_DOCKER:-/tmp/frappe_docker}"
# Pinned. The adapter, seeds and assertions were built against ERPNext v15;
# pwd.yml now ships v16 and a silent major-version bump under a fixed set of
# assertions makes the benchmark measure the environment.
ERPNEXT_VERSION="${ERPNEXT_VERSION:-v15.99.1}"
SITE="${ERPBENCH_SITE:-frontend}"
BENCH_MIRROR="${BENCH_ROOT:-$HOME/.erpbench/bench}"
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

command -v docker >/dev/null || {
  echo "docker not found. brew install --cask docker && open -a Docker" >&2
  exit 2
}
docker info >/dev/null 2>&1 || { echo "docker daemon is not running" >&2; exit 2; }

if [ ! -d "$FRAPPE_DOCKER" ]; then
  git clone -q --depth 1 https://github.com/frappe/frappe_docker.git "$FRAPPE_DOCKER"
fi

# Pin every erpnext image reference in the compose file.
sed -i.bak -E "s|frappe/erpnext:v[0-9]+\.[0-9]+\.[0-9]+|frappe/erpnext:${ERPNEXT_VERSION}|g" \
  "$FRAPPE_DOCKER/pwd.yml"

echo "bringing up ERPNext ${ERPNEXT_VERSION} (first run pulls ~2GB)..."
docker compose -f "$FRAPPE_DOCKER/pwd.yml" -f "$REPO/infra/pwd.override.yml" up -d

echo "waiting for the site to answer..."
for _ in $(seq 1 60); do
  if curl -sf -o /dev/null --max-time 5 -H "Host: $SITE" \
       "http://localhost:8080/api/method/ping"; then
    echo "  site is up"; break
  fi
  sleep 5
done

# ERPNext installs its schema on `bench install-app` but creates master data
# -- Company, UOM, Territory, Item Group, Customer Group, Price List -- only
# when the setup wizard completes. Without it the site has 766 doctypes and
# no records, and seeding fails on the first Customer because the customer
# group it references does not exist.
echo "running the ERPNext setup wizard (idempotent)..."
docker compose -f "$FRAPPE_DOCKER/pwd.yml" exec -T backend bash -c "
cd /home/frappe/frappe-bench && bench --site $SITE console <<'EOF'
import frappe
from frappe.desk.page.setup_wizard.setup_wizard import setup_complete
if frappe.db.exists('Company', {'company_name': 'Benchmark Trading'}):
    print('setup wizard: already complete')
else:
    setup_complete({
        'language': 'English (United States)', 'country': 'United States',
        'timezone': 'America/New_York', 'currency': 'USD',
        'full_name': 'Administrator', 'email': 'admin@example.com',
        'company_name': 'Benchmark Trading', 'company_abbr': 'BT',
        'chart_of_accounts': 'Standard with Numbers',
        'fy_start_date': '2026-01-01', 'fy_end_date': '2026-12-31',
        'setup_demo': 0,
    })
    frappe.db.commit()
    print('setup wizard: complete')
EOF"

# oracle/reset.py reads the site's DB credentials from a bench layout on the
# host; under frappe_docker the bench is inside the container.
echo "mirroring site_config.json to $BENCH_MIRROR"
mkdir -p "$BENCH_MIRROR/sites/$SITE"
docker compose -f "$FRAPPE_DOCKER/pwd.yml" exec -T backend \
  cat "/home/frappe/frappe-bench/sites/$SITE/site_config.json" \
  > "$BENCH_MIRROR/sites/$SITE/site_config.json"
chmod 600 "$BENCH_MIRROR/sites/$SITE/site_config.json"

echo
echo "ERPNext is up. Next:"
echo "  source infra/env.docker.sh"
echo "  .venv/bin/python scripts/build_firm_seeds_docker.py"
echo "  .venv/bin/python -m erpbench.preflight --check-adapter"
