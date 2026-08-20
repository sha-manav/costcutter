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
from shadow.distill.classify import classify_step, classify_steps
from shadow.distill.endpoints import (
    chrome_endpoints, endpoint_key, is_noise, normalize_path,
)
from shadow.distill.provenance import (
    ProvenanceEngine, ProvenanceResult, ValueSite,
)
from shadow.llm import LLMClient, LLMUsage, make_client, register_policy

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


# --------------------------------------------------------------------------
# Path normalisation
# --------------------------------------------------------------------------

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
    # The state-changing steps alone. Used by the backoff pass below.
    core_signature: str = ""
    core_indices: list[int] = field(default_factory=list)

    def core(self) -> "TrimmedEpisode":
        """This episode reduced to its state-changing steps."""
        remap = {old: new for new, old in enumerate(self.core_indices)}
        bindings: dict[tuple[int, str, str], Binding] = {}
        for (step, location, key), binding in self.bindings.items():
            if step not in remap:
                continue
            b = binding.model_copy(deep=True)
            if b.kind == "from_response":
                src = b.source_step_index
                if src not in remap:
                    b = Binding(kind="user_param",
                                param_name=binding.param_name or key.split("::")[-1],
                                example_value=binding.example_value,
                                confidence=0.4, required=False)
                else:
                    b.source_step_index = remap[src]
            bindings[(remap[step], location, key)] = b
        return TrimmedEpisode(
            self.episode_id, self.label,
            [self.records[i] for i in self.core_indices], bindings,
            self.core_signature, self.core_signature,
            list(range(len(self.core_indices))))


def load_bearing_indices(records: list[HttpRecord], prov: ProvenanceResult,
                         chrome: set[str] | None = None) -> list[int]:
    """The steps that actually carry the task.

    Kept: anything that changes state, anything whose response feeds a later
    step, and the final meaningful call (for a read, that *is* the result).
    Dropped: the rest — page chrome, metadata fetches, validation pings.
    This is what makes signatures stable across repetitions of a task and
    what stops an induced tool from replaying a whole page load.
    """
    chrome = chrome or set()
    referenced: set[int] = set()
    for binding in prov.bindings.values():
        if binding.kind == "from_response" and binding.source_step_index is not None:
            referenced.add(binding.source_step_index)

    keep: list[int] = []
    # Every episode keeps a terminal step. Prefer a non-chrome one; when a
    # workload is uniform enough that its only endpoint counts as chrome,
    # fall back to the last meaningful call rather than emitting nothing.
    last_meaningful = max(
        (i for i, r in enumerate(records)
         if not is_noise(r) and endpoint_key(r) not in chrome), default=-1)
    if last_meaningful < 0:
        last_meaningful = max(
            (i for i, r in enumerate(records) if not is_noise(r)), default=-1)
    for i, rec in enumerate(records):
        if is_noise(rec):
            continue
        if i in referenced or i == last_meaningful:
            keep.append(i)
            continue
        if endpoint_key(rec) in chrome:
            continue
        if classify_step(ToolStep(method=rec.method,
                                  path_template=rec.path)) != "read":
            keep.append(i)
    return keep


def trim_episode(ep: Episode, prov: ProvenanceResult,
                 chrome: set[str] | None = None) -> TrimmedEpisode | None:
    keep = load_bearing_indices(ep.records, prov, chrome)
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
    core_idx = [i for i, r in enumerate(records)
                if classify_step(ToolStep(method=r.method,
                                          path_template=r.path)) != "read"]
    core_sig = " | ".join(sig_parts[i] for i in core_idx)
    return TrimmedEpisode(ep.id, ep.label or "", records, bindings,
                          " | ".join(sig_parts), core_sig, core_idx)


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
    if all(isinstance(v, list) for v in non_null):
        flat = [x for v in non_null for x in v]
        scalars = [x for x in flat if not isinstance(x, (list, dict))]
        items = (infer_type(scalars) if scalars and len(scalars) == len(flat)
                 else {})
        return {"type": "array", "items": items} if items else {"type": "array"}
    if all(isinstance(v, dict) for v in non_null):
        return {"type": "object"}
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


