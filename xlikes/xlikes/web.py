"""A local search UI, served from the standard library.

Deliberately local-only: your likes are private, so the page binds to loopback
and the data never leaves the machine.
"""

from __future__ import annotations

import json
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import db, search as search_mod

PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>xlikes</title>
<link rel="icon" href="/favicon.ico">
<style>
  :root {
    color-scheme: light dark;
    --bg: #fbfbfa; --fg: #1a1a18; --dim: #6b6b66; --line: #e3e3df;
    --card: #ffffff; --accent: #1d6fd0; --mark: #ffe9a8; --quote: #f4f4f2;
  }
  @media (prefers-color-scheme: dark) {
    :root { --bg:#14140f; --fg:#e9e9e4; --dim:#94948c; --line:#2c2c26;
            --card:#1b1b16; --accent:#7ab4f5; --mark:#5a4a12; --quote:#22221c; }
  }
  * { box-sizing: border-box; }
  body { margin:0; background:var(--bg); color:var(--fg);
         font:15px/1.55 ui-sans-serif, -apple-system, "Segoe UI", Roboto, sans-serif; }
  header { position:sticky; top:0; background:var(--bg); border-bottom:1px solid var(--line);
           padding:14px 20px 10px; z-index:5; }
  h1 { margin:0 0 10px; font-size:15px; font-weight:600; letter-spacing:.02em; }
  h1 span { color:var(--dim); font-weight:400; margin-left:8px; }
  #q { width:100%; padding:10px 12px; font-size:16px; border:1px solid var(--line);
       border-radius:8px; background:var(--card); color:var(--fg); }
  #q:focus { outline:2px solid var(--accent); outline-offset:-1px; }
  .row { display:flex; flex-wrap:wrap; gap:8px; align-items:center; margin-top:10px; }
  .chip { border:1px solid var(--line); background:var(--card); color:var(--dim);
          padding:4px 10px; border-radius:999px; font-size:13px; cursor:pointer; }
  .chip[aria-pressed="true"] { background:var(--accent); border-color:var(--accent); color:#fff; }
  select, input[type=text].sm { border:1px solid var(--line); background:var(--card);
          color:var(--fg); border-radius:8px; padding:4px 8px; font-size:13px; }
  main { max-width:820px; margin:0 auto; padding:18px 20px 60px; }
  .meta { color:var(--dim); font-size:13px; margin:4px 0 14px; }
  article { background:var(--card); border:1px solid var(--line); border-radius:10px;
            padding:14px 16px; margin-bottom:12px; }
  .who { display:flex; flex-wrap:wrap; gap:8px; align-items:baseline; font-size:13px; }
  .handle { font-weight:600; color:var(--accent); text-decoration:none; }
  .name, .when { color:var(--dim); }
  .score { margin-left:auto; color:var(--dim); font-variant-numeric:tabular-nums; }
  .text { margin:8px 0 0; white-space:pre-wrap; word-wrap:break-word; }
  blockquote { margin:10px 0 0; padding:10px 12px; background:var(--quote);
               border-left:2px solid var(--line); border-radius:0 6px 6px 0; font-size:14px; }
  blockquote .who { font-size:12px; }
  .article-tag { display:inline-block; font-size:12px; border:1px solid var(--accent);
                 color:var(--accent); border-radius:4px; padding:0 6px; margin-left:6px; }
  .why { margin:8px 0 0; padding:0; list-style:none; font-size:12px; color:var(--dim); }
  .why li::before { content:"· "; }
  .links { margin-top:8px; font-size:12px; }
  .links a { color:var(--dim); display:block; overflow:hidden; text-overflow:ellipsis;
             white-space:nowrap; }
  mark { background:var(--mark); color:inherit; border-radius:2px; }
  .empty { color:var(--dim); padding:40px 0; text-align:center; }
  .inert { opacity:.35; pointer-events:none; }
</style>
</head>
<body>
<header>
  <h1>xlikes <span id="count"></span></h1>
  <input id="q" placeholder="Search your likes…  (try: article really good)" autofocus>
  <div class="row">
    <button class="chip" id="m-find" aria-pressed="false"
            title="Rank by 'reposted an article and praised it'">article praise</button>
    <button class="chip" id="f-quotes" aria-pressed="false">quote reposts</button>
    <button class="chip" id="f-links" aria-pressed="false">has link</button>
    <button class="chip" id="f-articles" aria-pressed="false">X Articles</button>
    <select id="recent">
      <option value="">all likes</option>
      <option value="100">last 100 liked</option>
      <option value="300">last 300 liked</option>
      <option value="600">last 600 liked</option>
      <option value="1500">last 1500 liked</option>
    </select>
    <input class="sm" type="text" id="author" placeholder="@author" size="10">
    <select id="sort">
      <option value="relevance">relevance</option>
      <option value="recent">recently liked</option>
      <option value="posted">newest posted</option>
      <option value="likes">most liked</option>
    </select>
  </div>
</header>
<main><div id="results"></div></main>
<script>
const $ = s => document.querySelector(s);
const state = { find:false, quotes:false, links:false, articles:false };

function toggle(id, key) {
  const el = $(id);
  el.onclick = () => { state[key] = !state[key];
    el.setAttribute('aria-pressed', state[key]); syncModeUI(); run(); };
}

// In article-praise mode the ranking picks the order and the filters are
// implied, so showing them as live controls would promise something false.
function syncModeUI() {
  ['#f-quotes','#f-links','#f-articles','#sort'].forEach(s =>
    $(s).classList.toggle('inert', state.find));
  $('#q').placeholder = state.find
    ? 'Narrow it down…  (a topic, an author, an outlet you remember)'
    : 'Search your likes…  (try: article really good)';
}
toggle('#m-find','find'); toggle('#f-quotes','quotes');
toggle('#f-links','links'); toggle('#f-articles','articles');
['#q','#recent','#author','#sort'].forEach(s => $(s).addEventListener('input', debounce(run, 180)));

function debounce(fn, ms) { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; }
const esc = s => (s||'').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));

