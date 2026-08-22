"""SystemAdapter — the boundary between the benchmark and the system under test.

Everything the harness, the verifier and the envelope diff need from the
business system goes through this protocol. Nothing above it imports ERPNext
or Frappe, so a second system can be added without touching a task, an
assertion, or a harness.

Two design points that are not negotiable downstream:

**Snapshots are of the whole database, not a watchlist.** SPEC §4 counts an
unexpected mutation as a failure, and a watchlist can only find mutations
somebody thought to watch. The one thing that must not happen is an agent
writing a record nobody enumerated and the run scoring clean.

**The base URL is configuration.** This container cannot reach an off-box
host, so weeks 1-2 run against localhost, but nothing here assumes that.
SPEC §1.3 puts ERPNext on a persistent VM in week 3; that is a config change,
not a code change.
"""
from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass, field
from typing import Any, Iterable, Protocol, runtime_checkable


@dataclass(frozen=True)
class Row:
    """One record, reduced to what a diff needs."""
    doctype: str
    name: str
    # Frappe stamps every row; `modified` changes on any field write, so a
    # row present in both snapshots with a new stamp is an update.
    modified: str
    docstatus: int = 0


@dataclass
class Snapshot:
    """The state of every table at one instant."""
    rows: dict[tuple[str, str], Row] = field(default_factory=dict)
    taken_at: float = 0.0

    def key_set(self) -> set[tuple[str, str]]:
        return set(self.rows)


@dataclass
class Diff:
    created: list[Row] = field(default_factory=list)
    updated: list[Row] = field(default_factory=list)
    deleted: list[Row] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not (self.created or self.updated or self.deleted)

    def touched_doctypes(self) -> set[str]:
        return {r.doctype for r in self.created + self.updated + self.deleted}

    def to_dict(self) -> dict[str, Any]:
        def rows(rs: list[Row]) -> list[dict[str, Any]]:
            return [{"doctype": r.doctype, "name": r.name,
                     "docstatus": r.docstatus} for r in rs]
        return {"created": rows(self.created), "updated": rows(self.updated),
                "deleted": rows(self.deleted)}


class AdapterError(RuntimeError):
    """The system under test could not be reached or operated.

    SPEC §12.4: infrastructure failure is `status: error` and is excluded
    from success-rate denominators. It is never an agent failure.
    """


@runtime_checkable
class SystemAdapter(Protocol):
    """What a business system must offer to be benchmarked."""

    name: str

    def health(self) -> bool: ...
    def reset(self) -> float: ...
    def snapshot(self) -> Snapshot: ...
    def diff(self, before: Snapshot, after: Snapshot) -> Diff: ...
    def read(self, doctype: str, name: str) -> dict[str, Any]: ...
    def query(self, doctype: str, filters: Any = None,
              fields: list[str] | None = None, limit: int = 100,
              order_by: str | None = None) -> list[dict[str, Any]]: ...
    def count(self, doctype: str, filters: Any = None) -> int: ...


# Frappe tables that change on their own -- login stamps, background jobs,
# view counters, scheduler bookkeeping. They are not agent mutations and
# would otherwise make every run look like it wrote something.
CHURN_DOCTYPES: frozenset[str] = frozenset({
    "Activity Log", "Access Log", "Route History", "Scheduled Job Log",
    "Error Log", "Error Snapshot", "Version", "View Log", "Notification Log",
    "Email Queue", "Email Queue Recipient", "Prepared Report", "Log Setting",
    "Sessions", "__Auth", "__UserSettings", "__global_search",
    "Website Analytics", "Energy Point Log", "Document Follow",
    "Scheduled Job Type", "Webhook Request Log",
})


