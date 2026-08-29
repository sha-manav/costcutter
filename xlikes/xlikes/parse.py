"""Parse X's GraphQL timeline JSON into like records.

Design note: X reshuffles the wrapper objects around its timeline payloads
regularly (`timeline_v2` vs `timeline`, added visibility wrappers, new module
types). Hardcoding a path like
`data.user.result.timeline_v2.timeline.instructions[...]` breaks every time
they do. So this parser is path-agnostic: it walks the whole document looking
for things that *look like* tweets, and uses structure -- not location -- to
decide what each one is.

The one thing it must get right is nesting. A quoted or retweeted post is a
tweet object nested inside another tweet object; it is not itself a like. So
the walk tracks which keys it descended through and refuses to promote a tweet
found under `quoted_status_result`, `retweeted_status_result` or `parent`.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Iterator

# Keys whose subtrees hold a post *attached to* another post, never a like.
NESTED_KEYS = {
    "quoted_status_result",
    "retweeted_status_result",
    "parent",
    "in_reply_to_status_result",
}

# X's snowflake epoch: 2010-11-04T01:42:54.657Z in ms.
SNOWFLAKE_EPOCH_MS = 1288834974657

_CREATED_AT = "%a %b %d %H:%M:%S %z %Y"


def snowflake_to_datetime(tweet_id: str | int) -> datetime | None:
    """Recover a post's creation time from its ID.

    X's IDs are snowflakes with a millisecond timestamp in the high bits. This
    is what makes a data-archive import useful: `like.js` carries no dates at
    all, but every ID in it is self-dating.
    """
    try:
        value = int(tweet_id)
    except (TypeError, ValueError):
        return None
    if value < 1 << 22:  # pre-snowflake sequential ID (2010 and earlier)
        return None
    ms = (value >> 22) + SNOWFLAKE_EPOCH_MS
    try:
        return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


def parse_created_at(value: str | None) -> str | None:
    """Normalise X's `created_at` ("Wed Aug 27 14:03:21 +0000 2025") to ISO-8601."""
    if not value:
        return None
    try:
        return datetime.strptime(value, _CREATED_AT).astimezone(timezone.utc).isoformat()
    except ValueError:
        return None


def _unwrap(node: Any) -> Any:
    """Strip visibility wrappers that hide the real tweet one level down."""
    seen = 0
    while (
        isinstance(node, dict)
        and node.get("__typename") in {"TweetWithVisibilityResults", "TimelineTweet"}
        and isinstance(node.get("tweet"), dict)
        and seen < 8
    ):
        node = node["tweet"]
        seen += 1
    if isinstance(node, dict) and isinstance(node.get("result"), dict):
        inner = node["result"]
        if _looks_like_tweet(inner) or inner.get("__typename") == "TweetWithVisibilityResults":
            return _unwrap(inner)
    return node


def _looks_like_tweet(node: Any) -> bool:
    if not isinstance(node, dict):
        return False
    if node.get("__typename") in {"TweetTombstone", "TweetUnavailable"}:
        return False
    legacy = node.get("legacy")
    if not isinstance(legacy, dict):
        return False
    has_id = bool(node.get("rest_id") or legacy.get("id_str"))
    has_text = "full_text" in legacy or "text" in legacy
    return has_id and has_text


def _tweet_id(node: dict) -> str | None:
    tid = node.get("rest_id") or node.get("legacy", {}).get("id_str")
    return str(tid) if tid else None


def _author(node: dict) -> dict[str, str | None]:
    """Pull the author out of `core.user_results.result`.

    X moved handle/name from `user.legacy` up to `user.core` partway through
    2024 and still serves both shapes, so read either.
    """
    user = _dig(node, "core", "user_results", "result") or {}
    if not isinstance(user, dict):
        user = {}
    legacy = user.get("legacy") if isinstance(user.get("legacy"), dict) else {}
    core = user.get("core") if isinstance(user.get("core"), dict) else {}
    return {
        "author_id": str(user.get("rest_id")) if user.get("rest_id") else None,
        "author_handle": core.get("screen_name") or legacy.get("screen_name"),
        "author_name": core.get("name") or legacy.get("name"),
    }


