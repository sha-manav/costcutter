"""FastMCP server exposing the synthesized tools.

Only verified tools are exposed. Writes and destructive tools are gated
behind --allow-writes and are announced as such in their descriptions, so a
caller cannot mutate the system by accident.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from shadow.capture.schema import ToolCatalog, ToolSpec, read_catalog
from shadow.config import Config, get_config
from shadow.serve.executor import ToolExecutor


def _signature_hint(spec: ToolSpec) -> str:
    props = spec.params_schema.get("properties", {})
    required = set(spec.params_schema.get("required", []))
    parts = []
    for name, schema in props.items():
        t = schema.get("type", "string")
        opt = "" if name in required else "?"
        enum = schema.get("enum")
        if enum:
            t = "|".join(map(str, enum[:4]))
        parts.append(f"{name}{opt}: {t}")
    return ", ".join(parts)


def tool_description(spec: ToolSpec) -> str:
    prefix = ""
    if spec.mutation_class == "write":
        prefix = "[writes data] "
    elif spec.mutation_class == "destructive":
        prefix = "[DESTRUCTIVE] "
    return f"{prefix}{spec.description} Parameters: {_signature_hint(spec)}"


def exposed_tools(catalog: ToolCatalog, allow_writes: bool,
                  require_verified: bool = True) -> list[ToolSpec]:
    out = []
    for spec in catalog.tools:
        if require_verified and not spec.verified:
            continue
        if spec.mutation_class != "read" and not allow_writes:
            continue
        out.append(spec)
    return out


def build_server(catalog_path: str | Path, allow_writes: bool = False,
                 cfg: Config | None = None, require_verified: bool = True):
    from fastmcp import FastMCP

    cfg = cfg or get_config()
    catalog = read_catalog(catalog_path)
    executor = ToolExecutor(cfg, allow_writes=allow_writes)
    mcp = FastMCP("shadow-synthesized")

    for spec in exposed_tools(catalog, allow_writes, require_verified):
        _register(mcp, spec, executor)
    return mcp


def _register(mcp, spec: ToolSpec, executor: ToolExecutor) -> None:
    schema = {
        "type": "object",
        "properties": spec.params_schema.get("properties", {}),
        "required": spec.params_schema.get("required", []),
    }

    async def handler(**kwargs: Any) -> str:
        result = executor.execute(spec, kwargs)
        return json.dumps({
            "ok": result.ok,
            "value": result.value,
            "error": result.error,
            "duration_s": round(result.duration_s, 3),
        }, default=str)

    handler.__name__ = spec.name
    mcp.add_tool(
        fn=handler,
        name=spec.name,
        description=tool_description(spec),
        parameters=schema,
    )
