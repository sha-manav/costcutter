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


def mutation_prefix(spec: ToolSpec) -> str:
    if spec.mutation_class == "write":
        return "[writes data] "
    if spec.mutation_class == "destructive":
        return "[DESTRUCTIVE] "
    return ""


def tool_description(spec: ToolSpec) -> str:
    """Description for MCP, where the schema travels separately but a
    signature hint in the text still helps a caller."""
    return f"{mutation_prefix(spec)}{spec.description} " \
           f"Parameters: {_signature_hint(spec)}"


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
    from fastmcp.tools.function_tool import FunctionTool

    schema = {
        "type": "object",
        "properties": spec.params_schema.get("properties", {}),
        "required": spec.params_schema.get("required", []),
        "additionalProperties": False,
    }

    async def handler(**kwargs: Any) -> str:
        result = executor.execute(spec, kwargs)
        return json.dumps({
            "ok": result.ok,
            "value": result.value,
            # Earlier step responses are part of the answer for multi-step
            # tools whose last call is a count or a save acknowledgement.
            "steps": result.values[:-1],
            "error": result.error,
            "duration_s": round(result.duration_s, 3),
        }, default=str)

    handler.__name__ = spec.name
    # The parameter schema is induced, not derived from a Python signature,
    # so the tool is constructed directly rather than via from_function.
    mcp.add_tool(FunctionTool(
        fn=handler,
        name=spec.name,
        description=tool_description(spec),
        parameters=schema,
    ))
