"""Import likes from an X data export (`data/like.js`).

The export is the no-credentials path: request your archive from X, wait for
the download, point this at it. What you get is thin -- `like.js` carries only
the post ID, its text, and a permalink. No author, no dates, no quote
structure. Two things make it usable anyway:

  * post IDs are snowflakes, so `created_at` is recoverable from the ID alone;
  * `hydrate` can fill in author and quote structure later from the browser.

File order is the one thing the export does not document. X has shipped it both
ways, so `order` is explicit rather than guessed, and `xlikes stats` after an
import will show you whether the dates line up with what you expect.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .parse import parse_status_url, snowflake_to_datetime

_ASSIGNMENT = re.compile(r"^\s*window\.YTD\.[A-Za-z0-9_]+\.part\d+\s*=\s*", re.MULTILINE)


def _strip_js_wrapper(raw: str) -> str:
    """Drop the `window.YTD.like.part0 = ` prefix that makes the file a .js."""
    match = _ASSIGNMENT.search(raw)
    if match:
        raw = raw[match.end():]
    return raw.strip().rstrip(";").strip()


def load_like_js(path: str | Path) -> list[dict[str, Any]]:
    raw = Path(path).read_text(encoding="utf-8", errors="replace")
    payload = json.loads(_strip_js_wrapper(raw))
    if isinstance(payload, dict):
        payload = [payload]
    return payload


def find_like_files(path: str | Path) -> list[Path]:
    """Accept a like.js, a `data/` folder, or an unzipped archive root."""
    path = Path(path)
    if path.is_file():
        return [path]
    if not path.is_dir():
        raise FileNotFoundError(f"no such path: {path}")
    matches = sorted(
        p for p in path.rglob("like*.js*")
        if p.is_file() and re.fullmatch(r"like(-part\d+)?\.(js|json)", p.name)
    )
    if not matches:
        raise FileNotFoundError(f"no like.js found under {path}")
    return matches


def iter_records(
    path: str | Path,
    *,
    order: str = "newest-first",
    start_seq: int = 0,
) -> Iterator[dict[str, Any]]:
    """Yield like records from an archive, assigning `like_seq` in like order."""
    if order not in {"newest-first", "oldest-first"}:
        raise ValueError("order must be 'newest-first' or 'oldest-first'")

    entries: list[dict[str, Any]] = []
    for file in find_like_files(path):
        entries.extend(load_like_js(file))
    if order == "oldest-first":
        entries.reverse()

    now = datetime.now(timezone.utc).isoformat()
    seq = start_seq
    for entry in entries:
        like = entry.get("like") if isinstance(entry, dict) else None
        if not isinstance(like, dict):
            continue
        tweet_id = like.get("tweetId") or like.get("tweet_id")
        expanded = like.get("expandedUrl") or like.get("expanded_url") or ""
        handle = None
        if not tweet_id and expanded:
            parsed = parse_status_url(expanded)
            if parsed:
                handle, tweet_id = parsed
        elif expanded:
            parsed = parse_status_url(expanded)
            if parsed:
                handle = parsed[0]
        if not tweet_id:
            continue

        created = snowflake_to_datetime(tweet_id)
        yield {
            "tweet_id": str(tweet_id),
            "like_seq": seq,
            "author_handle": handle,
            "created_at": created.isoformat() if created else None,
            "text": like.get("fullText") or like.get("full_text") or "",
            "url": expanded or f"https://x.com/i/status/{tweet_id}",
            "source": "archive",
            "first_seen": now,
        }
        seq += 1


def import_archive(conn, path: str | Path, *, order: str = "newest-first") -> int:
    """Import an archive into the database. Returns the number of rows written."""
    from . import db

    return db.upsert_many(conn, iter_records(path, order=order))
