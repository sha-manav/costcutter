# xlikes

Search your X likes from your own machine. Built for the case where you know
you liked something and can only half remember it.

Everything is local: a SQLite database, an FTS5 index, a CLI, and a small web
UI served on loopback. No API key, no third-party service, nothing leaves the
machine.

## Finding the thing you half remember

The specific case this was built for: *somewhere in the last three weeks I
liked a post where someone reposted an article and said it was really good.*

```bash
xlikes find --recent 400
```

That ranks your recent likes by how much each one looks like a quote repost
with praise attached, and shows you why each one scored. If you remember
anything else about it, say so:

```bash
xlikes find --recent 400 --about compilers
xlikes find --recent 400 --about "@gwern scaling"
```

`--recent N` means *the N most recently liked posts*, which is the important
distinction. `--since` filters on when a post was **written**; `--recent`
filters on when **you liked it**. A post from 2019 can be liked today, so for
"I liked this recently" you almost always want `--recent`.

Rough calibration: `--recent 400` covers about three weeks for someone who
likes ~20 posts a day. Check yours with `xlikes stats`, and widen the number
rather than guessing narrow.

## Setup

```bash
pip install -e .[browser]
playwright install chromium

xlikes login            # opens a real browser; you log in to X yourself
xlikes collect          # scrolls your likes into the local database
```

`login` runs a visible browser and waits. You type your password into X's own
login page — this tool never sees it, and only the resulting session cookies
are saved, to `~/.xlikes/state.json` (mode 600). If you would rather pass
cookies directly, set `X_AUTH_TOKEN` and `X_CT0` and skip `login`.

Collection scrolls your likes timeline the way you would, and reads the
GraphQL responses the page makes on its own. It is paced (1.4s between
scrolls) and stops when X rate-limits. A first run over thousands of likes
takes a while; `--max` bounds it, and re-running is incremental.

```bash
xlikes collect --max 500      # just the most recent 500
xlikes collect --headed       # watch it work
```

## Searching

```bash
xlikes search scaling hypothesis          # full-text, words ANDed
xlikes search '"really good"'             # FTS5 syntax passes through
xlikes search compilers --quotes          # quote reposts only
xlikes search --author gwern              # by author, or by quoted author
xlikes search rust --since 3w --sort recent
xlikes serve                              # the same thing in a browser
```

Search covers the post text, the quoted post's text, both authors, any
attached X Article title, and the links. Long posts are indexed in full, not
truncated at 280 characters.

Filters: `--quotes`, `--links`, `--media`, `--articles`, `--author`,
`--since`, `--until`, `--recent`. Sorts: `relevance`, `recent` (recently
liked), `posted`, `likes`. `--json` for piping.

Dates accept `2025-08-01` or relative forms like `21d`, `3w`, `6m`.

## Without a browser

If you would rather not automate a login, request your data archive from X
(Settings → Your account → Download an archive) and import it:

```bash
xlikes import-archive path/to/like.js
```

The archive is thin — it carries the post ID, its text, and a permalink, but
no author, no dates, and no quote structure. Two things make it usable anyway:
post IDs are snowflakes, so dates are recovered from the ID alone; and
`xlikes hydrate` fills in authors and quote structure later from the browser,
a few hundred at a time.

The one thing the export does not document is its own ordering, and X has
shipped it both ways. It defaults to newest-first; run `xlikes stats` after
importing and re-import with `--order oldest-first` if the dates look
inverted.

## How it holds up

X reshapes its GraphQL payloads regularly. The parser does not follow a fixed
path into the response — it walks the whole document looking for things
shaped like posts, and uses structure rather than location to classify them.
The one thing it must get right is nesting: a quoted post is a post object
inside another post object, and promoting it would invent a like you never
gave. The walk tracks the keys it descended through and refuses to do that.

Collection listens rather than calls, for the same reason. X's GraphQL
endpoints are versioned by an opaque query ID that rotates, so hand-rolled
requests break within weeks. Whatever the page fetches is by definition
current.

## Notes

- Your likes are visible only to you, so collection needs your own session.
  This reads your own data from your own account.
- The web UI binds to `127.0.0.1` and the data never leaves the machine.
- Nothing writes credentials to the database, and no key is ever logged.

## Tests

```bash
pip install -e .[dev]
pytest tests/
```

35 tests covering parsing (nesting, tombstones, long posts, both author
shapes, and a fallback for when X renames its entry envelope), storage merge
semantics, archive import, search filters, and the ranking.
