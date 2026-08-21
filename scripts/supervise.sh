#!/usr/bin/env bash
# Keep a long benchmark running across container restarts.
#
# The container is reclaimed every few hours. That kills ERPNext, and can
# leave its database half-restored, so a run does not simply resume -- the
# stack has to be brought back first. Three separate stretches of work were
# lost to this before it was automated.
#
#   scripts/supervise.sh <stage-script>
#
# The stage script is re-invoked after every recovery. It must be resumable:
# every stage here appends to its results file and skips completed rows.
set -uo pipefail
cd /home/user/costcutter
STAGE="${1:?usage: supervise.sh <stage-script>}"

ensure_up() {
  for attempt in 1 2 3; do
    if curl -fsS --noproxy '*' -m 8 \
        http://shadow.localhost:8000/api/method/ping >/dev/null 2>&1; then
      return 0
    fi
    echo "[supervise] ERPNext down (attempt $attempt); starting stack"
    bash infra/start_erpnext.sh >/tmp/supervise_start.log 2>&1
    # A restart can interrupt a reset and leave the site database empty, so
    # a successful port bind is not proof the site works.
    if ! curl -fsS --noproxy '*' -m 8 \
        http://shadow.localhost:8000/api/method/ping >/dev/null 2>&1; then
      echo "[supervise] site still failing; restoring from seed"
      .venv/bin/python -c 'from oracle.reset import reset; reset()' \
        >>/tmp/supervise_start.log 2>&1
    fi
  done
  curl -fsS --noproxy '*' -m 8 \
    http://shadow.localhost:8000/api/method/ping >/dev/null 2>&1
}

while true; do
  if ! ensure_up; then
    echo "[supervise] could not bring ERPNext up; retrying in 60s"
    sleep 60; continue
  fi
  # A lock left by a process the restart killed would block every stage.
  if [ -f /tmp/shadow-instance.lock ]; then
    holder=$(cut -d' ' -f1 /tmp/shadow-instance.lock)
    if ! kill -0 "$holder" 2>/dev/null; then
      echo "[supervise] clearing lock from dead pid $holder"
      rm -f /tmp/shadow-instance.lock
    fi
  fi
  echo "[supervise] running stage $STAGE at $(date -Is)"
  bash "$STAGE"
  rc=$?
  if [ $rc -eq 0 ]; then
    echo "[supervise] stage completed at $(date -Is)"
    exit 0
  fi
  echo "[supervise] stage exited $rc; recovering in 30s"
  sleep 30
done
