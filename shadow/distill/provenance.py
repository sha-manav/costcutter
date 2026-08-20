"""Parameter provenance inference.

For every scalar that appears in a request (path segment, query value, body
leaf), decide where it came from: a literal the app always sends, a value
lifted out of an earlier response in the same episode, or something the user
supplied.

This is what turns a flat request log into a dataflow graph, and it is the
step that makes multi-call tools executable rather than replayable.
"""
from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Iterable
from urllib.parse import quote, unquote

from shadow.capture.schema import Binding, Episode, HttpRecord, Transform
from shadow.config import Config, get_config
from shadow.distill.endpoints import chrome_endpoints, endpoint_key


@dataclass(frozen=True)
class ValueSite:
    """Where a value sits inside one request.

    Identity is the position, not the value: a site is used as a dict key and
    a value may be a list (a whole `filters` expression), which is not
    hashable.
    """

    step_index: int
    location: str  # "path" | "query" | "body" | "header"
    key: str       # query key, body json-path, or path segment index "seg3"
    value: Any = field(compare=False, default=None)


@dataclass
class Candidate:
    source_step_index: int
    json_path: str
    transform: Transform
    priority: int  # lower is better

    def score(self, current_step: int) -> tuple[int, int, int]:
        # (match quality, recency, path depth) — all ascending, lower wins.
        recency = current_step - self.source_step_index
        depth = self.json_path.count(".") + self.json_path.count("[")
        return (self.priority, recency, depth)


@dataclass
class ProvenanceResult:
    """Bindings for one episode, keyed by the site they fill."""

    bindings: dict[ValueSite, Binding] = field(default_factory=dict)
    unresolved: list[ValueSite] = field(default_factory=list)

    def trace(self, episode: Episode, verbose: bool = False) -> str:
        """Human-auditable dataflow trace (the M4 done-check).

        By default this shows the bindings a reader would actually audit:
        values lifted out of earlier responses, and identifier-like path
        segments. Constant path segments (`api`, `method`, the endpoint name)
        are structure, not data, and are only shown with `verbose`.
        """
        from shadow.distill.endpoints import normalize_path

        counts = Counter(b.kind for b in self.bindings.values())
        lines = [f"episode {episode.id}  label={episode.label!r}",
                 f"  bindings: {counts['from_response']} from earlier responses, "
                 f"{counts['user_param']} user-supplied, {counts['literal']} literal"]
        for i, rec in enumerate(episode.records):
            _template, var_idx = normalize_path(rec.path)
            id_segments = {f"seg{n}" for n in var_idx}
            rows = []
            for site, binding in sorted(self.bindings.items(),
                                        key=lambda kv: (kv[0].step_index, kv[0].key)):
                if site.step_index != i:
                    continue
                if not verbose and site.location == "path" and site.key not in id_segments:
                    continue
                if not verbose and binding.kind == "literal":
                    continue
                rows.append(f"        {site.location}:{site.key} = "
                            f"{binding.describe()}   (observed {site.value!r})")
            lines.append(f"  [{i}] {rec.method} {rec.path}")
            lines.extend(rows)
        return "\n".join(lines)


# --------------------------------------------------------------------------
# Value extraction
# --------------------------------------------------------------------------

