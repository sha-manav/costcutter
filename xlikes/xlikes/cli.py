"""Command line interface for xlikes."""

from __future__ import annotations

import argparse
import json
import os
import sys
import textwrap
from pathlib import Path

from . import db, search as search_mod
from .archive import import_archive

WIDTH = min(int(os.environ.get("COLUMNS", 100)), 100)


def _c(code: str, text: str) -> str:
    if not sys.stdout.isatty() or os.environ.get("NO_COLOR"):
        return text
    return f"\033[{code}m{text}\033[0m"


def _fmt_date(iso: str | None) -> str:
    return (iso or "")[:10] or "unknown date"


def print_record(rec: dict, index: int | None = None) -> None:
    head = []
    if index is not None:
        head.append(_c("2", f"{index:>3}."))
    handle = rec.get("author_handle")
    head.append(_c("1;36", f"@{handle}" if handle else "@unknown"))
    if rec.get("author_name"):
        head.append(_c("2", rec["author_name"]))
    head.append(_c("2", _fmt_date(rec.get("created_at"))))
    if rec.get("like_seq") is not None:
        head.append(_c("2", f"#{rec['like_seq']} in likes"))
    if rec.get("score") is not None:
        head.append(_c("1;33", f"score {rec['score']}"))
    print(" ".join(head))

    body = (rec.get("text") or "").strip()
    for line in textwrap.wrap(body, WIDTH - 4) or ["(no text)"]:
        print("    " + line)

    if rec.get("quoted_text") or rec.get("quoted_author_handle"):
        qh = rec.get("quoted_author_handle") or "unknown"
        label = _c("2", f"    ┌ quoting @{qh}")
        if rec.get("quoted_article_title"):
            label += _c("1;35", f"  [Article: {rec['quoted_article_title']}]")
        print(label)
        for line in textwrap.wrap((rec.get("quoted_text") or "").strip(), WIDTH - 8)[:4]:
            print(_c("2", "    │ " + line))

    for reason in rec.get("reasons") or []:
        print(_c("33", f"    · {reason}"))
    for url in (rec.get("urls") or [])[:3]:
        print(_c("2", f"    ↗ {url}"))
    if rec.get("url"):
        print(_c("4;34", f"    {rec['url']}"))
    print()


def _open(args) -> "db.sqlite3.Connection":
    return db.connect(args.database)


def cmd_login(args) -> int:
    from .collect import login

    login(args.state)
    return 0


def cmd_collect(args) -> int:
    from .collect import collect

    conn = _open(args)
    before = db.count(conn)
    total = collect(
        conn,
        handle=args.handle,
        max_likes=args.max,
        state_path=args.state,
        headless=not args.headed,
        scroll_pause_s=args.pause,
        progress=lambda n: print(f"  collected {n} likes…", end="\r", flush=True),
    )
    after = db.count(conn)
    print(f"\nsaw {total} likes this run; database now holds {after} ({after - before} new)")
    return 0


def cmd_import_archive(args) -> int:
    conn = _open(args)
    before = db.count(conn)
    n = import_archive(conn, args.path, order=args.order)
    after = db.count(conn)
    print(f"read {n} likes from archive; database now holds {after} ({after - before} new)")
    print(
        "Archive likes carry no author or quote structure. Run `xlikes hydrate` "
        "to fill those in from the browser."
    )
    return 0


def cmd_hydrate(args) -> int:
    from .collect import hydrate

    conn = _open(args)
    n = hydrate(conn, limit=args.limit, state_path=args.state, headless=not args.headed)
    remaining = conn.execute(
        "SELECT COUNT(*) n FROM likes WHERE author_handle IS NULL OR author_handle = ''"
    ).fetchone()["n"]
    print(f"hydrated {n} posts; {remaining} still missing author info")
    return 0


def _emit(records: list[dict], as_json: bool) -> int:
    if as_json:
        print(json.dumps(records, indent=2, ensure_ascii=False, default=str))
        return 0
    if not records:
        print("no matches")
        return 1
    for i, rec in enumerate(records, 1):
        print_record(rec, i)
    return 0


def cmd_search(args) -> int:
    conn = _open(args)
    try:
        records = search_mod.search(
            conn,
            " ".join(args.query),
            since=args.since,
            until=args.until,
            author=args.author,
            quotes_only=args.quotes,
            links_only=args.links,
            media_only=args.media,
            articles_only=args.articles,
            recent=args.recent,
            sort=args.sort,
            limit=args.limit,
        )
    except search_mod.SearchError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return _emit(records, args.json)


def cmd_find(args) -> int:
    conn = _open(args)
    try:
        records = search_mod.find_article_praise(
            conn,
            recent=args.recent,
            since=args.since,
            limit=args.limit,
            min_score=args.min_score,
            extra_terms=" ".join(args.about or []),
        )
    except search_mod.SearchError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if not args.json and records:
        print(
            _c("1", "Likes that look like someone reposting an article and praising it")
            + _c("2", f"  ({len(records)} shown, best first)\n")
        )
    return _emit(records, args.json)


