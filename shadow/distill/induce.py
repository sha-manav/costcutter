"""Template induction: episodes -> executable typed tools.

Grouping is on the *load-bearing* subsequence of an episode — writes, steps
whose responses feed a later step, and the final step of a read. Dropping
the rest is what makes signatures stable across repetitions of the same task
and what keeps an induced tool from replaying a page's worth of chatter.
"""
from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote

from shadow.capture.schema import (
    Binding, Episode, HttpRecord, ToolCatalog, ToolSpec, ToolStep,
)
from shadow.config import Config, get_config
from shadow.distill.classify import classify_steps
from shadow.distill.provenance import (
    ProvenanceEngine, ProvenanceResult, ValueSite,
)
from shadow.llm import LLMClient, LLMUsage, make_client, register_policy

# Endpoints that carry no task semantics even though they are API calls.
NOISE_PATH_PATTERNS = (
    re.compile(r"frappe\.realtime"),
    re.compile(r"frappe\.client\.is_document_amended"),
    re.compile(r"frappe\.desk\.doctype\.notification"),
    re.compile(r"frappe\.desk\.form\.utils\.get_.*_count"),
    re.compile(r"frappe\.desk\.reportview\.get_sidebar_stats"),
    re.compile(r"log_error|error_log|ping$|method/frappe\.utils\.telemetry"),
    re.compile(r"/api/method/frappe\.desk\.notifications\."),
    re.compile(r"/api/method/frappe\.core\.doctype\.activity_log"),
)

ID_SEGMENT = re.compile(
    r"""^(
        [A-Z]{2,8}(-[A-Za-z]{2,8})*-\d{2,4}-\d{3,6}      # SAL-ORD-2025-00001
        | [0-9a-f]{12,}                                   # hashes / uuids
        | \d{3,}                                          # long numerics
        | .*@.*\..*                                       # e-mail style keys
    )$""",
    re.VERBOSE,
)

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


# --------------------------------------------------------------------------
# Path normalisation
# --------------------------------------------------------------------------

def normalize_path(path: str) -> tuple[str, list[int]]:
    """Replace identifier-looking segments with placeholders.

    Returns the templated path and the indices of the segments replaced.
    """
    segs = path.strip("/").split("/")
    var_idx: list[int] = []
    out: list[str] = []
    for i, seg in enumerate(segs):
        if ID_SEGMENT.match(seg):
            var_idx.append(i)
            out.append("{}")
        else:
            out.append(seg)
    return "/" + "/".join(out), var_idx


def is_noise(rec: HttpRecord) -> bool:
    if rec.is_document or rec.is_polling:
        return True
    if not rec.path.startswith("/api/"):
        return True
    return any(p.search(rec.path) for p in NOISE_PATH_PATTERNS)


# --------------------------------------------------------------------------
# Load-bearing trimming
# --------------------------------------------------------------------------

@dataclass
class TrimmedEpisode:
    episode_id: str
    label: str
    records: list[HttpRecord]
    # Bindings keyed by (new_step_index, location, key)
    bindings: dict[tuple[int, str, str], Binding]
    signature: str


def load_bearing_indices(records: list[HttpRecord],
                         prov: ProvenanceResult) -> list[int]:
    referenced: set[int] = set()
    for binding in prov.bindings.values():
        if binding.kind == "from_response" and binding.source_step_index is not None:
            referenced.add(binding.source_step_index)

    keep: list[int] = []
    last_meaningful = max(
        (i for i, r in enumerate(records) if not is_noise(r)), default=-1)
    for i, rec in enumerate(records):
        if is_noise(rec):
            continue
        if rec.method != "GET" or i in referenced or i == last_meaningful:
            keep.append(i)
    return keep


def trim_episode(ep: Episode, prov: ProvenanceResult) -> TrimmedEpisode | None:
    keep = load_bearing_indices(ep.records, prov)
    if not keep:
        return None
    remap = {old: new for new, old in enumerate(keep)}
    records = [ep.records[i] for i in keep]

    bindings: dict[tuple[int, str, str], Binding] = {}
    for site, binding in prov.bindings.items():
        if site.step_index not in remap:
            continue
        b = binding.model_copy(deep=True)
        if b.kind == "from_response":
            src = b.source_step_index
            if src not in remap:
                # Source was trimmed away: the value cannot be recovered at
                # run time, so it becomes a user parameter instead.
                b = Binding(kind="user_param",
                            param_name=binding.param_name or _site_param(site),
                            example_value=binding.example_value, confidence=0.4)
            else:
                b.source_step_index = remap[src]
        bindings[(remap[site.step_index], site.location, site.key)] = b

    sig_parts = [f"{r.method} {normalize_path(r.path)[0]}" for r in records]
    return TrimmedEpisode(ep.id, ep.label or "", records, bindings,
                          " | ".join(sig_parts))


