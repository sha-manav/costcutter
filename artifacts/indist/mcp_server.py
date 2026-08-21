"""Auto-generated MCP server — do not edit.

Synthesized by Shadow from observed HTTP traffic.
Tools: 8 (8 verified).

    python mcp_server.py [--allow-writes]
"""
import sys
from pathlib import Path

sys.path.insert(0, '/home/user/costcutter')

from shadow.serve.server import build_server

CATALOG_PATH = '/home/user/costcutter/artifacts/indist/tools.json'

if __name__ == "__main__":
    allow_writes = "--allow-writes" in sys.argv
    build_server(CATALOG_PATH, allow_writes=allow_writes).run()