def _dig(node: Any, *keys: str) -> Any:
    for key in keys:
        if not isinstance(node, dict):
            return None
        node = node.get(key)
    return node


def _full_text(node: dict) -> str:
    """Prefer the note_tweet body, which holds the untruncated text of long posts."""
    note = _dig(node, "note_tweet", "note_tweet_results", "result", "text")
    if isinstance(note, str) and note:
        return note
    legacy = node.get("legacy", {})
    return legacy.get("full_text") or legacy.get("text") or ""


def _article_title(node: dict) -> str | None:
    """Title of an attached X Article (the long-form post type)."""
    for path in (
        ("article", "article_results", "result", "title"),
        ("article", "article_results", "result", "metadata", "title"),
        ("article", "title"),
    ):
        title = _dig(node, *path)
        if isinstance(title, str) and title:
            return title
    return None


def _urls(node: dict) -> list[str]:
    """External links, with X's t.co shorteners resolved to their expanded form."""
    out: list[str] = []
    legacy = node.get("legacy", {})
    for container in (legacy.get("entities"), node.get("note_tweet_entities"),
                      _dig(node, "note_tweet", "note_tweet_results", "result", "entity_set")):
        if not isinstance(container, dict):
            continue
        for item in container.get("urls") or []:
            if isinstance(item, dict):
                url = item.get("expanded_url") or item.get("url")
                if url and url not in out:
                    out.append(url)
    card_url = _dig(node, "card", "legacy", "url")
    if isinstance(card_url, str) and card_url.startswith("http") and card_url not in out:
        out.append(card_url)
    return out


def _media(node: dict) -> list[dict]:
    out = []
    legacy = node.get("legacy", {})
    entities = legacy.get("extended_entities") or legacy.get("entities") or {}
    for item in (entities.get("media") or []) if isinstance(entities, dict) else []:
        if isinstance(item, dict):
            out.append({"type": item.get("type"), "url": item.get("media_url_https")})
    return out


def _int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def tweet_to_record(node: dict) -> dict[str, Any] | None:
    """Convert one unwrapped tweet result into a flat like record."""
    node = _unwrap(node)
    if not _looks_like_tweet(node):
        return None
    tweet_id = _tweet_id(node)
    if not tweet_id:
        return None

    legacy = node.get("legacy", {})
    author = _author(node)
    created = parse_created_at(legacy.get("created_at"))
    if not created:
        derived = snowflake_to_datetime(tweet_id)
        created = derived.isoformat() if derived else None

    quoted = _unwrap(_dig(node, "quoted_status_result", "result") or {})
    quoted_ok = _looks_like_tweet(quoted)
    quoted_author = _author(quoted) if quoted_ok else {}

    retweeted = _unwrap(_dig(legacy, "retweeted_status_result", "result") or {})
    is_retweet = _looks_like_tweet(retweeted)

    handle = author["author_handle"]
    record = {
        "tweet_id": tweet_id,
        "author_id": author["author_id"],
        "author_handle": handle,
        "author_name": author["author_name"],
        "created_at": created,
        "text": _full_text(node),
        "lang": legacy.get("lang"),
        "is_quote": int(bool(legacy.get("is_quote_status")) or quoted_ok),
        "is_retweet": int(is_retweet),
        "is_reply": int(bool(legacy.get("in_reply_to_status_id_str"))),
        "reply_to_handle": legacy.get("in_reply_to_screen_name"),
        "quoted_tweet_id": _tweet_id(quoted) if quoted_ok else legacy.get("quoted_status_id_str"),
        "quoted_author_handle": quoted_author.get("author_handle"),
        "quoted_author_name": quoted_author.get("author_name"),
        "quoted_text": _full_text(quoted) if quoted_ok else None,
        "quoted_created_at": parse_created_at(quoted.get("legacy", {}).get("created_at"))
        if quoted_ok
        else None,
        "article_title": _article_title(node),
        "quoted_article_title": _article_title(quoted) if quoted_ok else None,
        "urls": _urls(node) + (_urls(quoted) if quoted_ok else []),
        "media": _media(node),
        "favorite_count": _int(legacy.get("favorite_count")),
        "retweet_count": _int(legacy.get("retweet_count")),
        "reply_count": _int(legacy.get("reply_count")),
        "quote_count": _int(legacy.get("quote_count")),
        "url": f"https://x.com/{handle or 'i'}/status/{tweet_id}",
    }
    return record