def _site_param(site: ValueSite) -> str:
    key = site.key.split("::")[0].replace("$", "")
    return re.sub(r"\W+", "_", key).strip("_") or "value"


# --------------------------------------------------------------------------
# Typing
# --------------------------------------------------------------------------

def infer_type(values: list[Any]) -> dict[str, Any]:
    non_null = [v for v in values if v is not None and v != ""]
    if not non_null:
        return {"type": "string"}
    strs = [str(v) for v in non_null]
    if all(isinstance(v, bool) for v in non_null):
        return {"type": "boolean"}
    if all(re.fullmatch(r"-?\d+", s) for s in strs):
        return {"type": "integer"}
    if all(re.fullmatch(r"-?\d+(\.\d+)?", s) for s in strs):
        return {"type": "number"}
    if all(DATE_RE.match(s) for s in strs):
        return {"type": "string", "format": "date"}
    return {"type": "string"}


def maybe_enum(values: list[Any], enum_max: int) -> list[Any] | None:
    distinct = list(dict.fromkeys(str(v) for v in values if v is not None))
    if 1 < len(distinct) <= enum_max and all(len(d) < 64 for d in distinct):
        return distinct
    return None


# --------------------------------------------------------------------------
# Naming
# --------------------------------------------------------------------------

NAME_SYSTEM = (
    "You name automation tools synthesized from observed HTTP traffic. "
    "Reply with JSON only: {\"name\": \"snake_case_verb_noun\", "
    "\"description\": \"one sentence\"}. The description must state what the "
    "tool does to the business system, never which endpoints it calls."
)

_VERB_BY_METHOD = {"POST": "create", "PUT": "update", "PATCH": "update",
                   "DELETE": "delete", "GET": "get"}


def _fallback_name(group: list[TrimmedEpisode]) -> tuple[str, str]:
    records = group[0].records
    doctypes: list[str] = []
    for rec in records:
        m = re.search(r"/api/resource/([^/?]+)", rec.path)
        if m:
            doctypes.append(m.group(1).replace("%20", " "))
        m = re.search(r"doctype=([^&]+)", rec.url)
        if m:
            doctypes.append(m.group(1).replace("%20", " "))
        if isinstance(rec.req_body, dict) and isinstance(rec.req_body.get("doctype"), str):
            doctypes.append(rec.req_body["doctype"])
    subject = Counter(doctypes).most_common(1)[0][0] if doctypes else "record"
    subject_snake = re.sub(r"\W+", "_", subject).strip("_").lower()

    methods = [r.method for r in records]
    if "DELETE" in methods:
        verb = "delete"
    elif any(re.search(r"submit|cancel", r.path, re.I) for r in records):
        verb = "submit"
    elif any(m in {"POST", "PUT", "PATCH"} for m in methods):
        # A POST to a *_list/get_list endpoint is still a read in Frappe.
        if all(re.search(r"get_list|reportview|get_count|search", r.path, re.I)
               for r in records if r.method != "GET"):
            verb = "list"
        else:
            verb = _VERB_BY_METHOD.get(
                next(m for m in methods if m in {"POST", "PUT", "PATCH"}), "update")
    elif any(re.search(r"get_list|reportview|get_count", r.path, re.I) for r in records):
        verb = "list"
    else:
        verb = "get"
    name = f"{verb}_{subject_snake}"
    plural = "s" if verb == "list" and not subject_snake.endswith("s") else ""
    name += plural
    desc = (f"{verb.capitalize()} {subject.lower()} records in the ERP system "
            f"({len(records)} step{'s' if len(records) != 1 else ''}).")
    return name, desc


def _offline_name_policy(messages, ctx):
    name, desc = _fallback_name(ctx["group"])
    return json.dumps({"name": name, "description": desc})


register_policy("induce_name", _offline_name_policy)


def _render_group(group: list[TrimmedEpisode], limit: int = 12) -> str:
    lines = [f"observed in {len(group)} episodes; labels: "
             + ", ".join(sorted({g.label for g in group})[:5])]
    for i, rec in enumerate(group[0].records[:limit]):
        body = ""
        if isinstance(rec.req_body, dict):
            sample = {k: rec.req_body[k] for k in list(rec.req_body)[:6]}
            body = " body=" + json.dumps(sample, default=str)[:200]
        lines.append(f"[{i}] {rec.method} {rec.path}{body}")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Induction
# --------------------------------------------------------------------------

