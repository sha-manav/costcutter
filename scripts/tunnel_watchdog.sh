#!/usr/bin/env bash
# Keep the GPU tunnel up.
#
# Twelve shards multiplexed through one ssh -L dropped it mid-run: vLLM stayed
# healthy, every shard lost its connection at once, and the streak guard
# halted all twelve. The connection is the weakest link in this setup and it
# fails silently from the harness's point of view -- a dead tunnel and a dead
# model look identical.
set -u
PORT_LOCAL="${1:-8001}"; PORT_REMOTE="${2:-8000}"
while true; do
  if ! curl -s --max-time 6 "http://localhost:${PORT_LOCAL}/v1/models" >/dev/null 2>&1; then
    pkill -f "ssh.*${PORT_LOCAL}:localhost:${PORT_REMOTE}" 2>/dev/null
    ssh -f -N -p 40281 -o StrictHostKeyChecking=no \
        -o ServerAliveInterval=15 -o ServerAliveCountMax=3 \
        -o ExitOnForwardFailure=yes \
        -L "${PORT_LOCAL}:localhost:${PORT_REMOTE}" root@107.206.71.138 2>/dev/null
    echo "$(date +%H:%M:%S) tunnel restarted"
  fi
  sleep 10
done