def maybe_enum(values: list[Any], enum_max: int,
               max_distinct_ratio: float = 0.5) -> list[Any] | None:
    """An enum, but only when the observed value set looks closed.

    Low cardinality alone is not enough: three customer names in three
    episodes is not a three-valued domain, it is an identifier that happened
    to be sampled three times. Require the values to repeat — a closed set
    saturates as observations accumulate, an identifier does not.
    """
    if any(isinstance(v, (list, dict)) for v in values):
        return None
    seen = [str(v) for v in values if v is not None]
    distinct = list(dict.fromkeys(seen))
    if not (1 < len(distinct) <= enum_max):
        return None
    if not all(len(d) < 64 for d in distinct):
        return None
    if len(distinct) / len(seen) > max_distinct_ratio:
        return None
    return distinct


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


def _fallback_name(group: list[TrimmedEpisode],
                   subject_is_parameter: bool = False) -> tuple[str, str]:
    records = group[0].records
    steps = [ToolStep(method=r.method, path_template=r.path) for r in records]
    mutation = classify_steps(steps)
    # A write tool is named after what it writes. Link-field lookups mention
    # other record types more often than the record being saved, so the
    # state-changing steps get first say.
    doctypes: list[str] = []
    mutating: list[str] = []
    for rec, step in zip(records, steps):
        found: list[str] = []
        m = re.search(r"/api/resource/([^/?]+)", rec.path)
        if m:
            found.append(m.group(1).replace("%20", " "))
        m = re.search(r"doctype=([^&]+)", rec.url)
        if m:
            found.append(m.group(1).replace("%20", " "))
        if isinstance(rec.req_body, dict):
            if isinstance(rec.req_body.get("doctype"), str):
                found.append(rec.req_body["doctype"])
            # A save call carries its record type inside the document payload,
            # which is the only place the subject of a write appears.
            found.extend(_doc_doctypes(rec.req_body))
        doctypes.extend(found)
        if classify_step(step) != "read":
            mutating.extend(found)
    if mutating:
        doctypes = mutating
    subject = Counter(doctypes).most_common(1)[0][0] if doctypes else "record"
    if subject_is_parameter:
        # The record type is chosen by the caller, so naming the tool after
        # whichever type happened to be most common would be a lie.
        subject, doctypes = "record", []
    subject_snake = re.sub(r"\W+", "_", subject).strip("_").lower()

    # The verb follows the mutation class, not the HTTP method: Frappe's desk
    # issues POSTs for list queries, so method alone names half the read
    # tools "create".
    methods = [r.method for r in records]
    loads_existing = any(re.search(r"getdoc\b|/api/resource/[^/]+/", r.path)
                         for r in records)
    if mutation == "destructive":
        verb = "delete" if "DELETE" in methods else "submit"
    elif mutation == "write":
        verb = "update" if loads_existing else "create"
    elif any(re.search(r"get_list|reportview\.get\b|get_count|search", r.path, re.I)
             for r in records):
        verb = "list"
    else:
        verb = "get"
    name = f"{verb}_{subject_snake}"
    plural = "s" if verb == "list" and not subject_snake.endswith("s") else ""
    name += plural
    if subject == "record":
        desc = (f"{verb.capitalize()} records of a caller-specified record type "
                f"in the ERP system.")
    else:
        desc = (f"{verb.capitalize()} {subject.lower()} records in the ERP "
                f"system ({len(records)} step"
                f"{'s' if len(records) != 1 else ''}).")
    return name, desc


def _doc_doctypes(body: dict) -> list[str]:
    from shadow.distill.provenance import _maybe_parse_json

    found: list[str] = []
    for value in body.values():
        parsed = _maybe_parse_json(value)
        candidates = parsed if isinstance(parsed, list) else [parsed]
        for item in candidates:
            if isinstance(item, dict) and isinstance(item.get("doctype"), str):
                found.append(item["doctype"])
    return found


def _offline_name_policy(messages, ctx):
    name, desc = _fallback_name(ctx["group"], ctx.get("subject_is_parameter", False))
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
    records_in: int = 0
    records_load_bearing: int = 0
    diagnostics: list[str] = field(default_factory=list)