@dataclass
class InductionResult:
    catalog: ToolCatalog
    usage: LLMUsage
    groups_seen: int = 0
    groups_below_support: int = 0
    dropped_by_rank: int = 0
    diagnostics: list[str] = field(default_factory=list)


def induce(episodes: list[Episode], cfg: Config | None = None,
           client: LLMClient | None = None) -> InductionResult:
    cfg = cfg or get_config()
    client = client or make_client(cfg.models.namer, cfg.models.provider)
    engine = ProvenanceEngine(cfg)
    usage = LLMUsage(model=cfg.models.namer)

    trimmed: list[TrimmedEpisode] = []
    for ep in episodes:
        prov = engine.infer(ep)
        t = trim_episode(ep, prov)
        if t is not None:
            trimmed.append(t)

    groups: dict[str, list[TrimmedEpisode]] = defaultdict(list)
    for t in trimmed:
        groups[t.signature].append(t)

    result = InductionResult(catalog=ToolCatalog(), usage=usage)
    result.groups_seen = len(groups)

    specs: list[ToolSpec] = []
    for signature, group in groups.items():
        if len(group) < cfg.induce.min_support:
            result.groups_below_support += 1
            result.diagnostics.append(
                f"support {len(group)} < {cfg.induce.min_support}: {signature[:120]}")
            continue
        spec, u = _induce_one(group, signature, cfg, client)
        usage = usage + u
        specs.append(spec)

    specs.sort(key=lambda s: (-s.support, s.name))
    if len(specs) > cfg.induce.max_tools:
        result.dropped_by_rank = len(specs) - cfg.induce.max_tools
        specs = specs[:cfg.induce.max_tools]

    _dedupe_names(specs)
    result.catalog = ToolCatalog(
        tools=specs, generated_from_episodes=len(episodes))
    result.usage = usage
    return result


def _dedupe_names(specs: list[ToolSpec]) -> None:
    seen: Counter[str] = Counter()
    for spec in specs:
        seen[spec.name] += 1
        if seen[spec.name] > 1:
            spec.name = f"{spec.name}_{seen[spec.name]}"


def _induce_one(group: list[TrimmedEpisode], signature: str, cfg: Config,
                client: LLMClient) -> tuple[ToolSpec, LLMUsage]:
    n_steps = len(group[0].records)
    # Collect every value site seen anywhere in the group.
    site_keys: list[tuple[int, str, str]] = []
    for t in group:
        for key in t.bindings:
            if key not in site_keys:
                site_keys.append(key)

    observed: dict[tuple[int, str, str], list[Any]] = defaultdict(list)
    kinds: dict[tuple[int, str, str], list[str]] = defaultdict(list)
    from_resp: dict[tuple[int, str, str], list[Binding]] = defaultdict(list)
    for t in group:
        for key in site_keys:
            b = t.bindings.get(key)
            if b is None:
                continue
            observed[key].append(b.example_value if b.kind != "literal"
                                 else b.literal_value)
            kinds[key].append(b.kind)
            if b.kind == "from_response":
                from_resp[key].append(b)

    presence = {key: len(kinds[key]) / len(group) for key in site_keys}
    params: dict[str, dict[str, Any]] = {}
    required: list[str] = []
    resolved: dict[tuple[int, str, str], Binding] = {}
    used_names: set[str] = set()

    for key in site_keys:
        step_i, location, site_key = key
        values = observed[key]
        distinct = {json.dumps(v, sort_keys=True, default=str) for v in values}

        if len(distinct) == 1 and "from_response" not in kinds[key]:
            resolved[key] = Binding(kind="literal", literal_value=values[0],
                                    example_value=values[0])
            continue

        stable_source = _stable_source(from_resp[key], len(group))
        if stable_source is not None:
            resolved[key] = stable_source
            continue

        name = _unique_param_name(site_key, location, used_names)
        used_names.add(name)
        schema = infer_type(values)
        enum = maybe_enum(values, cfg.induce.enum_max)
        if enum:
            schema["enum"] = enum
        schema["description"] = f"{site_key.split('::')[0]} for step {step_i}"
        if values:
            schema["examples"] = [values[0]]
        params[name] = schema
        if presence[key] >= cfg.induce.required_presence:
            required.append(name)
        resolved[key] = Binding(kind="user_param", param_name=name,
                                example_value=values[0] if values else None)

    steps = _build_steps(group[0].records, resolved, n_steps)
    name, desc, usage = _name_tool(group, client)
    example_args = {b.param_name: b.example_value
                    for b in resolved.values()
                    if b.kind == "user_param" and b.param_name}
    n_from_response = sum(1 for b in resolved.values() if b.kind == "from_response")

    spec = ToolSpec(
        name=name,
        description=desc,
        params_schema={"type": "object", "properties": params,
                       "required": sorted(required)},
        steps=steps,
        mutation_class=classify_steps(steps),
        support=len(group),
        source_episode_ids=[t.episode_id for t in group],
        signature=signature,
        response_shape=_response_shape(group[0].records[-1].resp_body),
        example_args=example_args,
        n_from_response_bindings=n_from_response,
    )
    return spec, usage


