"""Endpoint identity and task-independent "chrome" detection.

Shared by provenance and induction, so both agree on what counts as an
endpoint and which endpoints carry no task signal.
"""
from __future__ import annotations

import re
from collections import Counter

from shadow.capture.schema import Episode, HttpRecord

ID_SEGMENT = re.compile(
    r"""^(
        [A-Z]{2,8}(-[A-Za-z]{2,8})*-\d{2,4}-\d{3,6}      # SAL-ORD-2026-00001
        | [0-9a-f]{12,}                                   # hashes / uuids
        | \d{3,}                                          # long numerics
        | .*@.*\..*                                       # e-mail style keys
    )$""",
    re.VERBOSE,
)

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
    re.compile(r"/api/method/frappe\.desk\.doctype\.route_history"),
)


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


def endpoint_key(rec: HttpRecord) -> str:
    return f"{rec.method} {normalize_path(rec.path)[0]}"


def chrome_endpoints(episodes: list[Episode], threshold: float) -> set[str]:
    """Endpoints the app issues regardless of what the user is doing.

    Found the way stop words are: by document frequency. An endpoint present
    in almost every episode carries no task signal. Two consequences follow —
    it is dropped from task signatures, and its response is not offered as a
    provenance source, because a page-metadata blob will spuriously "explain"
    half the values in the episode that follows it.
    """
    if not episodes:
        return set()
    seen: Counter[str] = Counter()
    for ep in episodes:
        for key in {endpoint_key(r) for r in ep.records if not is_noise(r)}:
            seen[key] += 1
    return {key for key, n in seen.items() if n / len(episodes) >= threshold}