def induce(episodes: list[Episode], cfg: Config | None = None,
           client: LLMClient | None = None) -> InductionResult:
    cfg = cfg or get_config()
    client = client or make_client(cfg.models.namer, cfg.models.provider)
    engine = ProvenanceEngine(cfg)
    usage = LLMUsage(model=cfg.models.namer)

    chrome = chrome_endpoints(episodes, cfg.induce.chrome_df)
    trimmed: list[TrimmedEpisode] = []
    for ep in episodes:
        prov = engine.infer(ep, chrome)
        t = trim_episode(ep, prov, chrome)
        if t is not None:
            trimmed.append(t)

    groups: dict[str, list[TrimmedEpisode]] = defaultdict(list)
    for t in trimmed:
        groups[t.signature].append(t)

    result = InductionResult(catalog=ToolCatalog(), usage=usage)
    result.groups_seen = len(groups)
    result.records_in = sum(len(ep.records) for ep in episodes)
    result.records_load_bearing = sum(len(t.records) for t in trimmed)
    result.diagnostics.append(
        f"chrome endpoints dropped by document frequency "
        f"(>={cfg.induce.chrome_df:.0%} of episodes): {sorted(chrome)}")

    specs: list[ToolSpec] = []
    sparse: list[TrimmedEpisode] = []
    for signature, group in groups.items():
        if len(group) < cfg.induce.min_support:
            result.groups_below_support += 1
            result.diagnostics.append(
                f"support {len(group)} < {cfg.induce.min_support}: {signature[:120]}")
            sparse.extend(group)
            continue
        spec, u = _induce_one(group, signature, cfg, client)
        usage = usage + u
        specs.append(spec)

    # Backoff. A task whose full request sequence is rare may still share its
    # state-changing core with other tasks: three different "open a record and
    # save it" flows differ in how the record was found, not in the save. Any
    # value that differs across those cores becomes a parameter, which is how
    # a tool generalises past the record type it was observed on.
    if cfg.induce.backoff:
        backoff_groups: dict[str, list[TrimmedEpisode]] = defaultdict(list)
        for t in sparse:
            if t.core_signature:
                backoff_groups[t.core_signature].append(t.core())
        for signature, group in backoff_groups.items():
            if len(group) < cfg.induce.min_support:
                continue
            spec, u = _induce_one(group, signature, cfg, client)
            spec.description += (" Generalised from tasks that share this "
                                 "state-changing step.")
            usage = usage + u
            specs.append(spec)
            result.diagnostics.append(
                f"backoff group support {len(group)}: {signature[:120]}")

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
    # (base name, observed value vector) -> parameter name. Two sites that
    # always carry the same values under the same field name are one
    # parameter, not two: a list query that repeats `doctype` for its count
    # call should not ask the caller for `doctype` twice.
    unified: dict[tuple[str, str], str] = {}

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

        base = _param_base(site_key, location)
        vector = json.dumps(values, sort_keys=True, default=str)
        existing = unified.get((base, vector))
        if existing is not None:
            resolved[key] = Binding(
                kind="user_param", param_name=existing,
                example_value=values[0] if values else None,
                required=presence[key] >= cfg.induce.required_presence)
            continue
        name = _unique_param_name(site_key, location, used_names)
        used_names.add(name)
        unified[(base, vector)] = name
        schema = infer_type(values)
        enum = maybe_enum(values, cfg.induce.enum_max)
        if enum:
            schema["enum"] = enum
        schema["description"] = f"{site_key.split('::')[0]} for step {step_i}"
        if values:
            schema["examples"] = [values[0]]
        params[name] = schema
        is_required = presence[key] >= cfg.induce.required_presence
        if is_required:
            required.append(name)
        resolved[key] = Binding(kind="user_param", param_name=name,
                                example_value=values[0] if values else None,
                                required=is_required)

    steps = _build_steps(group[0].records, resolved, n_steps)
    subject_is_parameter = any(
        key[2].split("::")[-1].strip("$.") == "doctype" and b.kind == "user_param"
        for key, b in resolved.items())
    name, desc, usage = _name_tool(group, client, subject_is_parameter)
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


def _param_base(site_key: str, location: str) -> str:
    base = re.sub(r"\W+", "_", site_key.split("::")[-1].replace("$", "")).strip("_")
    base = base or location
    return "record_id" if base.startswith("seg") else base


def _unique_param_name(site_key: str, location: str, used: set[str]) -> str:
    base = _param_base(site_key, location)
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


def _name_tool(group: list[TrimmedEpisode], client: LLMClient,
               subject_is_parameter: bool = False) -> tuple[str, str, LLMUsage]:
    hint = ("\nThe record type is a caller-supplied parameter, so the name "
            "must be generic." if subject_is_parameter else "")
    messages = [
        {"role": "system", "content": NAME_SYSTEM},
        {"role": "user", "content": _render_group(group) + hint},
    ]
    resp = client.complete(messages, policy="induce_name",
                           policy_context={"group": group,
                                           "subject_is_parameter": subject_is_parameter})
    try:
        data = resp.json()
        name = re.sub(r"\W+", "_", str(data["name"])).strip("_").lower()
        desc = str(data["description"]).strip()
    except Exception:
        name, desc = _fallback_name(group, subject_is_parameter)
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
