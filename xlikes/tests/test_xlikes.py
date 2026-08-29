"""Tests for xlikes: parsing, storage, search, ranking, and the web endpoints."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from xlikes import archive, db, search as search_mod  # noqa: E402
from xlikes.parse import (  # noqa: E402
    extract_cursor,
    parse_created_at,
    parse_status_url,
    parse_timeline,
    snowflake_to_datetime,
)

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def timeline():
    return json.loads((FIXTURES / "likes_timeline.json").read_text())


@pytest.fixture
def conn(timeline):
    c = db.connect(":memory:")
    records = parse_timeline(timeline)
    for seq, rec in enumerate(records):
        rec["like_seq"] = seq
        rec["source"] = "browser"
    db.upsert_many(c, records)
    return c


# --- parsing ---------------------------------------------------------------

def test_parses_each_like_once(timeline):
    records = parse_timeline(timeline)
    ids = [r["tweet_id"] for r in records]
    assert ids == [
        "1900000000000000001",
        "1900000000000000003",
        "1900000000000000004",
    ]
    assert len(ids) == len(set(ids))


def test_quoted_post_is_not_itself_a_like(timeline):
    """The quoted post is nested inside the like; promoting it would invent a like."""
    records = parse_timeline(timeline)
    assert "1899000000000000002" not in {r["tweet_id"] for r in records}
    quote = records[0]
    assert quote["quoted_tweet_id"] == "1899000000000000002"
    assert quote["quoted_author_handle"] == "gwern"
    assert quote["quoted_article_title"] == "The Scaling Hypothesis, Revisited"
    assert quote["is_quote"] == 1


def test_tombstones_and_cursors_are_skipped(timeline):
    records = parse_timeline(timeline)
    assert all(r["text"] for r in records)
    assert extract_cursor(timeline) == "DAABCgABGm-cursor-value"


def test_note_tweet_text_wins_over_truncated_legacy(timeline):
    long_post = next(r for r in parse_timeline(timeline) if r["tweet_id"].endswith("003"))
    assert long_post["text"].endswith("whenever it is present.")
    assert "…" not in long_post["text"]
    assert "https://sqlite.org/fts5.html" in long_post["urls"]


def test_author_read_from_either_shape(timeline):
    records = parse_timeline(timeline)
    assert records[0]["author_handle"] == "patio11"       # core.screen_name
    assert records[1]["author_handle"] == "simonw"        # legacy.screen_name


def test_visibility_wrapper_is_unwrapped(timeline):
    records = parse_timeline(timeline)
    assert records[1]["tweet_id"] == "1900000000000000003"


def test_timeline_order_and_sort_index_preserved(timeline):
    records = parse_timeline(timeline)
    assert [r["sort_index"] for r in records] == [
        "1799999999999999999",
        "1799999999999999998",
        "1799999999999999997",
    ]


def test_fallback_walk_when_entries_are_unrecognisable(timeline):
    """If X renames the entry envelope, the structural sweep still finds posts."""
    stripped = json.loads(
        json.dumps(timeline).replace('"entryId"', '"someNewEnvelopeKey"')
    )
    records = parse_timeline(stripped)
    assert {r["tweet_id"] for r in records} == {
        "1900000000000000001",
        "1900000000000000003",
        "1900000000000000004",
    }
    assert "1899000000000000002" not in {r["tweet_id"] for r in records}


def test_snowflake_dates():
    when = snowflake_to_datetime("1857395430102421584")
    assert when is not None and when.year == 2024
    assert snowflake_to_datetime("not-a-number") is None
    assert snowflake_to_datetime("12345") is None


def test_created_at_normalised():
    assert parse_created_at("Wed Aug 27 14:03:21 +0000 2025") == "2025-08-27T14:03:21+00:00"
    assert parse_created_at("nonsense") is None
    assert parse_created_at(None) is None


def test_status_url_parsing():
    assert parse_status_url("https://x.com/gwern/status/123") == ("gwern", "123")
    assert parse_status_url("https://twitter.com/i/web/status/456") == (None, "456")
    assert parse_status_url("https://example.com") is None


def test_empty_payload_is_not_an_error():
    assert parse_timeline({"data": {}}) == []


# --- storage ---------------------------------------------------------------

def test_upsert_merges_rather_than_replaces():
    c = db.connect(":memory:")
    db.upsert(c, {"tweet_id": "1", "text": "hello", "author_handle": "alice"})
    db.upsert(c, {"tweet_id": "1", "quoted_text": "quoted"})
    row = c.execute("SELECT * FROM likes WHERE tweet_id='1'").fetchone()
    assert row["text"] == "hello"          # not blanked by the second pass
    assert row["author_handle"] == "alice"
    assert row["quoted_text"] == "quoted"  # filled in by it
    assert db.count(c) == 1


def test_fts_follows_updates():
    c = db.connect(":memory:")
    db.upsert(c, {"tweet_id": "1", "text": "original wording"})
    c.commit()
    assert search_mod.search(c, "original")
    db.upsert(c, {"tweet_id": "1", "text": "revised wording"})
    c.commit()
    assert not search_mod.search(c, "original")
    assert search_mod.search(c, "revised")


# --- archive ---------------------------------------------------------------

def test_archive_import_derives_dates_and_handles():
    c = db.connect(":memory:")
    n = archive.import_archive(c, FIXTURES / "like.js")
    assert n == 2
    rows = list(c.execute("SELECT * FROM likes ORDER BY like_seq"))
    assert rows[0]["like_seq"] == 0
    assert rows[0]["created_at"].startswith("2025-")     # from the snowflake ID
    assert rows[0]["author_handle"] is None              # /i/web/ URL carries no handle
    assert rows[1]["author_handle"] == "catmemes"        # this one does


def test_archive_order_flag_reverses_like_sequence():
    c = db.connect(":memory:")
    archive.import_archive(c, FIXTURES / "like.js", order="oldest-first")
    first = c.execute("SELECT tweet_id FROM likes WHERE like_seq=0").fetchone()
    assert first["tweet_id"] == "1899000000000000009"


def test_archive_and_browser_records_coexist(conn):
    archive.import_archive(conn, FIXTURES / "like.js")
    row = conn.execute("SELECT * FROM likes WHERE tweet_id='1900000000000000001'").fetchone()
    assert row["author_handle"] == "patio11"        # archive did not erase it
    assert row["quoted_author_handle"] == "gwern"
    assert db.count(conn) == 4                      # one new id from the archive


# --- search ----------------------------------------------------------------

def test_full_text_search_ands_bare_words(conn):
    assert len(search_mod.search(conn, "article really good")) == 1
    assert search_mod.search(conn, "article really good")[0]["author_handle"] == "patio11"
    assert search_mod.search(conn, "article unicorn") == []


def test_search_matches_quoted_text_and_article_title(conn):
    assert search_mod.search(conn, "scaling hypothesis")[0]["tweet_id"].endswith("001")
    assert search_mod.search(conn, "gwern")[0]["tweet_id"].endswith("001")


def test_author_filter_matches_quoted_author_too(conn):
    assert len(search_mod.search(conn, "", author="patio11")) == 1
    assert len(search_mod.search(conn, "", author="@gwern")) == 1
    assert search_mod.search(conn, "", author="nobody") == []


def test_recent_filter_bounds_by_like_position_not_post_date(conn):
    assert len(search_mod.search(conn, "", recent=1)) == 1
    assert len(search_mod.search(conn, "", recent=3)) == 3


def test_boolean_filters(conn):
    assert len(search_mod.search(conn, "", quotes_only=True)) == 1
    assert len(search_mod.search(conn, "", articles_only=True)) == 1
    assert len(search_mod.search(conn, "", media_only=True)) == 1
    assert len(search_mod.search(conn, "", links_only=True)) == 2


def test_relative_and_absolute_dates(conn):
    assert search_mod._parse_date("2025-08-01").startswith("2025-08-01")
    recent = search_mod._parse_date("3w")
    assert datetime.fromisoformat(recent) < datetime.now(timezone.utc)
    assert len(search_mod.search(conn, "", since="2025-08-11")) == 1
    assert len(search_mod.search(conn, "", until="2025-08-10")) == 2


def test_bad_input_is_reported_not_crashed(conn):
    with pytest.raises(search_mod.SearchError):
        search_mod.search(conn, "", sort="sideways")
    with pytest.raises(search_mod.SearchError):
        search_mod.search(conn, "", since="last tuesday")
    with pytest.raises(search_mod.SearchError):
        search_mod.search(conn, 'unbalanced "quote AND (')


def test_fts_operators_pass_through(conn):
    assert search_mod.search(conn, '"really good"')
    assert search_mod.search(conn, "coffee OR sqlite")


def test_empty_query_lists_in_like_order(conn):
    rows = search_mod.search(conn, "", sort="recent")
    assert [r["like_seq"] for r in rows] == [0, 1, 2]


# --- ranked recall ---------------------------------------------------------

def test_find_article_praise_ranks_the_target_first(conn):
    hits = search_mod.find_article_praise(conn)
    assert hits, "expected at least one candidate"
    assert hits[0]["tweet_id"] == "1900000000000000001"
    assert hits[0]["author_handle"] == "patio11"
    assert any("praise in comment" in r for r in hits[0]["reasons"])
    assert any("Article" in r for r in hits[0]["reasons"])


def test_find_excludes_unrelated_likes(conn):
    ids = {h["tweet_id"] for h in search_mod.find_article_praise(conn)}
    assert "1900000000000000004" not in ids   # the cat post


def test_find_respects_recent_window(conn):
    assert search_mod.find_article_praise(conn, recent=1)
    # Bounding to likes *after* the target excludes it.
    conn.execute("UPDATE likes SET like_seq = like_seq + 10 WHERE tweet_id LIKE '%001'")
    conn.commit()
    assert not search_mod.find_article_praise(conn, recent=3)


def test_about_terms_boost_matching_candidates(conn):
    plain = search_mod.find_article_praise(conn)[0]["score"]
    boosted = search_mod.find_article_praise(conn, extra_terms="gwern scaling")[0]["score"]
    assert boosted > plain


def test_praise_scoring_shape():
    quote_praise, reasons = search_mod.score_article_praise(
        {"text": "this article is really good", "is_quote": 1,
         "quoted_article_title": "Something", "urls": []}
    )
    plain, _ = search_mod.score_article_praise(
        {"text": "my cat knocked over the coffee", "is_quote": 0, "urls": []}
    )
    quoted_only, _ = search_mod.score_article_praise(
        {"text": "", "quoted_text": "great piece", "is_quote": 1, "urls": []}
    )
    assert quote_praise > quoted_only > plain
    assert plain == 0.0


def test_praise_matching_is_word_bounded():
    # "great" must not fire on "greatly", nor "read" on "already".
    score, _ = search_mod.score_article_praise(
        {"text": "I have already greatly reduced this", "is_quote": 0, "urls": []}
    )
    assert score == 0.0


# --- stats -----------------------------------------------------------------

def test_stats_summarises(conn):
    info = search_mod.stats(conn)
    assert info["total"] == 3
    assert info["quotes"] == 1
    assert info["articles"] == 1
    assert info["unhydrated"] == 0
    assert {a["author_handle"] for a in info["top_authors"]} == {"patio11", "simonw", "catmemes"}


def test_praise_with_nothing_to_point_at_is_not_a_match():
    """"great weather today" is praise, but not praise *of* anything."""
    c = db.connect(":memory:")
    db.upsert(c, {"tweet_id": "9", "like_seq": 0, "text": "great weather today"})
    c.commit()
    assert search_mod.find_article_praise(c) == []
    assert search_mod.find_article_praise(c, min_score=1.0)   # findable if you ask


def test_row_without_permalink_still_gets_a_link():
    c = db.connect(":memory:")
    db.upsert(c, {"tweet_id": "9", "like_seq": 0, "text": "hi", "author_handle": "amy"})
    c.commit()
    assert search_mod.search(c, "hi")[0]["url"] == "https://x.com/amy/status/9"
    db.upsert(c, {"tweet_id": "10", "like_seq": 1, "text": "yo"})
    c.commit()
    assert search_mod.search(c, "yo")[0]["url"] == "https://x.com/i/status/10"
