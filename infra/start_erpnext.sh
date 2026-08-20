#!/usr/bin/env bash
# Bring up the ERPNext site the benchmark runs against. Idempotent.
set -euo pipefail
BENCH_ROOT="${BENCH_ROOT:-/home/frappe/frappe-bench}"
SITE="${SITE:-shadow.localhost}"
PORT="${PORT:-8000}"
BENCH=/opt/benchtools/bin/bench

pgrep -f mariadbd >/dev/null || (/usr/sbin/mariadbd --user=mysql >/var/log/mariadbd.log 2>&1 &)
for port in 11000 13000; do
  redis-cli -p "$port" ping >/dev/null 2>&1 || redis-server --port "$port" --daemonize yes
done
grep -q "$SITE" /etc/hosts || echo "127.0.0.1 $SITE" >> /etc/hosts

start() {  # start() <pgrep-pattern> <logfile> <args...>
  local pattern="$1" log="$2"; shift 2
  pgrep -f "$pattern" >/dev/null && return 0
  ( cd "$BENCH_ROOT" && sudo -u frappe -H nohup "$BENCH" "$@" > "$log" 2>&1 & )
}
start "bench serve"    /tmp/bench_web.log    serve --port "$PORT"
start "bench worker"   /tmp/bench_worker.log worker --queue short,default,long
start "bench schedule" /tmp/bench_sched.log  schedule

for _ in $(seq 1 60); do
  if curl -fsS --noproxy '*' -m 3 "http://$SITE:$PORT/api/method/ping" >/dev/null 2>&1; then
    echo "ERPNext up on http://$SITE:$PORT"
    exit 0
  fi
  sleep 2
done
echo "ERPNext did not come up; see /tmp/bench_web.log" >&2
exit 1
