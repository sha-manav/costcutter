"""Provision a pool of independent ERPNext sites under frappe_docker.

Rollout throughput is bounded by the ERP, not the model (SPEC §6): the
calibration gate ran 810 rows serially against one site and took the better
part of two days, almost all of it waiting. Week 2 is ~2,160 rows, which is
not viable one at a time.

`erpbench/sites.py` already does this for a native Frappe bench, where the
bench directory is on the host. Under frappe_docker it lives inside a volume,
so site directories are created through the backend container while databases
are created over the published MariaDB port.

Independence is the whole point: a rollout resets its own site and must not
be able to see another's writes. One site is owned by exactly one worker --
sharing would make every reset a barrier and every diff a race, which is the
bug that cost the first throughput measurement 15%.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
COMPOSE = ["docker", "compose", "-f", "/tmp/frappe_docker/pwd.yml",
           "-f", str(REPO / "infra" / "pwd.override.yml")]
SUBDIRS = ("logs", "private/backups", "private/files", "public/files",
           "task-logs", "error-snapshots", "locks")
BASE_SEED = REPO / "artifacts" / "firm_seeds" / "firm_A.sql"


def site_name(i: int) -> str:
    return f"erp{i:02d}.localhost"


def db_name(i: int) -> str:
    return f"erpbench_{i:02d}"


def _sql(statement: str) -> None:
    subprocess.run(["mariadb", "-h", "127.0.0.1", "-u", "root", "-padmin",
                    "-e", statement], check=True, capture_output=True)


def _backend(*args: str, stdin=None) -> subprocess.CompletedProcess:
    return subprocess.run([*COMPOSE, "exec", "-T", "backend", *args],
                          check=True, capture_output=True, stdin=stdin)


def in_use(site: str) -> bool:
    """Whether a gate process currently owns this site."""
    out = subprocess.run(["ps", "-eo", "command"], capture_output=True,
                         text=True).stdout
    return f"--site {site}" in out


def provision(i: int, password: str, force: bool = False) -> dict:
    site, db = site_name(i), db_name(i)
    # Provisioning drops the database. Doing that to a site a shard owns
    # replaces its world mid-rollout, which is the one-worker-per-site
    # invariant broken from the outside -- it has happened twice, both times
    # from a second shell during this session, and both times the rows in
    # flight had to be thrown away. Refuse rather than warn.
    if in_use(site) and not force:
        raise SystemExit(
            f"{site} is owned by a running gate process. Provisioning would "
            f"drop its database mid-rollout. Stop that shard first, or use "
            f"--start to provision only new sites.")
    _sql(f"DROP DATABASE IF EXISTS `{db}`; "
         f"CREATE DATABASE `{db}` CHARACTER SET utf8mb4 "
         f"COLLATE utf8mb4_unicode_ci; "
         f"CREATE USER IF NOT EXISTS '{db}'@'%' IDENTIFIED BY '{password}'; "
         f"GRANT ALL ON `{db}`.* TO '{db}'@'%';")
    with BASE_SEED.open("rb") as fh:
        subprocess.run(["mariadb", "-h", "127.0.0.1", "-u", db,
                        f"-p{password}", db], check=True, stdin=fh,
                       capture_output=True)

    root = f"/home/frappe/frappe-bench/sites/{site}"
    _backend("mkdir", "-p", *[f"{root}/{d}" for d in SUBDIRS])
    config = json.dumps({"db_name": db, "db_password": password,
                         "db_type": "mariadb", "developer_mode": 0}, indent=1)
    _backend("sh", "-c", f"cat > {root}/site_config.json <<'EOF'\n{config}\nEOF")

    # oracle/reset.py reads credentials from a bench layout on the host.
    mirror = Path.home() / ".erpbench" / "bench" / "sites" / site
    mirror.mkdir(parents=True, exist_ok=True)
    (mirror / "site_config.json").write_text(config)
    (mirror / "site_config.json").chmod(0o600)
    return {"site": site, "db": db}


def healthy(site: str) -> bool:
    import httpx

    try:
        r = httpx.get("http://localhost:8080/api/method/ping",
                      headers={"Host": site}, timeout=20)
        return r.status_code == 200
    except Exception:
        return False


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--count", type=int, default=6)
    ap.add_argument("--start", type=int, default=1,
                    help="first site index to provision. Use this to add "
                         "sites without touching ones already in use.")
    ap.add_argument("--password", default="benchpool")
    args = ap.parse_args()

    if not BASE_SEED.exists():
        print(f"no base seed at {BASE_SEED}; build the firm seeds first",
              file=sys.stderr)
        return 1

    # --count is a count, not an end index. It read as `range(start, count+1)`
    # until 2026-08-26, which silently provisioned nothing whenever start > 1
    # -- precisely the "add sites without touching ones in use" case --start
    # exists for, and it exited 0 while doing it.
    made = [provision(i, args.password)
            for i in range(args.start, args.start + args.count)]
    subprocess.run([*COMPOSE, "restart", "frontend"], check=False,
                   capture_output=True)

    ok = 0
    for m in made:
        state = healthy(m["site"])
        ok += state
        print(f"  {m['site']:20} db={m['db']:14} {'healthy' if state else 'UNHEALTHY'}")
    print(f"\n{ok}/{len(made)} sites healthy")
    return 0 if ok == len(made) else 1


if __name__ == "__main__":
    raise SystemExit(main())
