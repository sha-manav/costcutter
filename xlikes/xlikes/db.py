"""SQLite storage for liked posts, with an FTS5 index over the searchable text."""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any, Iterable

DEFAULT_DB = Path(os.environ.get("XLIKES_HOME", Path.home() / ".xlikes")) / "likes.db"

# Columns that make up a stored like. `like_seq` is position in the likes
# timeline (0 = most recently liked); it is the only signal we have for *when
# something was liked*, because X exposes no like timestamp anywhere.
COLUMNS = [
    "tweet_id",
    "like_seq",
    "sort_index",
    "author_handle",
    "author_name",
    "author_id",
    "created_at",
    "text",
    "lang",
    "is_quote",
    "is_retweet",
    "is_reply",
    "reply_to_handle",
    "quoted_tweet_id",
    "quoted_author_handle",
    "quoted_author_name",
    "quoted_text",
    "quoted_created_at",
    "article_title",
    "quoted_article_title",
    "urls",
    "media",
    "favorite_count",
    "retweet_count",
    "reply_count",
    "quote_count",
    "url",
    "source",
    "first_seen",
]

SCHEMA = """
CREATE TABLE IF NOT EXISTS likes (
    tweet_id             TEXT PRIMARY KEY,
    like_seq             INTEGER,
    sort_index           TEXT,
    author_handle        TEXT,
    author_name          TEXT,
    author_id            TEXT,
    created_at           TEXT,
    text                 TEXT,
    lang                 TEXT,
    is_quote             INTEGER DEFAULT 0,
    is_retweet           INTEGER DEFAULT 0,
    is_reply             INTEGER DEFAULT 0,
    reply_to_handle      TEXT,
    quoted_tweet_id      TEXT,
    quoted_author_handle TEXT,
    quoted_author_name   TEXT,
    quoted_text          TEXT,
    quoted_created_at    TEXT,
    article_title        TEXT,
    quoted_article_title TEXT,
    urls                 TEXT,
    media                TEXT,
    favorite_count       INTEGER,
    retweet_count        INTEGER,
    reply_count          INTEGER,
    quote_count          INTEGER,
    url                  TEXT,
    source               TEXT,
    first_seen           TEXT
);

CREATE INDEX IF NOT EXISTS likes_seq_idx     ON likes(like_seq);
CREATE INDEX IF NOT EXISTS likes_created_idx ON likes(created_at);
CREATE INDEX IF NOT EXISTS likes_author_idx  ON likes(author_handle);

CREATE VIRTUAL TABLE IF NOT EXISTS likes_fts USING fts5(
    text,
    author_handle,
    author_name,
    quoted_text,
    quoted_author_handle,
    quoted_author_name,
    article_title,
    quoted_article_title,
    urls,
    content='likes',
    content_rowid='rowid',
    tokenize='unicode61 remove_diacritics 2'
);

CREATE TRIGGER IF NOT EXISTS likes_ai AFTER INSERT ON likes BEGIN
    INSERT INTO likes_fts(rowid, text, author_handle, author_name, quoted_text,
                          quoted_author_handle, quoted_author_name, article_title,
                          quoted_article_title, urls)
    VALUES (new.rowid, new.text, new.author_handle, new.author_name, new.quoted_text,
            new.quoted_author_handle, new.quoted_author_name, new.article_title,
            new.quoted_article_title, new.urls);
END;

CREATE TRIGGER IF NOT EXISTS likes_ad AFTER DELETE ON likes BEGIN
    INSERT INTO likes_fts(likes_fts, rowid, text, author_handle, author_name, quoted_text,
                          quoted_author_handle, quoted_author_name, article_title,
                          quoted_article_title, urls)
    VALUES ('delete', old.rowid, old.text, old.author_handle, old.author_name, old.quoted_text,
            old.quoted_author_handle, old.quoted_author_name, old.article_title,
            old.quoted_article_title, old.urls);
END;

CREATE TRIGGER IF NOT EXISTS likes_au AFTER UPDATE ON likes BEGIN
    INSERT INTO likes_fts(likes_fts, rowid, text, author_handle, author_name, quoted_text,
                          quoted_author_handle, quoted_author_name, article_title,
                          quoted_article_title, urls)
    VALUES ('delete', old.rowid, old.text, old.author_handle, old.author_name, old.quoted_text,
            old.quoted_author_handle, old.quoted_author_name, old.article_title,
            old.quoted_article_title, old.urls);
    INSERT INTO likes_fts(rowid, text, author_handle, author_name, quoted_text,
                          quoted_author_handle, quoted_author_name, article_title,
                          quoted_article_title, urls)
    VALUES (new.rowid, new.text, new.author_handle, new.author_name, new.quoted_text,
            new.quoted_author_handle, new.quoted_author_name, new.article_title,
            new.quoted_article_title, new.urls);
END;

CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""


def connect(path: str | Path | None = None) -> sqlite3.Connection:
    """Open (creating if needed) the likes database."""
    db_path = Path(path) if path else DEFAULT_DB
    if str(db_path) != ":memory:":
        db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    return conn


def _encode(value: Any) -> Any:
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, bool):
        return int(value)
    return value


def upsert(conn: sqlite3.Connection, record: dict[str, Any]) -> None:
    """Insert or merge one like.

    Merge rather than replace: a later pass (e.g. `hydrate`) fills in fields the
    first pass could not see, and must not blank out fields it does not know.
    A NULL or empty incoming value leaves the stored one alone.
    """
    row = {k: _encode(record.get(k)) for k in COLUMNS if k in record}
    row["tweet_id"] = str(record["tweet_id"])

    cols = list(row)
    placeholders = ", ".join("?" for _ in cols)
    updates = ", ".join(
        f"{c}=COALESCE(NULLIF(excluded.{c}, ''), {c})" for c in cols if c != "tweet_id"
    )
    sql = (
        f"INSERT INTO likes ({', '.join(cols)}) VALUES ({placeholders}) "
        f"ON CONFLICT(tweet_id) DO UPDATE SET {updates}"
    )
    conn.execute(sql, [row[c] for c in cols])


def upsert_many(conn: sqlite3.Connection, records: Iterable[dict[str, Any]]) -> int:
    n = 0
    for rec in records:
        upsert(conn, rec)
        n += 1
    conn.commit()
    return n


def get_meta(conn: sqlite3.Connection, key: str, default: str | None = None) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO meta(key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, str(value)),
    )
    conn.commit()


def count(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) AS n FROM likes").fetchone()["n"]


def rebuild_fts(conn: sqlite3.Connection) -> None:
    conn.execute("INSERT INTO likes_fts(likes_fts) VALUES('rebuild')")
    conn.commit()