function highlight(text, terms) {
  let out = esc(text);
  for (const t of terms) {
    if (t.length < 2) continue;
    out = out.replace(new RegExp('(' + t.replace(/[.*+?^${}()|[\]\\]/g,'\\$&') + ')', 'gi'), '<mark>$1</mark>');
  }
  return out;
}

function card(r, terms) {
  const when = (r.created_at||'').slice(0,10);
  const seq = r.like_seq === null || r.like_seq === undefined ? '' :
    `<span class="when">#${r.like_seq} in your likes</span>`;
  const quote = (r.quoted_text || r.quoted_author_handle) ? `
    <blockquote>
      <div class="who"><span class="handle">@${esc(r.quoted_author_handle||'unknown')}</span>
      ${r.quoted_article_title ? `<span class="article-tag">Article: ${esc(r.quoted_article_title)}</span>` : ''}</div>
      <div class="text">${highlight((r.quoted_text||'').slice(0,600), terms)}</div>
    </blockquote>` : '';
  const why = (r.reasons||[]).length
    ? `<ul class="why">${r.reasons.map(x => `<li>${esc(x)}</li>`).join('')}</ul>` : '';
  const links = (r.urls||[]).slice(0,3)
    .map(u => `<a href="${esc(u)}" target="_blank" rel="noreferrer noopener">↗ ${esc(u)}</a>`).join('');
  return `<article>
    <div class="who">
      <a class="handle" href="${esc(r.url)}" target="_blank" rel="noreferrer noopener">@${esc(r.author_handle||'unknown')}</a>
      <span class="name">${esc(r.author_name||'')}</span>
      <span class="when">${when}</span> ${seq}
      ${r.score !== undefined ? `<span class="score">score ${r.score}</span>` : ''}
    </div>
    <div class="text">${highlight(r.text||'', terms)}</div>
    ${quote}${why}
    <div class="links">${links}</div>
  </article>`;
}

async function run() {
  const q = $('#q').value.trim();
  const params = new URLSearchParams();
  if (q) params.set('q', q);
  if ($('#recent').value) params.set('recent', $('#recent').value);
  if ($('#author').value.trim()) params.set('author', $('#author').value.trim());
  const endpoint = state.find ? '/api/find' : '/api/search';
  if (state.find) { if (q) params.set('about', q); params.delete('q'); }
  else {
    params.set('sort', $('#sort').value);
    if (state.quotes) params.set('quotes', '1');
    if (state.links) params.set('links', '1');
    if (state.articles) params.set('articles', '1');
  }
  const res = await fetch(endpoint + '?' + params.toString());
  const data = await res.json();
  if (data.error) { $('#results').innerHTML = `<p class="empty">${esc(data.error)}</p>`; return; }
  const terms = q.split(/\s+/).filter(Boolean);
  $('#count').textContent = `${data.results.length} of ${data.total} likes`;
  $('#results').innerHTML = data.results.length
    ? data.results.map(r => card(r, terms)).join('')
    : '<p class="empty">nothing matched</p>';
}
syncModeUI();
run();
</script>
</body>
</html>
"""


FAVICON = (
    b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16">'
    b'<text y="13" font-size="13">\xe2\x99\xa5</text></svg>'
)


def _handler_factory(database):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_args):  # keep the terminal quiet
            pass

        def _send(self, body: bytes, content_type: str, status: int = 200):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, payload, status: int = 200):
            self._send(
                json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8"),
                "application/json; charset=utf-8",
                status,
            )

        def do_GET(self):  # noqa: N802 - stdlib naming
            parts = urlparse(self.path)
            params = {k: v[0] for k, v in parse_qs(parts.query).items()}

            if parts.path == "/":
                return self._send(PAGE.encode("utf-8"), "text/html; charset=utf-8")
            if parts.path == "/favicon.ico":
                return self._send(FAVICON, "image/svg+xml")

            conn = db.connect(database)
            try:
                total = db.count(conn)
                if parts.path == "/api/stats":
                    return self._json(search_mod.stats(conn))
                if parts.path == "/api/search":
                    results = search_mod.search(
                        conn,
                        params.get("q", ""),
                        since=params.get("since"),
                        until=params.get("until"),
                        author=params.get("author"),
                        quotes_only=params.get("quotes") == "1",
                        links_only=params.get("links") == "1",
                        media_only=params.get("media") == "1",
                        articles_only=params.get("articles") == "1",
                        recent=int(params["recent"]) if params.get("recent") else None,
                        sort=params.get("sort", "relevance"),
                        limit=int(params.get("limit", 60)),
                    )
                    return self._json({"results": results, "total": total})
                if parts.path == "/api/find":
                    results = search_mod.find_article_praise(
                        conn,
                        recent=int(params["recent"]) if params.get("recent") else None,
                        since=params.get("since"),
                        limit=int(params.get("limit", 40)),
                        extra_terms=params.get("about", ""),
                    )
                    return self._json({"results": results, "total": total})
                return self._json({"error": "not found"}, 404)
            except search_mod.SearchError as exc:
                return self._json({"error": str(exc)}, 400)
            finally:
                conn.close()

    return Handler


def serve(database=None, *, host: str = "127.0.0.1", port: int = 8712,
          open_browser: bool = True) -> None:
    server = ThreadingHTTPServer((host, port), _handler_factory(database))
    url = f"http://{host}:{port}/"
    print(f"xlikes UI on {url}  (ctrl-c to stop)")
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        server.server_close()
