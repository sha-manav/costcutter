"""Query the likes database: full-text search, filters, and a ranked recall mode."""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

# Words people actually use when they repost something and say it is good.
# Multi-word entries are matched as phrases.
PRAISE_TERMS = [
    "really good", "very good", "so good", "this is good", "quite good",
    "great", "excellent", "fantastic", "brilliant", "superb", "terrific",
    "outstanding", "phenomenal", "incredible", "amazing", "wonderful",
    "must read", "must-read", "worth reading", "worth your time", "worth a read",
    "highly recommend", "recommend", "recommended",
    "best thing", "one of the best", "banger", "gem", "masterpiece",
    "insightful", "thoughtful", "thorough", "sharp", "lucid", "clear-eyed",
    "nails it", "nailed it", "spot on", "spot-on", "dead on",
    "loved this", "love this", "enjoyed this", "fascinating", "excellent read",
    "top notch", "top-notch", "superbly", "beautifully written", "well written",
    "well-written", "essential", "important piece", "a joy", "delightful",
    "read this", "go read", "everyone should read", "required reading",
]

# Words that mark the thing being praised as a piece of writing.
ARTICLE_TERMS = [
    "article", "piece", "essay", "post", "writeup", "write-up", "write up",
    "blog", "blogpost", "blog post", "newsletter", "paper", "report",
    "story", "read", "series", "deep dive", "deep-dive", "profile", "column",
]

SORTS = {
    "recent": "COALESCE(l.like_seq, 1e18) ASC, l.created_at DESC",
    "oldest": "COALESCE(l.like_seq, -1) DESC, l.created_at ASC",
    "posted": "l.created_at DESC",
    "likes": "l.favorite_count DESC",
    "relevance": "rank ASC",
}


class SearchError(ValueError):
    """A query the user can fix -- bad FTS syntax, unknown sort."""


def _fts_query(text: str) -> str:
    """Turn user input into an FTS5 MATCH expression.

    Bare words are ANDed; anything already using FTS operators (quotes, OR,
    NEAR, prefix *) is passed through so power users keep the full syntax.
    """
    text = text.strip()
    if not text:
        return ""
    if re.search(r'["*():]|\b(OR|AND|NOT|NEAR)\b', text):
        return text
    tokens = re.findall(r"[\w#@]+", text, flags=re.UNICODE)
    return " AND ".join(f'"{t}"' for t in tokens)


