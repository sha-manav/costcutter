"""A pool of independent ERPNext sites — SPEC §6.

Rollout throughput is bounded by the ERP, not by the model. One Frappe bench
serves many sites by Host header, each with its own database, so a pool is a
database copy plus a config directory rather than a full `bench new-site`
(which reinstalls every app and takes minutes per site).

Independence is what matters: a rollout resets its own site and cannot see
another's writes. Sharing one site across workers would make every reset a
barrier and every diff a race.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

BENCH_ROOT = Path(os.environ.get("BENCH_ROOT", "/home/frappe/frappe-bench"))
SITES = BENCH_ROOT / "sites"
SEED = Path(__file__).resolve().parent.parent / "artifacts" / "seed.sql"
# Frappe expects these to exist; a missing logs/ dir alone 500s the site.
SITE_SUBDIRS = ("logs", "private/backups", "private/files", "public/files",
                "task-logs", "error-snapshots", "locks")


@dataclass(frozen=True)
class Site:
    name: str
    db_name: str
    db_password: str

    @property
    def base_url(self) -> str:
        port = os.environ.get("ERPBENCH_PORT", "8000")
        return f"http://127.0.0.1:{port}"


def _mysql_root(sql: str) -> None:
    subprocess.run(["mariadb", "-h", "127.0.0.1", "-u", "root", "-padmin",
                    "-e", sql], check=True, capture_output=True)


def provision(index: int, password: str = "PHlMuQin4lLTaYRJ",
              force: bool = False) -> Site:
    """Create (or reuse) site N. Idempotent."""
    site = Site(name=f"erp{index:02d}.localhost", db_name=f"erpbench_{index:02d}",
                db_password=password)
    site_dir = SITES / site.name
    fresh = force or not (site_dir / "site_config.json").exists()

    if fresh:
        _mysql_root(
            f"DROP DATABASE IF EXISTS `{site.db_name}`; "
            f"CREATE DATABASE `{site.db_name}` CHARACTER SET utf8mb4 "
            f"COLLATE utf8mb4_unicode_ci; "
            f"CREATE USER IF NOT EXISTS '{site.db_name}'@'%' "
            f"IDENTIFIED BY '{site.db_password}'; "
            f"GRANT ALL ON `{site.db_name}`.* TO '{site.db_name}'@'%';")
        restore(site)

    for sub in SITE_SUBDIRS:
        (site_dir / sub).mkdir(parents=True, exist_ok=True)
    (site_dir / "site_config.json").write_text(json.dumps({
        "db_name": site.db_name, "db_password": site.db_password,
        "db_type": "mariadb", "developer_mode": 0}, indent=1))
    shutil.chown(site_dir, user="frappe", group="frappe")
    subprocess.run(["chown", "-R", "frappe:frappe", str(site_dir)],
                   check=False, capture_output=True)

    hosts = Path("/etc/hosts")
    if site.name not in hosts.read_text():
        with hosts.open("a") as fh:
            fh.write(f"127.0.0.1 {site.name}\n")
    return site


def restore(site: Site) -> float:
    """Reload this site's database from the seed image.

    Connections are killed first: an idle pooled connection holds a metadata
    lock and DROP DATABASE blocks behind it silently.
    """
    t0 = time.time()
    ids = subprocess.run(
        ["mariadb", "-h", "127.0.0.1", "-u", "root", "-padmin", "-N", "-B",
         "-e", "SELECT id FROM information_schema.processlist "
               f"WHERE db = '{site.db_name}' AND id != CONNECTION_ID();"],
        capture_output=True, text=True, check=False).stdout.split()
    if ids:
        # --force: a connection that closed between the SELECT and the KILL
        # is a normal race, not an error, and must not abort the rest.
        subprocess.run(
            ["mariadb", "-h", "127.0.0.1", "-u", "root", "-padmin", "--force",
             "-e", " ".join(f"KILL CONNECTION {i};" for i in ids)],
            check=False, capture_output=True)
    _mysql_root(f"DROP DATABASE IF EXISTS `{site.db_name}`; "
                f"CREATE DATABASE `{site.db_name}` CHARACTER SET utf8mb4 "
                f"COLLATE utf8mb4_unicode_ci; "
                f"GRANT ALL ON `{site.db_name}`.* TO '{site.db_name}'@'%';")
    with SEED.open("rb") as fh:
        subprocess.run(["mariadb", "-h", "127.0.0.1", "-u", site.db_name,
                        f"-p{site.db_password}", site.db_name],
                       stdin=fh, check=True, capture_output=True)
    return time.time() - t0


def provision_pool(n: int, force: bool = False) -> list[Site]:
    return [provision(i, force=force) for i in range(1, n + 1)]


def health(site: Site, timeout: float = 15.0) -> bool:
    import httpx

    try:
        r = httpx.get(f"{site.base_url}/api/method/ping",
                      headers={"Host": site.name}, timeout=timeout)
        return r.status_code == 200
    except Exception:
        return False
