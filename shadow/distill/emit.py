"""Emit a synthesized tool catalog as JSON and as a runnable MCP server."""
from __future__ import annotations

import json
from pathlib import Path

from shadow.capture.schema import ToolCatalog, write_catalog

SERVER_TEMPLATE = '''"""Auto-generated MCP server — do not edit.

Synthesized by Shadow from observed HTTP traffic.
Tools: {n_tools} ({n_verified} verified).

    python {rel_path} [--allow-writes]
"""
import sys
from pathlib import Path

sys.path.insert(0, {repo_root!r})

from shadow.serve.server import build_server

CATALOG_PATH = {catalog_path!r}

if __name__ == "__main__":
    allow_writes = "--allow-writes" in sys.argv
    build_server(CATALOG_PATH, allow_writes=allow_writes).run()
'''


def emit(catalog: ToolCatalog, tools_path: str | Path,
         server_path: str | Path, repo_root: str | Path) -> None:
    write_catalog(tools_path, catalog)
    server_path = Path(server_path)
    server_path.parent.mkdir(parents=True, exist_ok=True)
    server_path.write_text(SERVER_TEMPLATE.format(
        n_tools=len(catalog.tools),
        n_verified=sum(1 for t in catalog.tools if t.verified),
        rel_path=server_path.name,
        repo_root=str(repo_root),
        catalog_path=str(tools_path),
    ))


def render_catalog(catalog: ToolCatalog) -> str:
    """Compact human-readable listing (used by the CLI and by FINDINGS)."""
    lines = [f"{len(catalog.tools)} tools from {catalog.generated_from_episodes} episodes"]
    for t in catalog.tools:
        params = ", ".join(t.params_schema.get("properties", {}))
        flag = "verified" if t.verified else "unverified"
        lines.append(
            f"  {t.name:34s} support={t.support:<3d} {t.mutation_class:11s} "
            f"{flag:10s} steps={len(t.steps)}  params=({params})")
    return "\n".join(lines)


def catalog_to_openapi_like(catalog: ToolCatalog) -> str:
    """Debug view of the dataflow inside each tool."""
    out = []
    for t in catalog.tools:
        out.append(f"### {t.name}  [{t.mutation_class}] support={t.support}")
        out.append(f"    {t.description}")
        for i, step in enumerate(t.steps):
            out.append(f"    [{i}] {step.method} {step.path_template}")
            for group, label in ((step.path_bindings, "path"),
                                 (step.query_bindings, "query"),
                                 (step.body_bindings, "body")):
                for k, b in group.items():
                    if b.kind == "literal":
                        continue
                    out.append(f"          {label}.{k} <- {b.describe()}")
        out.append(f"    schema: {json.dumps(t.params_schema)[:400]}")
    return "\n".join(out)
