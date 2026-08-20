"""Noise rejection.

Raw browser traffic is overwhelmingly assets and heartbeats. This drops
everything that cannot carry task semantics, collapses polling, and keeps
document loads as navigation markers only (segmentation needs them; the
tool inducer ignores them).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from statistics import pstdev
from typing import Iterable

from shadow.capture.schema import HttpRecord

DROP_CONTENT_PREFIXES = ("image/", "font/", "audio/", "video/")
DROP_CONTENT_TYPES = {
    "text/css",
    "application/javascript",
    "text/javascript",
    "application/x-javascript",
    "application/font-woff",
    "application/font-woff2",
    "application/wasm",
    "image/svg+xml",
}
DOCUMENT_CONTENT_TYPES = {"text/html", "application/xhtml+xml"}
ASSET_SUFFIXES = (".js", ".css", ".map", ".png", ".jpg", ".jpeg", ".gif", ".svg",
                  ".ico", ".woff", ".woff2", ".ttf", ".eot", ".webp", ".mp4")
# Frappe/socket.io transport paths carry no task-level semantics.
DROP_PATH_MARKERS = ("/socket.io/", "/assets/", "/files/", "/private/files/",
                     "/.well-known/", "/favicon")


@dataclass
class FilterStats:
    total: int = 0
    kept: int = 0
    dropped_content_type: int = 0
    dropped_asset_path: int = 0
    dropped_websocket: int = 0
    dropped_not_modified: int = 0
    dropped_polling: int = 0
    documents_kept: int = 0
    reasons: dict[str, int] = field(default_factory=dict)

    @property
    def retention(self) -> float:
        return self.kept / self.total if self.total else 0.0

    def render(self) -> str:
        return (
            f"raw flows      : {self.total}\n"
            f"kept           : {self.kept} ({self.retention:.1%})\n"
            f"  of which docs: {self.documents_kept} (navigation markers)\n"
            f"dropped assets : {self.dropped_content_type + self.dropped_asset_path}\n"
            f"dropped ws     : {self.dropped_websocket}\n"
            f"dropped 304    : {self.dropped_not_modified}\n"
            f"collapsed poll : {self.dropped_polling}\n"
        )


def _is_document(rec: HttpRecord) -> bool:
    return rec.content_type in DOCUMENT_CONTENT_TYPES


def _is_asset(rec: HttpRecord) -> bool:
    if rec.content_type.startswith(DROP_CONTENT_PREFIXES):
        return True
    if rec.content_type in DROP_CONTENT_TYPES:
        return True
    path = rec.path.lower()
    if path.endswith(ASSET_SUFFIXES):
        return True
    return any(marker in path for marker in DROP_PATH_MARKERS)


def _poll_key(rec: HttpRecord) -> tuple:
    return (rec.method, rec.path, tuple(sorted((k, str(v)) for k, v in rec.query.items())))


def _body_fingerprint(body) -> str:
    try:
        import json

        return json.dumps(body, sort_keys=True, default=str)[:4096]
    except Exception:
        return str(body)[:4096]


def collapse_polling(records: list[HttpRecord], min_repeats: int = 3,
                     interval_tolerance: float = 0.45) -> tuple[list[HttpRecord], int]:
    """Collapse near-periodic identical requests with unchanged responses.

    A heartbeat is: same (method, path, query), >= `min_repeats` occurrences,
    low relative variance in inter-arrival time, and one distinct response
    fingerprint. Only the first occurrence survives, flagged `is_polling`.
    """
    groups: dict[tuple, list[int]] = {}
    for i, rec in enumerate(records):
        groups.setdefault(_poll_key(rec), []).append(i)

    drop: set[int] = set()
    collapsed = 0
    for _key, idxs in groups.items():
        if len(idxs) < min_repeats:
            continue
        times = [records[i].ts_start for i in idxs]
        gaps = [b - a for a, b in zip(times, times[1:])]
        if not gaps:
            continue
        mean_gap = sum(gaps) / len(gaps)
        if mean_gap <= 0:
            continue
        jitter = pstdev(gaps) / mean_gap if len(gaps) > 1 else 0.0
        fingerprints = {_body_fingerprint(records[i].resp_body) for i in idxs}
        if jitter <= interval_tolerance and len(fingerprints) == 1:
            keep = idxs[0]
            records[keep].is_polling = True
            records[keep].collapsed_count = len(idxs)
            for i in idxs[1:]:
                drop.add(i)
            collapsed += len(idxs) - 1

    kept = [r for i, r in enumerate(records) if i not in drop]
    return kept, collapsed


def filter_records(records: Iterable[HttpRecord]) -> tuple[list[HttpRecord], FilterStats]:
    stats = FilterStats()
    staged: list[HttpRecord] = []

    for rec in records:
        stats.total += 1
        if rec.status == 304:
            stats.dropped_not_modified += 1
            continue
        if rec.method == "CONNECT" or rec.status == 101:
            stats.dropped_websocket += 1
            continue
        if "/socket.io/" in rec.path:
            stats.dropped_websocket += 1
            continue
        if _is_document(rec):
            # Kept as a navigation marker only; body was already dropped at capture.
            rec.is_document = True
            rec.resp_body = None
            staged.append(rec)
            stats.documents_kept += 1
            continue
        if _is_asset(rec):
            stats.dropped_content_type += 1
            continue
        staged.append(rec)

    staged.sort(key=lambda r: r.ts_start)
    kept, collapsed = collapse_polling(staged)
    stats.dropped_polling = collapsed
    stats.kept = len(kept)
    return kept, stats


def summarize(records: list[HttpRecord], limit: int = 60) -> str:
    """Human-readable view of the filtered stream (the M1 done-check)."""
    lines = []
    for rec in records[:limit]:
        marker = "DOC " if rec.is_document else ("POLL" if rec.is_polling else "    ")
        q = ""
        if rec.query:
            first = list(rec.query.items())[:2]
            q = "?" + "&".join(f"{k}={v}" for k, v in first)
        lines.append(f"{marker} {rec.method:5s} {rec.status} {rec.path}{q}")
    if len(records) > limit:
        lines.append(f"... {len(records) - limit} more")
    return "\n".join(lines)