def _walk(node: Any, path: tuple[str, ...] = ()) -> Iterator[tuple[tuple[str, ...], dict]]:
    """Yield every dict in the document along with the key path taken to reach it."""
    if isinstance(node, dict):
        yield path, node
        for key, value in node.items():
            yield from _walk(value, path + (key,))
    elif isinstance(node, list):
        for value in node:
            yield from _walk(value, path)


def _is_nested(path: tuple[str, ...]) -> bool:
    return any(key in NESTED_KEYS for key in path)


def iter_timeline_entries(doc: Any) -> Iterator[dict]:
    """Yield timeline entry objects (those carrying an `entryId`), in document order."""
    for _, node in _walk(doc):
        if isinstance(node.get("entryId"), str) and "content" in node:
            yield node


def parse_timeline(doc: Any) -> list[dict[str, Any]]:
    """Extract like records from one GraphQL timeline response, in timeline order.

    Prefers walking timeline entries, which preserves X's own ordering and gives
    us `sortIndex`. Falls back to a bare structural sweep when the payload has no
    recognisable entries -- that keeps the parser working if X renames the
    entry envelope.
    """
    if isinstance(doc, (str, bytes)):
        doc = json.loads(doc)

    records: list[dict[str, Any]] = []
    seen: set[str] = set()

    for entry in iter_timeline_entries(doc):
        entry_id = entry.get("entryId", "")
        if entry_id.startswith(("cursor-", "messageprompt-")):
            continue
        sort_index = entry.get("sortIndex")
        for path, node in _walk(entry.get("content")):
            if _is_nested(path) or not _looks_like_tweet(_unwrap(node)):
                continue
            record = tweet_to_record(node)
            if not record or record["tweet_id"] in seen:
                continue
            seen.add(record["tweet_id"])
            record["sort_index"] = str(sort_index) if sort_index is not None else None
            records.append(record)

    if records:
        return records

    for path, node in _walk(doc):
        if _is_nested(path) or not _looks_like_tweet(_unwrap(node)):
            continue
        record = tweet_to_record(node)
        if not record or record["tweet_id"] in seen:
            continue
        seen.add(record["tweet_id"])
        record["sort_index"] = None
        records.append(record)
    return records


def extract_cursor(doc: Any) -> str | None:
    """Return the bottom pagination cursor, if the payload carries one."""
    if isinstance(doc, (str, bytes)):
        doc = json.loads(doc)
    for _, node in _walk(doc):
        if node.get("cursorType") == "Bottom" and isinstance(node.get("value"), str):
            return node["value"]
    return None


_HANDLE_IN_URL = re.compile(r"https?://(?:www\.)?(?:x|twitter)\.com/([A-Za-z0-9_]{1,15})/status/(\d+)")


def parse_status_url(url: str) -> tuple[str | None, str] | None:
    """Split a post permalink into (handle, tweet_id). Handle is None for /i/web/ URLs."""
    match = _HANDLE_IN_URL.search(url or "")
    if match:
        handle = match.group(1)
        return (None if handle == "i" else handle, match.group(2))
    generic = re.search(r"/status(?:es)?/(\d+)", url or "")
    return (None, generic.group(1)) if generic else None


def within_days(iso_timestamp: str | None, days: int, now: datetime | None = None) -> bool:
    if not iso_timestamp:
        return False
    try:
        when = datetime.fromisoformat(iso_timestamp)
    except ValueError:
        return False
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    now = now or datetime.now(timezone.utc)
    return when >= now - timedelta(days=days)