def _walk_json(obj: Any, prefix: str = "$") -> Iterable[tuple[str, Any]]:
    """Yield (json_path, leaf) for every scalar leaf."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from _walk_json(v, f"{prefix}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _walk_json(v, f"{prefix}[{i}]")
    else:
        yield prefix, obj


def _maybe_parse_json(value: Any) -> Any:
    """Frappe nests JSON inside form fields; unwrap so leaves are reachable."""
    if isinstance(value, str):
        s = value.strip()
        if s[:1] in "[{" and s[-1:] in "]}":
            try:
                return json.loads(s)
            except ValueError:
                return value
    return value


def request_sites(rec: HttpRecord, step_index: int) -> list[ValueSite]:
    sites: list[ValueSite] = []
    for i, seg in enumerate(rec.path.strip("/").split("/")):
        if seg:
            sites.append(ValueSite(step_index, "path", f"seg{i}", unquote(seg)))
    for k, v in rec.query.items():
        sites.extend(_field_sites(step_index, "query", k, v))
    body = rec.req_body
    if isinstance(body, dict):
        for k, v in body.items():
            sites.extend(_field_sites(step_index, "body", k, v))
    elif isinstance(body, list):
        for path, leaf in _walk_json(body):
            sites.append(ValueSite(step_index, "body", path, leaf))
    return sites


# `_=1712345678` is a cache-buster and `__last_sync_on` is a client clock:
# both change every request and neither is a parameter of anyone's task.
# Other framework flags (`__islocal`, `__unsaved`) are kept — they are
# constant, so they become literals, and dropping them would break the very
# save calls that need them.
_VOLATILE_KEYS = {"_", "__last_sync_on", "__last_update", "_t", "timestamp"}


def _is_bookkeeping(key: str) -> bool:
    return key in _VOLATILE_KEYS


def _is_task_field(path: str) -> bool:
    """Reject volatile leaves and anything inside a framework subtree.

    `__islocal` is a flag the save call needs, so scalar `__` keys stay.
    `__onload.dashboard_info[0].total_unpaid` is server-computed display
    data the client echoes back; its leaves are not parameters of anything.
    """
    segments = [seg.split("[")[0] for seg in path.lstrip("$.").split(".")]
    if not segments:
        return True
    if any(seg.startswith("__") for seg in segments[:-1]):
        return False
    return not _is_bookkeeping(segments[-1])


def _field_sites(step_index: int, location: str, key: str,
                 value: Any) -> list[ValueSite]:
    """One request field -> the value sites inside it.

    An object field is walked, so each of its leaves becomes its own
    parameter (a saved document's fields really are separate parameters).
    A list field is kept whole: exploding `fields` or `filters` into
    `fields_0`, `fields_1`, … produces tools nobody can call, and the list
    as a unit is the thing a caller actually varies.
    """
    if _is_bookkeeping(key):
        return []
    parsed = _maybe_parse_json(value)
    if isinstance(parsed, dict):
        return [ValueSite(step_index, location, f"{key}::{path}", leaf)
                for path, leaf in _walk_json(parsed, "$")
                if _is_task_field(path)]
    if isinstance(parsed, list):
        return [ValueSite(step_index, location, key, parsed)]
    return [ValueSite(step_index, location, key, value)]


def _index_response(rec: HttpRecord, step_index: int) -> list[tuple[str, Any]]:
    """Leaves of a response that could genuinely be a source of a later value.

    A leaf that merely echoes this request's own input is not a source: the
    step did not produce it, the user did. Without this, any endpoint that
    reflects its parameters back (settings saves, validation calls) looks
    like the origin of every value the user typed, and the "most recent
    source wins" rule then binds tools to the wrong step.
    """
    if rec.resp_body is None:
        return []
    echoed = {str(site.value) for site in request_sites(rec, step_index)
              if not isinstance(site.value, (dict, list))}
    return [(path, leaf) for path, leaf in _walk_json(rec.resp_body)
            if str(leaf) not in echoed]


# --------------------------------------------------------------------------
# Matching
# --------------------------------------------------------------------------

def _match(value: Any, leaf: Any) -> Transform | None:
    """Return the transform that turns `leaf` into `value`, if any.

    Priority order is encoded by the caller via MATCH_PRIORITY.
    """
    if leaf is None:
        return None
    if value == leaf:
        return "identity"
    sv, sl = str(value), str(leaf)
    if sv == sl:
        return "str"
    if isinstance(leaf, (int, float)) and sv == str(leaf):
        return "str"
    try:
        if isinstance(value, str) and value.strip() and float(value) == float(leaf):
            return "int" if float(leaf).is_integer() else "float"
    except (TypeError, ValueError):
        pass
    if unquote(sv) == sl or sv == quote(sl, safe=""):
        return "urlencode"
    if sv.lower() == sl.lower():
        return "lower"
    return None


MATCH_PRIORITY: dict[Transform, int] = {
    "identity": 0, "str": 1, "int": 2, "float": 2, "urlencode": 3,
    "lower": 4, "json": 5,
}


class ProvenanceEngine:
    def __init__(self, cfg: Config | None = None) -> None:
        cfg = cfg or get_config()
        self.min_value_len = cfg.provenance.min_value_len
        self.stoplist = {s.lower() for s in cfg.provenance.stoplist}
        self.max_lookback = cfg.provenance.max_lookback_steps

    def _is_matchable(self, value: Any) -> bool:
        """Guard against spurious matches on short or ubiquitous values."""
        if value is None or isinstance(value, bool):
            return False
        s = str(value)
        if not s.strip():
            return False
        if s.lower() in self.stoplist:
            return False
        if len(s) < self.min_value_len:
            return False
        return True

    def infer(self, episode: Episode,
              chrome: set[str] | None = None) -> ProvenanceResult:
        """Infer bindings for one episode.

        `chrome` names endpoints whose responses must not be used as
        provenance sources: app metadata blobs match far too many values by
        coincidence, and a binding onto one is always a false positive.
        """
        chrome = chrome or set()
        result = ProvenanceResult()
        responses: list[list[tuple[str, Any]]] = []

        for i, rec in enumerate(episode.records):
            for site in request_sites(rec, i):
                if not self._is_matchable(site.value):
                    result.bindings[site] = Binding(
                        kind="literal", literal_value=site.value,
                        example_value=site.value, confidence=0.5)
                    continue
                cand = self._best_candidate(site, responses, i)
                if cand is None:
                    result.unresolved.append(site)
                    result.bindings[site] = Binding(
                        kind="user_param",
                        param_name=_param_name(site),
                        example_value=site.value,
                        confidence=0.5,
                    )
                else:
                    result.bindings[site] = Binding(
                        kind="from_response",
                        source_step_index=cand.source_step_index,
                        json_path=cand.json_path,
                        transform=cand.transform,
                        example_value=site.value,
                        confidence=1.0 - 0.1 * cand.priority,
                    )
            responses.append([] if endpoint_key(rec) in chrome
                             else _index_response(rec, i))
        return result

    def _best_candidate(self, site: ValueSite, responses: list[list[tuple[str, Any]]],
                        current: int) -> Candidate | None:
        candidates: list[Candidate] = []
        lo = max(0, current - self.max_lookback)
        for step in range(lo, len(responses)):
            for path, leaf in responses[step]:
                transform = _match(site.value, leaf)
                if transform is None:
                    continue
                candidates.append(Candidate(step, path, transform,
                                            MATCH_PRIORITY[transform]))
        if not candidates:
            return None
        return min(candidates, key=lambda c: c.score(current))


def _param_name(site: ValueSite) -> str:
    key = site.key.split("::")[0]
    key = key.replace("$", "").replace(".", "_").replace("[", "_").replace("]", "")
    return key or f"{site.location}_{site.step_index}"


def infer_all(episodes: list[Episode], cfg: Config | None = None
              ) -> dict[str, ProvenanceResult]:
    cfg = cfg or get_config()
    engine = ProvenanceEngine(cfg)
    chrome = chrome_endpoints(episodes, cfg.induce.chrome_df)
    return {ep.id: engine.infer(ep, chrome) for ep in episodes}
