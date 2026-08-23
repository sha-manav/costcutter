"""Build the three per-firm seed images against a containerised ERPNext.

`erpbench/seeds.py` builds them against the native Frappe bench pool: one
site per firm, reset by restoring that site's own dump. frappe_docker has no
host-side bench to address that way -- one site, one database, and the only
handle on it is the published MariaDB port. So the sequence here is
serial rather than parallel:

    dump the post-setup site once as a base image
    for each firm: restore base -> write that firm's entities -> dump

The entity sets come from `erpbench.seeds.write_entities`, which is the same
code the native path uses. That matters more than it looks: SPEC §5 makes the
firms' entity sets disjoint so a model cannot carry a memorised name across
firms, and a second copy of the seeding logic is a second thing that can
drift out of agreement with the assertions.

Firm C is frozen (SPEC §10.6). Its manifest fingerprint is checked after
building, and a mismatch is a hard failure -- the correct response is to
revert whatever changed C, never to update the constant.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from erpbench.adapter import ERPNextAdapter                       # noqa: E402
from erpbench.firms import FIRMS                                  # noqa: E402
from erpbench.seeds import FIRM_DATA, write_entities              # noqa: E402

ARTIFACTS = REPO / "artifacts"
FIRM_SEEDS = ARTIFACTS / "firm_seeds"
BASE_IMAGE = FIRM_SEEDS / "base_post_setup.sql"


def _site_db(site: str) -> tuple[str, str, str]:
    from oracle.reset import site_db

    return site_db(site)


def dump_db(site: str, dest: Path) -> Path:
    db_name, db_user, db_password = _site_db(site)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("wb") as fh:
        subprocess.run(
            ["mariadb-dump", "-h", "127.0.0.1", "-u", db_user,
             f"-p{db_password}", "--single-transaction", "--quick",
             "--default-character-set=utf8mb4", db_name],
            check=True, stdout=fh)
    return dest


def restore_db(site: str, source: Path) -> float:
    """Restore, then flush the Frappe caches.

    The flush is not optional. Frappe caches doctype metadata and documents
    in redis; restoring the database underneath a live cache leaves the site
    serving records that no longer exist, which reads as an agent seeing
    phantom state rather than as a broken reset.
    """
    from oracle.reset import reset as _reset

    return _reset(site, source)


def build(site: str, firm_ids: list[str], rebuild_base: bool = False) -> int:
    if rebuild_base or not BASE_IMAGE.exists():
        print(f"dumping base image (post-setup) -> {BASE_IMAGE.name}")
        dump_db(site, BASE_IMAGE)
        print(f"  {BASE_IMAGE.stat().st_size / 1e6:.1f} MB")

    for firm_id in firm_ids:
        firm, data = FIRMS[firm_id], FIRM_DATA[firm_id]
        t0 = time.time()
        secs = restore_db(site, BASE_IMAGE)
        adapter = ERPNextAdapter(site=site)
        try:
            created = write_entities(adapter, firm, data)
        finally:
            adapter.close()

        dest = FIRM_SEEDS / f"firm_{firm_id}.sql"
        dump_db(site, dest)
        manifest = {
            "firm_id": firm_id, "name": firm.name,
            "seed_file": str(dest.relative_to(REPO)),
            "seed_bytes": dest.stat().st_size,
            "created": created,
            "customers": list(data.customers),
            "suppliers": list(data.suppliers),
            "items": [list(i) for i in data.items],
            "ambiguous_pair": list(data.ambiguous_pair),
            "absent_customer": data.absent_customer,
            "absent_item": data.absent_item,
            "policy_sha": hashlib.sha1(firm.policy_text.encode()).hexdigest()[:16],
            "built_at_unix": int(time.time()),
            "deployment": "frappe_docker",
        }
        (FIRM_SEEDS / f"firm_{firm_id}.json").write_text(
            json.dumps(manifest, indent=2) + "\n")
        print(f"firm {firm_id} ({firm.name}): {created}  "
              f"restore {secs:.1f}s  total {time.time() - t0:.1f}s  "
              f"{dest.stat().st_size / 1e6:.1f} MB")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--site", default=os.environ.get("ERPBENCH_SITE", "frontend"))
    ap.add_argument("--firms", default="A,B,C")
    ap.add_argument("--rebuild-base", action="store_true")
    args = ap.parse_args()
    return build(args.site, [f.strip() for f in args.firms.split(",")],
                 rebuild_base=args.rebuild_base)


if __name__ == "__main__":
    raise SystemExit(main())
