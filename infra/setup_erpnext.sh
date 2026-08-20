#!/usr/bin/env bash
# Native ERPNext (frappe bench) setup.
#
# The containerised frappe_docker path is preferred (infra/pwd.yml); this
# script exists because container-registry blob CDNs are unreachable from
# some sandboxes. It produces the same thing: an ERPNext site on
# http://127.0.0.1:8000 with Administrator/admin.
#
# Must NOT run as root — bench refuses. Run:  sudo -u frappe bash infra/setup_erpnext.sh
set -euo pipefail

BENCH_ROOT="${BENCH_ROOT:-/home/frappe/frappe-bench}"
FRAPPE_BRANCH="${FRAPPE_BRANCH:-version-15}"
SITE="${SITE:-shadow.localhost}"
export PATH="/opt/benchtools/bin:/opt/node22/bin:$PATH"
# uv (used by bench for the app venv) needs the sandbox CA to reach PyPI.
export UV_NATIVE_TLS=1
export SSL_CERT_FILE="${SSL_CERT_FILE:-/opt/certs/ca-bundle.crt}"
export REQUESTS_CA_BUNDLE="$SSL_CERT_FILE"
export NODE_EXTRA_CA_CERTS="$SSL_CERT_FILE"

if [ "$(id -u)" -eq 0 ]; then
  echo "refusing to run as root; use: sudo -u frappe bash $0" >&2
  exit 2
fi

python3.11 -m venv /opt/benchtools 2>/dev/null || true
/opt/benchtools/bin/pip install -q --upgrade pip
/opt/benchtools/bin/pip install -q frappe-bench

cd "$(dirname "$BENCH_ROOT")"
if [ ! -d "$BENCH_ROOT" ]; then
  bench init --frappe-branch "$FRAPPE_BRANCH" --python python3.11 --verbose "$(basename "$BENCH_ROOT")"
fi
cd "$BENCH_ROOT"
bench set-config -g db_host 127.0.0.1

if [ ! -d "apps/erpnext" ]; then
  bench get-app --branch "$FRAPPE_BRANCH" erpnext
fi
if [ ! -d "sites/$SITE" ]; then
  bench new-site "$SITE" --db-root-username root --db-root-password admin \
    --admin-password admin --no-mariadb-socket --install-app erpnext
fi
# new-site does not always run ERPNext's after_install custom-field hook;
# without it several ERPNext queries reference columns that do not exist.
bench --site "$SITE" execute erpnext.setup.install.after_install || true
bench --site "$SITE" migrate
bench --site "$SITE" set-config developer_mode 0
bench --site "$SITE" clear-cache
echo "SETUP_OK"
