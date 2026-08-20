"""Oracle REST client — the documented ERPNext API.

Used only to check post-conditions and to score synthesis. Never imported by
shadow/distill/ (see oracle/README.md and tests/test_isolation.py).
"""
from __future__ import annotations

import json
from typing import Any

import httpx

from shadow.config import Config, get_config


class OracleClient:
    def __init__(self, cfg: Config | None = None, timeout: float = 30.0) -> None:
        self.cfg = cfg or get_config()
        self.client = httpx.Client(base_url=self.cfg.app.base_url, timeout=timeout,
                                   follow_redirects=True, trust_env=False)
        self._logged_in = False

    def login(self) -> None:
        r = self.client.post("/api/method/login",
                             data={"usr": self.cfg.app.username,
                                   "pwd": self.cfg.app.password})
        r.raise_for_status()
        self._logged_in = True

    def invalidate(self) -> None:
        """Drop the session. The DB reset takes the server-side session with
        it, so callers must invalidate after every reset."""
        self._logged_in = False

    def _ensure(self) -> None:
        if not self._logged_in:
            self.login()

    # -- documented REST surface ------------------------------------------
    def list(self, doctype: str, filters: Any = None, fields: list[str] | None = None,
             limit: int = 100, order_by: str | None = None) -> list[dict]:
        self._ensure()
        params: dict[str, Any] = {"limit_page_length": limit}
        if filters is not None:
            params["filters"] = json.dumps(filters)
        if fields:
            params["fields"] = json.dumps(fields)
        if order_by:
            params["order_by"] = order_by
        r = self.client.get(f"/api/resource/{doctype}", params=params)
        r.raise_for_status()
        return r.json().get("data", [])

    def get(self, doctype: str, name: str) -> dict:
        self._ensure()
        r = self.client.get(f"/api/resource/{doctype}/{name}")
        r.raise_for_status()
        return r.json().get("data", {})

    def count(self, doctype: str, filters: Any = None) -> int:
        self._ensure()
        params: dict[str, Any] = {"doctype": doctype}
        if filters is not None:
            params["filters"] = json.dumps(filters)
        r = self.client.get("/api/method/frappe.client.get_count", params=params)
        r.raise_for_status()
        return int(r.json().get("message", 0))

    def exists(self, doctype: str, filters: Any) -> bool:
        return bool(self.list(doctype, filters=filters, fields=["name"], limit=1))

    def call(self, method: str, **params: Any) -> Any:
        self._ensure()
        r = self.client.get(f"/api/method/{method}", params=params)
        r.raise_for_status()
        return r.json().get("message")

    def snapshot(self, doctypes: list[str]) -> dict[str, int]:
        """Row counts per doctype — the before/after picture for write verification."""
        return {dt: self.count(dt) for dt in doctypes}

    def close(self) -> None:
        self.client.close()

    def __enter__(self) -> "OracleClient":
        self.login()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()