def _response_shape(body: Any, limit: int = 40) -> dict[str, str]:
    """Record the observed response structure, not its values."""
    from shadow.distill.provenance import _walk_json

    shape: dict[str, str] = {}
    if body is None:
        return shape
    for path, leaf in _walk_json(body):
        # Collapse array indices so the shape survives a different row count.
        generic = re.sub(r"\[\d+\]", "[]", path)
        shape.setdefault(generic, type(leaf).__name__)
        if len(shape) >= limit:
            break
    return shape


def _stable_source(bindings: list[Binding], group_size: int) -> Binding | None:
    """A from_response binding is kept only if the same (step, path) resolved
    it in a majority of the group's episodes — this is the cross-episode
    stability check that kills coincidental matches."""
    if not bindings:
        return None
    counts = Counter((b.source_step_index, b.json_path, b.transform) for b in bindings)
    (src, path, transform), n = counts.most_common(1)[0]
    if n / group_size < 0.6:
        return None
    return Binding(kind="from_response", source_step_index=src, json_path=path,
                   transform=transform, confidence=n / group_size,
                   example_value=bindings[0].example_value)


def _unique_param_name(site_key: str, location: str, used: set[str]) -> str:
    base = re.sub(r"\W+", "_", site_key.split("::")[-1].replace("$", "")).strip("_")
    base = base or location
    if base.startswith("seg"):
        base = "record_id"
    name = base
    i = 2
    while name in used:
        name = f"{base}_{i}"
        i += 1
    return name


def _build_steps(records: list[HttpRecord],
                 resolved: dict[tuple[int, str, str], Binding],
                 n_steps: int) -> list[ToolStep]:
    steps: list[ToolStep] = []
    for i in range(n_steps):
        rec = records[i]
        template, var_idx = normalize_path(rec.path)
        path_bindings: dict[str, Binding] = {}
        # Fill placeholders left to right with p0, p1, ...
        parts = template.split("{}")
        rebuilt = parts[0]
        for n, idx in enumerate(var_idx):
            pname = f"p{n}"
            key = (i, "path", f"seg{idx}")
            path_bindings[pname] = resolved.get(
                key, Binding(kind="literal",
                             literal_value=rec.path.strip("/").split("/")[idx]))
            rebuilt += "{" + pname + "}" + parts[n + 1]

        query_bindings: dict[str, Binding] = {}
        for key, binding in resolved.items():
            if key[0] != i or key[1] != "query":
                continue
            query_bindings[key[2]] = binding
        body_bindings: dict[str, Binding] = {}
        for key, binding in resolved.items():
            if key[0] != i or key[1] != "body":
                continue
            body_bindings[key[2]] = binding

        encoding = "none"
        if rec.req_body is not None:
            ct = rec.req_headers.get("content-type", "") or rec.req_headers.get(
                "Content-Type", "")
            encoding = "form" if "x-www-form-urlencoded" in ct.lower() else "json"

        steps.append(ToolStep(
            method=rec.method,
            path_template=rebuilt,
            query_bindings=query_bindings,
            body_bindings=body_bindings,
            path_bindings=path_bindings,
            body_encoding=encoding,
        ))
    return steps


def _name_tool(group: list[TrimmedEpisode], client: LLMClient
               ) -> tuple[str, str, LLMUsage]:
    messages = [
        {"role": "system", "content": NAME_SYSTEM},
        {"role": "user", "content": _render_group(group)},
    ]
    resp = client.complete(messages, policy="induce_name",
                           policy_context={"group": group})
    try:
        data = resp.json()
        name = re.sub(r"\W+", "_", str(data["name"])).strip("_").lower()
        desc = str(data["description"]).strip()
    except Exception:
        name, desc = _fallback_name(group)
    return name or "unnamed_tool", desc, resp.usage


def encode_value(value: Any, transform: str) -> Any:
    if transform == "str":
        return str(value)
    if transform == "int":
        return int(float(value))
    if transform == "float":
        return float(value)
    if transform == "urlencode":
        return quote(str(value), safe="")
    if transform == "lower":
        return str(value).lower()
    if transform == "json":
        return json.dumps(value)
    return value
