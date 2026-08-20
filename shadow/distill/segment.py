"""Episode segmentation.

Cuts the filtered stream into candidate user-level tasks:

1. primary   — idle gap between consecutive kept records
2. secondary — top-level document navigation (the markers kept in M1)
3. refine    — an LLM labels each candidate and scores its coherence;
               incoherent episodes are split, same-labelled neighbours merge
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from shadow.capture.schema import Episode, HttpRecord
from shadow.config import Config, get_config
from shadow.llm import LLMClient, LLMUsage, make_client, register_policy

LABEL_SYSTEM = (
    "You segment web-application HTTP traces into user tasks. "
    "Given one candidate episode of requests, reply with JSON only: "
    '{"label": "<short imperative task label>", "coherence": <0..1>, '
    '"split_after": <index or null>}. '
    "coherence is 1.0 when every request serves one user goal. "
    "Set split_after to the last index of the first task when the episode "
    "clearly contains two unrelated tasks, else null."
)


def _cut_on_gaps(records: list[HttpRecord], idle_gap_s: float) -> list[list[HttpRecord]]:
    groups: list[list[HttpRecord]] = []
    current: list[HttpRecord] = []
    for rec in records:
        if current and rec.ts_start - current[-1].ts_end > idle_gap_s:
            groups.append(current)
            current = []
        # A document load starts a new episode unless it opens the current one.
        if current and rec.is_document and not all(r.is_document for r in current):
            groups.append(current)
            current = []
        current.append(rec)
    if current:
        groups.append(current)
    return groups


def _render_episode(records: list[HttpRecord], limit: int = 40) -> str:
    lines = []
    for i, rec in enumerate(records[:limit]):
        body = ""
        if isinstance(rec.req_body, dict):
            keys = list(rec.req_body.keys())[:6]
            body = " body_keys=" + ",".join(map(str, keys))
        q = ""
        if rec.query:
            q = " q=" + ",".join(list(rec.query.keys())[:5])
        lines.append(f"[{i}] {rec.method} {rec.path}{q}{body}")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Deterministic labeller used when no model credentials are present. It reads
# only the trace (never task metadata), so it leaks nothing the LLM would not
# also see.
# --------------------------------------------------------------------------
_DOCTYPE_RE = re.compile(r"/api/resource/([^/?]+)")
_METHOD_RE = re.compile(r"/api/method/([\w.]+)")
_APP_ROUTE_RE = re.compile(r"/app/([\w-]+)")


def _guess_label(records: list[HttpRecord]) -> str:
    doctypes: list[str] = []
    verbs: set[str] = set()
    for rec in records:
        m = _DOCTYPE_RE.search(rec.path)
        if m:
            doctypes.append(m.group(1).replace("%20", " "))
        m = _APP_ROUTE_RE.search(rec.path)
        if m and rec.is_document:
            doctypes.append(m.group(1).replace("-", " ").title())
        m = _METHOD_RE.search(rec.path)
        if m:
            tail = m.group(1).split(".")[-1]
            verbs.add(tail)
        if rec.method in {"POST", "PUT", "PATCH"} and "/api/resource/" in rec.path:
            verbs.add("save")
        if rec.method == "DELETE":
            verbs.add("delete")
    subject = max(set(doctypes), key=doctypes.count) if doctypes else "app"
    if "delete" in verbs:
        verb = "delete"
    elif "submit" in verbs or "savedocs" in verbs:
        verb = "submit"
    elif "save" in verbs or "insert" in verbs:
        verb = "create or update"
    elif "get_list" in verbs or "get_count" in verbs or "reportview" in verbs:
        verb = "list"
    else:
        verb = "view"
    return f"{verb} {subject}".strip().lower()


def _offline_label_policy(messages, ctx):
    records: list[HttpRecord] = ctx.get("records", [])
    label = _guess_label(records)
    # Coherence heuristic: an episode touching many unrelated doctypes with
    # writes to more than one of them is likely two tasks glued together.
    doctypes = {m.group(1) for rec in records
                if (m := _DOCTYPE_RE.search(rec.path))}
    written = {m.group(1) for rec in records
               if rec.method in {"POST", "PUT", "PATCH", "DELETE"}
               and (m := _DOCTYPE_RE.search(rec.path))}
    coherence = 1.0 if len(written) <= 1 else max(0.2, 1.0 - 0.3 * (len(written) - 1))
    if len(doctypes) > 6:
        coherence = min(coherence, 0.6)
    split_after = None
    if len(written) > 1:
        first_written = next((i for i, rec in enumerate(records)
                              if rec.method in {"POST", "PUT", "PATCH", "DELETE"}), None)
        if first_written is not None and first_written + 1 < len(records) - 1:
            split_after = first_written
    payload = {"label": label, "coherence": round(coherence, 2),
               "split_after": split_after}
    import json

    return json.dumps(payload)


register_policy("segment_label", _offline_label_policy)


@dataclass
class SegmentResult:
    episodes: list[Episode]
    usage: LLMUsage


def _label_episode(client: LLMClient, records: list[HttpRecord]) -> tuple[dict, LLMUsage]:
    messages = [
        {"role": "system", "content": LABEL_SYSTEM},
        {"role": "user", "content": _render_episode(records)},
    ]
    resp = client.complete(messages, policy="segment_label",
                           policy_context={"records": records})
    try:
        data = resp.json()
    except Exception:
        data = {"label": _guess_label(records), "coherence": 0.5, "split_after": None}
    return data, resp.usage


def segment(records: list[HttpRecord], cfg: Config | None = None,
            client: LLMClient | None = None, refine: bool = True) -> SegmentResult:
    cfg = cfg or get_config()
    client = client or make_client(cfg.models.labeler, cfg.models.provider)
    total_usage = LLMUsage(model=cfg.models.labeler)

    groups = _cut_on_gaps(records, cfg.segment.idle_gap_s)
    groups = [g for g in groups if len(g) >= cfg.segment.min_records]

    episodes: list[Episode] = []
    for gi, group in enumerate(groups):
        if not refine:
            episodes.append(_make_episode(len(episodes), group, _guess_label(group), 1.0))
            continue
        data, usage = _label_episode(client, group)
        total_usage = total_usage + usage
        split_after = data.get("split_after")
        coherence = float(data.get("coherence", 1.0) or 1.0)
        label = str(data.get("label") or _guess_label(group))
        if (isinstance(split_after, int) and 0 <= split_after < len(group) - 1
                and coherence < cfg.segment.coherence_threshold + 0.5):
            left, right = group[:split_after + 1], group[split_after + 1:]
            for part in (left, right):
                if len(part) >= cfg.segment.min_records:
                    sub, usage2 = _label_episode(client, part)
                    total_usage = total_usage + usage2
                    episodes.append(_make_episode(
                        len(episodes), part,
                        str(sub.get("label") or _guess_label(part)),
                        float(sub.get("coherence", 1.0) or 1.0)))
        else:
            episodes.append(_make_episode(len(episodes), group, label, coherence))

    episodes = _merge_adjacent(episodes, cfg.segment.merge_gap_s)
    for idx, ep in enumerate(episodes):
        ep.id = f"ep{idx:04d}"
        for rec in ep.records:
            rec.episode_id = ep.id
    return SegmentResult(episodes=episodes, usage=total_usage)


def _make_episode(idx: int, records: list[HttpRecord], label: str,
                  coherence: float) -> Episode:
    ep = Episode(id=f"ep{idx:04d}", records=list(records), label=label,
                 coherence=coherence)
    for rec in ep.records:
        rec.episode_id = ep.id
    return ep


def _merge_adjacent(episodes: list[Episode], merge_gap_s: float) -> list[Episode]:
    if not episodes:
        return []
    merged = [episodes[0]]
    for ep in episodes[1:]:
        prev = merged[-1]
        same_label = (prev.label or "") == (ep.label or "")
        close = ep.ts_start - prev.ts_end <= merge_gap_s
        if same_label and close:
            prev.records.extend(ep.records)
            prev.coherence = min(prev.coherence or 1.0, ep.coherence or 1.0)
        else:
            merged.append(ep)
    return merged
