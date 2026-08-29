"""Collect likes by driving a real logged-in browser.

Your likes are visible only to you, and X's public API no longer exposes them
on any free tier, so the practical way to read them is the same way you read
them yourself: log in and scroll. This module does that with Playwright and
listens to the GraphQL responses the page makes on its own.

Listening beats calling. X's GraphQL endpoints are versioned by an opaque
query ID that rotates, and calls need matching transaction headers -- so
hand-rolled requests break within weeks. Whatever the page fetches is by
definition current, so we let it fetch and read over its shoulder.

Nothing here touches the network except x.com, and credentials live only in the
Playwright storage-state file you control.
"""

from __future__ import annotations

import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from . import db
from .parse import parse_timeline

DEFAULT_STATE = Path(os.environ.get("XLIKES_HOME", Path.home() / ".xlikes")) / "state.json"

LIKES_OP_HINTS = ("Likes", "Favoriters", "UserByScreenName")


def _require_playwright():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise SystemExit(
            "Playwright is required for browser collection.\n"
            "  pip install playwright && playwright install chromium\n"
            "Or skip the browser entirely: request your X data archive and run\n"
            "  xlikes import-archive path/to/like.js"
        ) from exc
    return sync_playwright


def _launch_kwargs() -> dict[str, Any]:
    """Reuse a preinstalled Chromium when one is configured, rather than downloading."""
    kwargs: dict[str, Any] = {}
    explicit = os.environ.get("XLIKES_CHROMIUM")
    if explicit and Path(explicit).exists():
        kwargs["executable_path"] = explicit
    return kwargs


def login(state_path: str | Path = DEFAULT_STATE, *, timeout_s: int = 300) -> Path:
    """Open a visible browser, wait for you to log in, and save the session.

    Runs headed on purpose: you type your own password into a real X login page,
    and nothing in this tool ever sees or stores it. Only the resulting session
    cookies are written, to a file you own.
    """
    sync_playwright = _require_playwright()
    state_path = Path(state_path)
    state_path.parent.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False, **_launch_kwargs())
        context = browser.new_context()
        page = context.new_page()
        page.goto("https://x.com/login", wait_until="domcontentloaded")
        print("Log in to X in the browser window. Waiting for the home timeline…")
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            if any(c["name"] == "auth_token" for c in context.cookies()):
                page.wait_for_timeout(2000)
                break
            page.wait_for_timeout(1000)
        else:
            browser.close()
            raise SystemExit("timed out waiting for login")
        context.storage_state(path=str(state_path))
        browser.close()

    state_path.chmod(0o600)
    print(f"Session saved to {state_path}")
    return state_path


def _context_from_cookies(pw, headless: bool):
    """Build a context from X_AUTH_TOKEN / X_CT0 instead of a saved session."""
    auth_token = os.environ.get("X_AUTH_TOKEN")
    ct0 = os.environ.get("X_CT0")
    if not (auth_token and ct0):
        return None, None
    browser = pw.chromium.launch(headless=headless, **_launch_kwargs())
    context = browser.new_context()
    context.add_cookies(
        [
            {"name": "auth_token", "value": auth_token, "domain": ".x.com", "path": "/"},
            {"name": "ct0", "value": ct0, "domain": ".x.com", "path": "/"},
        ]
    )
    return browser, context


def _detect_handle(page) -> str | None:
    try:
        page.goto("https://x.com/home", wait_until="domcontentloaded", timeout=45000)
        link = page.wait_for_selector('a[data-testid="AppTabBar_Profile_Link"]', timeout=20000)
        href = link.get_attribute("href") or ""
        return href.strip("/") or None
    except Exception:
        return None


