#!/usr/bin/env bash
# Create the two virtualenvs the project needs. Neither is committed.
#
#   .venv          everything except mitmproxy
#   .venv-capture  mitmproxy only
#
# They are separate because mitmproxy pins a dependency set that resolves
# against litellm/fastmcp by downgrading mitmproxy to a decade-old release.
# The capture addon runs under the capture interpreter; nothing else does.
set -euo pipefail
cd "$(dirname "$0")/.."

PYTHON="${PYTHON:-python3.11}"

"$PYTHON" -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q "pydantic>=2" httpx pyyaml pytest matplotlib \
    playwright fastmcp litellm

"$PYTHON" -m venv .venv-capture
.venv-capture/bin/pip install -q --upgrade pip
.venv-capture/bin/pip install -q "mitmproxy>=11" "pydantic>=2" pyyaml

echo "main:    $(.venv/bin/python -V)"
echo "capture: $(.venv-capture/bin/mitmdump --version | head -1)"