class ERPNextAdapter:
    """ERPNext v15 over its REST API, with SQL for whole-database snapshots.

    Snapshots go through SQL rather than REST because the diff must cover
    every table: enumerating ~900 doctypes over HTTP takes minutes, and the
    same information is two queries against `information_schema`.
    """

    name = "erpnext"

    def __init__(self, base_url: str | None = None, site: str | None = None,
                 username: str = "Administrator", password: str = "admin",
                 db_host: str = "127.0.0.1", timeout: float = 30.0) -> None:
        self.site = site or os.environ.get("ERPBENCH_SITE", "shadow.localhost")
        self.base_url = (base_url or os.environ.get("ERPBENCH_BASE_URL")
                         or f"http://{self.site}:8000").rstrip("/")
        self.username, self.password = username, password
        self.db_host = os.environ.get("ERPBENCH_DB_HOST", db_host)
        self.timeout = timeout
        self._http: Any = None
        self._db_creds: tuple[str, str, str] | None = None

    # ---------------------------------------------------------------- http
    def _client(self):
        import httpx

        if self._http is None:
            self._http = httpx.Client(
                base_url=self.base_url, timeout=self.timeout,
                headers={"Host": self.site}, follow_redirects=True)
            r = self._http.post("/api/method/login",
                                data={"usr": self.username, "pwd": self.password})
            if r.status_code != 200:
                raise AdapterError(
                    f"login to {self.base_url} failed: {r.status_code}")
        return self._http

    def invalidate(self) -> None:
        """Drop the session. A reset destroys it server-side."""
        if self._http is not None:
            try:
                self._http.close()
            except Exception:
                pass
        self._http = None

    # -------------------------------------------------------------- health
    def health(self) -> bool:
        import httpx

        try:
            r = httpx.get(f"{self.base_url}/api/method/ping",
                          headers={"Host": self.site}, timeout=10.0)
            return r.status_code == 200
        except Exception:
            return False

    def reset(self) -> float:
        from oracle.reset import reset as _reset

        try:
            seconds = _reset(self.site)
        except Exception as exc:                       # pragma: no cover
            raise AdapterError(f"reset failed: {type(exc).__name__}: {exc}") from exc
        self.invalidate()
        return seconds

    # ----------------------------------------------------------------- sql
    def _creds(self) -> tuple[str, str, str]:
        if self._db_creds is None:
            from oracle.reset import site_db

            self._db_creds = site_db(self.site)
        return self._db_creds

    def _sql(self, query: str) -> list[tuple]:
        import pymysql

        db_name, db_user, db_password = self._creds()
        try:
            conn = pymysql.connect(host=self.db_host, user=db_user,
                                   password=db_password, database=db_name,
                                   connect_timeout=int(self.timeout))
        except Exception as exc:
            raise AdapterError(f"database unreachable: {exc}") from exc
        try:
            with conn.cursor() as cur:
                cur.execute(query)
                return list(cur.fetchall())
        finally:
            conn.close()

    # ------------------------------------------------------------ snapshot
    def snapshot(self) -> Snapshot:
        """Every row of every business table, keyed by (doctype, name).

        One query per table would be ~900 round trips. Instead the table list
        comes from information_schema and the rows come from a single UNION,
        which keeps a snapshot well under a second.
        """
        db_name, _u, _p = self._creds()
        # Only tables that actually carry the identity and stamp columns a
        # diff needs. Some tab* tables (singles, internal stores) do not, and
        # assuming they do turns a snapshot into a hard error.
        tables = [t[0] for t in self._sql(
            "SELECT c.table_name FROM information_schema.columns c "
            f"WHERE c.table_schema = '{db_name}' "
            "AND c.table_name LIKE 'tab%' AND c.column_name IN ('name','modified') "
            "GROUP BY c.table_name HAVING COUNT(DISTINCT c.column_name) = 2")]
        doctypes = {t: t[3:] for t in tables if t[3:] not in CHURN_DOCTYPES}
        if not doctypes:
            return Snapshot(taken_at=time.time())

        submittable = {t[0] for t in self._sql(
            "SELECT table_name FROM information_schema.columns "
            f"WHERE table_schema = '{db_name}' AND column_name = 'docstatus'")}
        parts = [
            f"SELECT {self._quote(dt)} AS dt, `name`, `modified`, "
            f"{'`docstatus`' if tbl in submittable else '0'} AS ds FROM `{tbl}`"
            for tbl, dt in doctypes.items()]
        rows: dict[tuple[str, str], Row] = {}
        # Batched: a single 900-way UNION exceeds MariaDB's parser limits.
        for i in range(0, len(parts), 40):
            chunk = " UNION ALL ".join(parts[i:i + 40])
            for dt, nm, mod, ds in self._sql(chunk):
                rows[(dt, nm)] = Row(doctype=str(dt), name=str(nm),
                                     modified=str(mod), docstatus=int(ds or 0))
        return Snapshot(rows=rows, taken_at=time.time())

    @staticmethod
    def _quote(value: str) -> str:
        return "'" + value.replace("\\", "\\\\").replace("'", "''") + "'"

    def diff(self, before: Snapshot, after: Snapshot) -> Diff:
        d = Diff()
        b, a = before.key_set(), after.key_set()
        for key in sorted(a - b):
            d.created.append(after.rows[key])
        for key in sorted(b - a):
            d.deleted.append(before.rows[key])
        for key in sorted(a & b):
            if after.rows[key].modified != before.rows[key].modified:
                d.updated.append(after.rows[key])
        return d

    # ------------------------------------------------------------- reading
    def read(self, doctype: str, name: str) -> dict[str, Any]:
        r = self._client().get(f"/api/resource/{doctype}/{name}")
        if r.status_code != 200:
            raise AdapterError(f"read {doctype}/{name}: {r.status_code}")
        return r.json().get("data", {})

    def query(self, doctype: str, filters: Any = None,
              fields: list[str] | None = None, limit: int = 100,
              order_by: str | None = None) -> list[dict[str, Any]]:
        import json as _json

        params: dict[str, Any] = {"limit_page_length": limit}
        if filters is not None:
            params["filters"] = _json.dumps(filters)
        params["fields"] = _json.dumps(fields or ["name"])
        if order_by:
            params["order_by"] = order_by
        r = self._client().get(f"/api/resource/{doctype}", params=params)
        if r.status_code != 200:
            raise AdapterError(f"query {doctype}: {r.status_code} {r.text[:160]}")
        return r.json().get("data", [])

    def count(self, doctype: str, filters: Any = None) -> int:
        return len(self.query(doctype, filters=filters, fields=["name"],
                              limit=100000))

    def close(self) -> None:
        self.invalidate()


def make_adapter(kind: str = "erpnext", **kw: Any) -> SystemAdapter:
    if kind != "erpnext":
        raise ValueError(f"no adapter for {kind!r}")
    return ERPNextAdapter(**kw)