def collect(
    conn,
    *,
    handle: str | None = None,
    max_likes: int | None = None,
    state_path: str | Path = DEFAULT_STATE,
    headless: bool = True,
    scroll_pause_s: float = 1.4,
    idle_rounds: int = 6,
    progress: Callable[[int], None] | None = None,
) -> int:
    """Scroll the likes timeline and store everything it serves.

    Returns the number of likes seen. `like_seq` is assigned from arrival order,
    which is X's own like ordering (most recent first) -- the only handle we get
    on *when* something was liked, since no like timestamp is exposed anywhere.
    """
    sync_playwright = _require_playwright()
    state_path = Path(state_path)

    captured: list[dict[str, Any]] = []
    seen: set[str] = set()
    errors: list[str] = []

    def on_response(response):
        url = response.url
        if "/graphql/" not in url or not any(h in url for h in LIKES_OP_HINTS):
            return
        if response.status == 429:
            errors.append("rate limited by X (HTTP 429)")
            return
        if response.status >= 400:
            return
        try:
            payload = response.json()
        except Exception:
            return
        try:
            records = parse_timeline(payload)
        except Exception as exc:  # a shape we do not understand should not kill the run
            errors.append(f"parse error on {url.split('?')[0]}: {exc}")
            return
        for rec in records:
            if rec["tweet_id"] in seen:
                continue
            seen.add(rec["tweet_id"])
            captured.append(rec)

    with sync_playwright() as pw:
        browser, context = _context_from_cookies(pw, headless)
        if context is None:
            if not state_path.exists():
                raise SystemExit(
                    f"No saved session at {state_path}.\n"
                    "Run `xlikes login` first, or set X_AUTH_TOKEN and X_CT0."
                )
            browser = pw.chromium.launch(headless=headless, **_launch_kwargs())
            context = browser.new_context(storage_state=str(state_path))

        page = context.new_page()
        page.on("response", on_response)

        if not handle:
            handle = _detect_handle(page)
            if not handle:
                browser.close()
                raise SystemExit("could not detect your handle; pass --handle yourname")

        page.goto(f"https://x.com/{handle}/likes", wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(4000)

        if "/login" in page.url or "/i/flow/login" in page.url:
            browser.close()
            raise SystemExit("session expired -- run `xlikes login` again")

        idle = 0
        last_count = 0
        while idle < idle_rounds:
            if max_likes is not None and len(captured) >= max_likes:
                break
            if errors and any("rate limited" in e for e in errors):
                print(f"stopping: {errors[-1]}", file=sys.stderr)
                break
            page.mouse.wheel(0, 4000)
            page.wait_for_timeout(int(scroll_pause_s * 1000))
            if len(captured) == last_count:
                idle += 1
            else:
                idle = 0
                last_count = len(captured)
                if progress:
                    progress(last_count)
        browser.close()

    now = datetime.now(timezone.utc).isoformat()
    if max_likes is not None:
        captured = captured[:max_likes]
    for seq, rec in enumerate(captured):
        rec["like_seq"] = seq
        rec["source"] = "browser"
        rec["first_seen"] = now
    db.upsert_many(conn, captured)
    db.set_meta(conn, "last_collect", now)
    db.set_meta(conn, "handle", handle or "")
    db.set_meta(conn, "last_collect_count", str(len(captured)))

    for err in dict.fromkeys(errors):
        print(f"warning: {err}", file=sys.stderr)
    return len(captured)


def hydrate(
    conn,
    *,
    limit: int = 200,
    state_path: str | Path = DEFAULT_STATE,
    headless: bool = True,
    pause_s: float = 2.0,
) -> int:
    """Fill in author and quote structure for rows imported from an archive.

    Opens each post's own page and reads the GraphQL response it triggers. This
    is one page load per post, so it is slow and rate-limitable -- run it in
    batches with `--limit`.
    """
    sync_playwright = _require_playwright()
    state_path = Path(state_path)

    rows = conn.execute(
        "SELECT tweet_id, url FROM likes "
        "WHERE author_handle IS NULL OR author_handle = '' "
        "ORDER BY COALESCE(like_seq, 1e18) ASC LIMIT ?",
        (int(limit),),
    ).fetchall()
    if not rows:
        return 0

    updated = 0
    with sync_playwright() as pw:
        browser, context = _context_from_cookies(pw, headless)
        if context is None:
            if not state_path.exists():
                raise SystemExit(f"No saved session at {state_path}. Run `xlikes login` first.")
            browser = pw.chromium.launch(headless=headless, **_launch_kwargs())
            context = browser.new_context(storage_state=str(state_path))
        page = context.new_page()

        pending: list[dict[str, Any]] = []
        rate_limited = False

        def on_response(response):
            nonlocal rate_limited
            if "/graphql/" not in response.url:
                return
            if response.status == 429:
                rate_limited = True
                return
            if response.status >= 400:
                return
            try:
                pending.extend(parse_timeline(response.json()))
            except Exception:
                return

        page.on("response", on_response)

        for row in rows:
            if rate_limited:
                print("stopping: rate limited by X (HTTP 429)", file=sys.stderr)
                break
            pending.clear()
            try:
                page.goto(
                    f"https://x.com/i/status/{row['tweet_id']}",
                    wait_until="domcontentloaded",
                    timeout=45000,
                )
                page.wait_for_timeout(int(pause_s * 1000))
            except Exception:
                continue
            for rec in pending:
                if rec["tweet_id"] == str(row["tweet_id"]):
                    rec.pop("like_seq", None)
                    rec["source"] = "hydrated"
                    db.upsert(conn, rec)
                    updated += 1
                    break
        conn.commit()
        browser.close()
    return updated