def _iso_days_ago(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def _parse_date(value: str, *, end_of_day: bool = False) -> str:
    """Accept `2025-08-01`, a full ISO timestamp, or `21d` / `3w` / `6m`.

    A bare date used as an upper bound covers the whole day. Without this,
    `--until 2025-08-10` silently drops everything posted on the 10th, which is
    never what someone naming that date means.
    """
    value = value.strip()
    rel = re.fullmatch(r"(\d+)\s*([dwmy])", value, flags=re.I)
    if rel:
        n, unit = int(rel.group(1)), rel.group(2).lower()
        return _iso_days_ago(n * {"d": 1, "w": 7, "m": 30, "y": 365}[unit])
    bare_date = re.fullmatch(r"\d{4}-\d{2}-\d{2}", value) is not None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise SearchError(f"could not read date {value!r}; use YYYY-MM-DD or 3w") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    if end_of_day and bare_date:
        parsed = parsed.replace(hour=23, minute=59, second=59, microsecond=999999)
    return parsed.isoformat()


def search(
    conn: sqlite3.Connection,
    query: str = "",
    *,
    since: str | None = None,
    until: str | None = None,
    author: str | None = None,
    quotes_only: bool = False,
    links_only: bool = False,
    media_only: bool = False,
    articles_only: bool = False,
    recent: int | None = None,
    sort: str = "relevance",
    limit: int = 25,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """Search likes.

    `recent` restricts to the N most recently liked posts -- which is a
    different question from `since`. `since` filters on when the post was
    *written*; `recent` filters on when *you liked it*. Someone hunting for
    "a thing I liked in the last three weeks" almost always wants `recent`,
    because a post written in 2019 can be liked today.
    """
    if sort not in SORTS:
        raise SearchError(f"unknown sort {sort!r}; choose from {', '.join(SORTS)}")

    where: list[str] = []
    params: list[Any] = []
    match = _fts_query(query)

    if match:
        table = "likes_fts f JOIN likes l ON l.rowid = f.rowid"
        where.append("likes_fts MATCH ?")
        params.append(match)
    else:
        table = "likes l"
        if sort == "relevance":
            sort = "recent"

    if since:
        where.append("l.created_at >= ?")
        params.append(_parse_date(since))
    if until:
        where.append("l.created_at <= ?")
        params.append(_parse_date(until, end_of_day=True))
    if author:
        handle = author.lstrip("@").lower()
        where.append("(LOWER(l.author_handle) = ? OR LOWER(l.quoted_author_handle) = ?)")
        params += [handle, handle]
    if quotes_only:
        where.append("l.is_quote = 1")
    if links_only:
        where.append("l.urls IS NOT NULL AND l.urls != '' AND l.urls != '[]'")
    if media_only:
        where.append("l.media IS NOT NULL AND l.media != '' AND l.media != '[]'")
    if articles_only:
        where.append("(l.article_title IS NOT NULL OR l.quoted_article_title IS NOT NULL)")
    if recent is not None:
        where.append("l.like_seq IS NOT NULL AND l.like_seq < ?")
        params.append(int(recent))

    sql = f"SELECT l.* FROM {table}"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += f" ORDER BY {SORTS[sort]} LIMIT ? OFFSET ?"
    params += [int(limit), int(offset)]

    try:
        rows = conn.execute(sql, params).fetchall()
    except sqlite3.OperationalError as exc:
        raise SearchError(f"bad query: {exc}") from exc
    return [_hydrate_row(r) for r in rows]


def _hydrate_row(row: sqlite3.Row) -> dict[str, Any]:
    rec = dict(row)
    for field in ("urls", "media"):
        raw = rec.get(field)
        if isinstance(raw, str) and raw:
            try:
                rec[field] = json.loads(raw)
            except json.JSONDecodeError:
                rec[field] = []
        else:
            rec[field] = []
    if not rec.get("url") and rec.get("tweet_id"):
        # /i/status/<id> resolves without knowing the handle, so a row that was
        # stored without a permalink is still one click from the real post.
        handle = rec.get("author_handle") or "i"
        rec["url"] = f"https://x.com/{handle}/status/{rec['tweet_id']}"
    return rec


def _count_terms(haystack: str, terms: list[str]) -> list[str]:
    hits = []
    for term in terms:
        pattern = r"\b" + re.escape(term).replace(r"\ ", r"\s+") + r"\b"
        if re.search(pattern, haystack, flags=re.I):
            hits.append(term)
    return hits


def score_article_praise(rec: dict[str, Any]) -> tuple[float, list[str]]:
    """Score how much a like looks like "reposted someone's article, said it was good".

    The commentary is what carries the praise, so praise found in the user's own
    added text counts for more than praise anywhere in the quoted post.
    """
    comment = (rec.get("text") or "")
    quoted = (rec.get("quoted_text") or "")
    titles = " ".join(filter(None, [rec.get("article_title"), rec.get("quoted_article_title")]))

    reasons: list[str] = []
    score = 0.0

    praise = _count_terms(comment, PRAISE_TERMS)
    if praise:
        score += 3.0 + 0.5 * (len(praise) - 1)
        reasons.append("praise in comment: " + ", ".join(praise[:3]))
    elif _count_terms(quoted, PRAISE_TERMS):
        score += 0.5
        reasons.append("praise in quoted post")

    article_words = _count_terms(comment + " " + titles, ARTICLE_TERMS)
    if article_words:
        score += 2.0
        reasons.append("mentions " + "/".join(sorted(set(article_words))[:3]))

    if rec.get("quoted_article_title") or rec.get("article_title"):
        score += 3.0
        reasons.append("attached X Article")

    if rec.get("is_quote"):
        score += 2.0
        reasons.append("quote repost")
    elif rec.get("is_retweet"):
        score += 0.5

    # A quote whose commentary is short and appreciative is the classic shape.
    if rec.get("is_quote") and 0 < len(comment) <= 160 and praise:
        score += 1.5
        reasons.append("short appreciative comment")

    external = [u for u in (rec.get("urls") or []) if not re.match(r"https?://(www\.)?(x|twitter)\.com/", u)]
    if external:
        score += 1.0
        reasons.append("links out to " + re.sub(r"^https?://(www\.)?", "", external[0])[:40])

    return score, reasons


def find_article_praise(
    conn: sqlite3.Connection,
    *,
    recent: int | None = None,
    since: str | None = None,
    limit: int = 20,
    min_score: float = 4.0,
    extra_terms: str = "",
) -> list[dict[str, Any]]:
    """Rank likes by how well they match "someone reposted an article and praised it".

    Scans candidates rather than relying on FTS, because the phrasing varies too
    much for a keyword query -- "this is superb" and "required reading" share no
    words but are the same gesture.
    """
    where = ["1=1"]
    params: list[Any] = []
    if recent is not None:
        where.append("like_seq IS NOT NULL AND like_seq < ?")
        params.append(int(recent))
    if since:
        where.append("created_at >= ?")
        params.append(_parse_date(since))

    sql = (
        "SELECT * FROM likes WHERE " + " AND ".join(where) +
        " ORDER BY COALESCE(like_seq, 1e18) ASC"
    )
    rows = [_hydrate_row(r) for r in conn.execute(sql, params).fetchall()]

    extra = [t.strip().lower() for t in re.split(r"[,\s]+", extra_terms) if t.strip()]
    scored = []
    for rec in rows:
        score, reasons = score_article_praise(rec)
        if extra:
            blob = " ".join(
                filter(None, [rec.get("text"), rec.get("quoted_text"),
                              rec.get("author_handle"), rec.get("quoted_author_handle"),
                              rec.get("quoted_article_title"), " ".join(rec.get("urls") or [])])
            ).lower()
            matched = [t for t in extra if t in blob]
            if matched:
                score += 2.0 * len(matched)
                reasons.append("matches " + ", ".join(matched))
        if score >= min_score:
            rec["score"] = round(score, 2)
            rec["reasons"] = reasons
            scored.append(rec)

    scored.sort(key=lambda r: (-r["score"], r.get("like_seq") if r.get("like_seq") is not None else 10**18))
    return scored[:limit]


def stats(conn: sqlite3.Connection) -> dict[str, Any]:
    row = conn.execute(
        """
        SELECT COUNT(*) AS total,
               SUM(is_quote) AS quotes,
               SUM(CASE WHEN author_handle IS NULL OR author_handle = '' THEN 1 ELSE 0 END) AS unhydrated,
               SUM(CASE WHEN article_title IS NOT NULL OR quoted_article_title IS NOT NULL
                        THEN 1 ELSE 0 END) AS articles,
               MIN(created_at) AS oldest,
               MAX(created_at) AS newest
        FROM likes
        """
    ).fetchone()
    top = conn.execute(
        "SELECT author_handle, COUNT(*) AS n FROM likes "
        "WHERE author_handle IS NOT NULL AND author_handle != '' "
        "GROUP BY author_handle ORDER BY n DESC LIMIT 10"
    ).fetchall()
    out = dict(row)
    out["top_authors"] = [dict(r) for r in top]
    return out
