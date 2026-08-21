"""Reset the ERPNext instance to seed state.

The benchmark is only meaningful if every task starts from identical state,
so this is a hard requirement rather than a convenience. Implementation is a
logical dump taken once after seeding and re-imported per reset, which is an
order of magnitude faster than re-running the seed script.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

BENCH_ROOT = Path(os.environ.get("BENCH_ROOT", "/home/frappe/frappe-bench"))
DEFAULT_SITE = os.environ.get("SHADOW_SITE", "shadow.localhost")
SEED_DUMP = Path(os.environ.get(
    "SHADOW_SEED_DUMP",
    Path(__file__).resolve().parent.parent / "artifacts" / "seed.sql"))
REDIS_PORTS = (11000, 13000)
# A drop that blocks past this is stuck behind a lock, not being slow. Fail
# loudly: a silent hang is indistinguishable from a long-running task.
DROP_TIMEOUT_S = 120


def site_db(site: str = DEFAULT_SITE) -> tuple[str, str, str]:
    cfg = json.loads((BENCH_ROOT / "sites" / site / "site_config.json").read_text())
    return cfg["db_name"], cfg.get("db_user", cfg["db_name"]), cfg["db_password"]


def _mysql_args(db_user: str, db_password: str) -> list[str]:
    return ["-h", "127.0.0.1", "-u", db_user, f"-p{db_password}"]


def snapshot(site: str = DEFAULT_SITE, dest: Path | None = None) -> Path:
    """Dump current state as the canonical seed image."""
    dest = dest or SEED_DUMP
    dest.parent.mkdir(parents=True, exist_ok=True)
    db_name, db_user, db_password = site_db(site)
    with dest.open("wb") as fh:
        subprocess.run(
            ["mariadb-dump", *_mysql_args(db_user, db_password),
             "--single-transaction", "--quick", "--default-character-set=utf8mb4",
             db_name],
            check=True, stdout=fh)
    return dest


def _kill_connections(db_name: str) -> int:
    """Drop every other connection to the site database.

    ERPNext's web and worker processes hold pooled connections open. An idle
    one still holds a metadata lock, so DROP DATABASE blocks behind it --
    without a timeout, and without any output. A benchmark run hung here for
    26 minutes at zero CPU, which looks exactly like slow progress until you
    check the process list.
    """
    listing = subprocess.run(
        ["mariadb", "-h", "127.0.0.1", "-u", "root", "-padmin", "-N", "-B",
         "-e", "SELECT id FROM information_schema.processlist "
               f"WHERE db = '{db_name}' AND id != CONNECTION_ID();"],
        capture_output=True, text=True, check=False)
    ids = [line.strip() for line in listing.stdout.splitlines() if line.strip()]
    if not ids:
        return 0
    # Each KILL is separate: one already-gone connection must not abort the
    # rest, and a connection closing on its own between the two statements is
    # a normal race rather than an error.
    kills = " ".join(f"KILL CONNECTION {cid};" for cid in ids)
    subprocess.run(
        ["mariadb", "-h", "127.0.0.1", "-u", "root", "-padmin", "--force",
         "-e", kills],
        capture_output=True, check=False)
    return len(ids)


def reset(site: str = DEFAULT_SITE, source: Path | None = None) -> float:
    """Restore the seed image. Returns wall-clock seconds."""
    source = source or SEED_DUMP
    if not source.exists():
        raise FileNotFoundError(
            f"no seed snapshot at {source}; run `python -m shadow.cli seed` first")
    t0 = time.time()
    db_name, db_user, db_password = site_db(site)
    _kill_connections(db_name)
    subprocess.run(
        ["mariadb", "-h", "127.0.0.1", "-u", "root", "-padmin", "-e",
         f"DROP DATABASE IF EXISTS `{db_name}`; "
         f"CREATE DATABASE `{db_name}` CHARACTER SET utf8mb4 "
         f"COLLATE utf8mb4_unicode_ci; "
         f"GRANT ALL ON `{db_name}`.* TO '{db_user}'@'%';"],
        check=True, capture_output=True, timeout=DROP_TIMEOUT_S)
    with source.open("rb") as fh:
        subprocess.run(["mariadb", *_mysql_args(db_user, db_password), db_name],
                       check=True, stdin=fh, capture_output=True)
    for port in REDIS_PORTS:
        subprocess.run(["redis-cli", "-p", str(port), "flushall"],
                       check=False, capture_output=True)
    return time.time() - t0


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="snapshot/reset the benchmark site")
    ap.add_argument("action", choices=["snapshot", "reset"])
    ap.add_argument("--site", default=DEFAULT_SITE)
    args = ap.parse_args()
    if args.action == "snapshot":
        print(f"seed image written to {snapshot(args.site)}")
    else:
        print(f"reset in {reset(args.site):.1f}s")