def cmd_stats(args) -> int:
    conn = _open(args)
    info = search_mod.stats(conn)
    handle = db.get_meta(conn, "handle") or "(unknown)"
    last = db.get_meta(conn, "last_collect") or "never"
    print(f"database   {args.database or db.DEFAULT_DB}")
    print(f"account    @{handle}")
    print(f"collected  {last}")
    print(f"likes      {info['total']}")
    print(f"  quotes   {info['quotes'] or 0}")
    print(f"  articles {info['articles'] or 0}")
    print(f"  missing author info {info['unhydrated'] or 0}")
    print(f"posted between {_fmt_date(info['oldest'])} and {_fmt_date(info['newest'])}")
    if info["top_authors"]:
        print("\nmost liked accounts:")
        for row in info["top_authors"]:
            print(f"  {row['n']:>5}  @{row['author_handle']}")
    return 0


def cmd_export(args) -> int:
    conn = _open(args)
    rows = search_mod.search(conn, "", sort="recent", limit=10**9)
    out = Path(args.out)
    out.write_text(json.dumps(rows, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    print(f"wrote {len(rows)} likes to {out}")
    return 0


def cmd_serve(args) -> int:
    from .web import serve

    serve(args.database, host=args.host, port=args.port, open_browser=not args.no_open)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="xlikes",
        description="Search your X likes locally.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent(
            """\
            typical first run:
              xlikes login                     save a browser session (headed, you log in)
              xlikes collect --max 2000        scroll your likes into the local database
              xlikes serve                     search them in a browser

            finding a specific thing:
              xlikes find --recent 400         reposts of an article with praise attached
              xlikes search scaling --quotes   full-text search, quote reposts only
            """
        ),
    )
    parser.add_argument("--database", "-d", help=f"database path (default {db.DEFAULT_DB})")
    sub = parser.add_subparsers(dest="command", required=True)

    def add_state(p):
        from .collect import DEFAULT_STATE

        p.add_argument("--state", default=DEFAULT_STATE, help="browser session file")
        p.add_argument("--headed", action="store_true", help="show the browser window")

    p = sub.add_parser("login", help="log in to X once and save the session")
    add_state(p)
    p.set_defaults(func=cmd_login)

    p = sub.add_parser("collect", help="scroll your likes timeline into the database")
    p.add_argument("--handle", help="your X handle (auto-detected if omitted)")
    p.add_argument("--max", type=int, help="stop after this many likes")
    p.add_argument("--pause", type=float, default=1.4, help="seconds between scrolls")
    add_state(p)
    p.set_defaults(func=cmd_collect)

    p = sub.add_parser("import-archive", help="import likes from an X data export")
    p.add_argument("path", help="like.js, the data/ folder, or the unzipped archive root")
    p.add_argument(
        "--order",
        default="newest-first",
        choices=["newest-first", "oldest-first"],
        help="order of entries in the file (check with `xlikes stats` and flip if wrong)",
    )
    p.set_defaults(func=cmd_import_archive)

    p = sub.add_parser("hydrate", help="fill in authors for archive-imported likes")
    p.add_argument("--limit", type=int, default=200, help="how many to hydrate this run")
    add_state(p)
    p.set_defaults(func=cmd_hydrate)

    p = sub.add_parser("search", help="full-text search over your likes")
    p.add_argument("query", nargs="*", help="words to look for (FTS5 syntax supported)")
    p.add_argument("--since", help="posted on or after: YYYY-MM-DD, or 3w / 21d / 6m")
    p.add_argument("--until", help="posted on or before")
    p.add_argument("--author", help="author or quoted author handle")
    p.add_argument("--recent", type=int, metavar="N",
                   help="only the N most recently liked posts (when you liked it, "
                        "not when it was posted)")
    p.add_argument("--quotes", action="store_true", help="quote reposts only")
    p.add_argument("--links", action="store_true", help="posts carrying a link")
    p.add_argument("--media", action="store_true", help="posts with images or video")
    p.add_argument("--articles", action="store_true", help="posts with an attached X Article")
    p.add_argument("--sort", default="relevance", choices=sorted(search_mod.SORTS))
    p.add_argument("--limit", type=int, default=25)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_search)

    p = sub.add_parser(
        "find",
        help='find "someone reposted an article and said it was good"',
        description="Ranks likes by how much they look like a quote repost praising "
                    "a piece of writing. Use --recent to bound it to what you liked lately.",
    )
    p.add_argument("--recent", type=int, metavar="N", help="only the N most recently liked posts")
    p.add_argument("--since", help="only posts written on or after this date")
    p.add_argument("--about", nargs="*", help="extra words you remember (topic, author, outlet)")
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--min-score", type=float, default=4.0, dest="min_score",
               help="lower this to cast a wider net")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_find)

    p = sub.add_parser("stats", help="what is in the database")
    p.set_defaults(func=cmd_stats)

    p = sub.add_parser("export", help="dump every like to JSON")
    p.add_argument("out")
    p.set_defaults(func=cmd_export)

    p = sub.add_parser("serve", help="browse and search in a local web UI")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8712)
    p.add_argument("--no-open", action="store_true", help="do not open a browser window")
    p.set_defaults(func=cmd_serve)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130
    except BrokenPipeError:
        # `xlikes find | head` closes the pipe early. Point stdout at /dev/null
        # so the interpreter's final flush has somewhere to go -- without this
        # Python prints a second traceback on the way out.
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        return 141


if __name__ == "__main__":
    raise SystemExit(main())
