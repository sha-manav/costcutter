"""mitmproxy addon: one HttpRecord per flow, appended to JSONL.

Run under the capture virtualenv (mitmproxy has its own pinned dependency
set, so it lives in .venv-capture rather than the main environment):

    .venv-capture/bin/mitmdump --listen-port 8081 -s shadow/capture/addon.py

Credentials never reach the record stream. `Authorization`, `Cookie`,
`Set-Cookie` and Frappe's CSRF header are removed from every record and
written to a separate credential store keyed by host.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import parse_qs, urlparse

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from shadow.capture.schema import HttpRecord  # noqa: E402

SECRET_HEADERS = {"authorization", "cookie", "set-cookie", "x-frappe-csrf-token",
                  "proxy-authorization", "x-api-key"}
# Response bodies above this size are stored truncated: provenance only ever
# needs scalar leaves, and full HTML/asset payloads would dwarf the capture.
MAX_BODY_BYTES = 512 * 1024


def _json_or_text(raw: bytes, content_type: str):
    if not raw:
        return None
    if len(raw) > MAX_BODY_BYTES:
        raw = raw[:MAX_BODY_BYTES]
    ct = content_type.lower()
    text = raw.decode("utf-8", errors="replace")
    if "json" in ct:
        try:
            return json.loads(text)
        except ValueError:
            return text
    if "x-www-form-urlencoded" in ct:
        return {k: v[0] if len(v) == 1 else v for k, v in parse_qs(text).items()}
    if "html" in ct or "text" in ct:
        # Kept only as a navigation marker; the body itself is dropped.
        return None
    return None


class CredentialStore:
    """Host -> latest observed secrets. Written with 0600, never committed."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.lock = threading.Lock()
        self.data: dict[str, dict] = {}
        if path.exists():
            try:
                self.data = json.loads(path.read_text())
            except ValueError:
                self.data = {}

    def record(self, host: str, headers: dict[str, str]) -> None:
        found = {k: v for k, v in headers.items() if k.lower() in SECRET_HEADERS}
        if not found:
            return
        with self.lock:
            entry = self.data.setdefault(host, {})
            entry.update({k.lower(): v for k, v in found.items()})
            entry["updated"] = time.time()
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data, indent=2))
            os.chmod(tmp, 0o600)
            tmp.replace(self.path)


class ShadowCapture:
    def __init__(self) -> None:
        out = os.environ.get("SHADOW_CAPTURE_OUT")
        cred = os.environ.get("SHADOW_CREDENTIALS")
        if not out or not cred:
            from shadow.config import get_config

            cfg = get_config()
            out = out or str(cfg.path("capture"))
            cred = cred or str(cfg.path("credentials"))
        self.out_path = Path(out)
        self.out_path.parent.mkdir(parents=True, exist_ok=True)
        self.creds = CredentialStore(Path(cred))
        self.hosts = {h for h in os.environ.get("SHADOW_CAPTURE_HOSTS", "").split(",") if h}
        self.lock = threading.Lock()
        self.count = 0

    # -- mitmproxy hooks ---------------------------------------------------
    def response(self, flow) -> None:  # noqa: ANN001 - mitmproxy flow
        try:
            self._handle(flow)
        except Exception as exc:  # never kill the proxy over one flow
            print(f"[shadow-capture] error: {exc}", file=sys.stderr)

    def _handle(self, flow) -> None:  # noqa: ANN001
        req, resp = flow.request, flow.response
        host = req.pretty_host
        if self.hosts and host not in self.hosts:
            return

        self.creds.record(host, dict(req.headers))
        self.creds.record(host, {k: v for k, v in resp.headers.items()
                                 if k.lower() == "set-cookie"})

        req_ct = req.headers.get("content-type", "")
        resp_ct = resp.headers.get("content-type", "")
        parsed = urlparse(req.pretty_url)

        ts_start = getattr(flow.request, "timestamp_start", time.time())
        ts_end = getattr(flow.response, "timestamp_end", None) or time.time()

        record = HttpRecord(
            id=uuid.uuid4().hex[:16],
            ts_start=ts_start,
            ts_end=ts_end,
            method=req.method,
            url=req.pretty_url,
            path=parsed.path,
            query={k: v[0] if len(v) == 1 else v
                   for k, v in parse_qs(parsed.query).items()},
            req_headers=self._safe_headers(req.headers),
            req_body=_json_or_text(req.raw_content or b"", req_ct),
            status=resp.status_code,
            resp_headers=self._safe_headers(resp.headers),
            resp_body=_json_or_text(resp.raw_content or b"", resp_ct),
            duration_ms=max(0.0, (ts_end - ts_start) * 1000.0),
            content_type=resp_ct.split(";")[0].strip().lower(),
        )
        with self.lock:
            with self.out_path.open("a") as fh:
                fh.write(record.model_dump_json() + "\n")
            self.count += 1

    @staticmethod
    def _safe_headers(headers) -> dict[str, str]:  # noqa: ANN001
        return {k: v for k, v in headers.items() if k.lower() not in SECRET_HEADERS}

    def done(self) -> None:
        print(f"[shadow-capture] wrote {self.count} records to {self.out_path}",
              file=sys.stderr)


addons = [ShadowCapture()]
