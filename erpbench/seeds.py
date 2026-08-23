"""Per-firm seed images — SPEC §5.

Three firms as separate sites with distinct seeds. The seeds differ in the
entities they contain, not just in the policy document, for one reason: if
every firm shared a customer list, a model could carry a memorised name
across firms and the cross-firm comparison would measure recall rather than
policy. Distinct entity sets make the counterfactual set do its job.

Firm C's seed is authored here in week 1 and frozen. After the tag, nothing
in this file may change what C contains -- it is evaluated once, in week 5,
and an environment that drifted underneath it would invalidate the only
blind number the study has.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from erpbench.firms import FIRMS, Firm
from erpbench.sites import SEED, SITES, Site, provision, restore

ARTIFACTS = Path(__file__).resolve().parent.parent / "artifacts"
FIRM_SEEDS = ARTIFACTS / "firm_seeds"


@dataclass(frozen=True)
class FirmData:
    """The entity set a firm's world is seeded with.

    Deliberately small and enumerable: every name here can appear in an
    instruction, and an assertion must be able to check for its presence or
    absence without ambiguity.
    """
    customers: tuple[str, ...]
    suppliers: tuple[str, ...]
    items: tuple[tuple[str, str, float], ...]     # (code, name, rate)
    # Two near-identical names, for the `ambiguous` entity axis. Picking
    # either without asking is wrong.
    ambiguous_pair: tuple[str, str]
    # Referenced by instructions but deliberately absent, for the `missing`
    # axis. Never seeded.
    absent_customer: str
    absent_item: str


FIRM_DATA: dict[str, FirmData] = {
    "A": FirmData(
        customers=("Harbourline Freight", "Vantage Tooling", "Pinegrove Foods",
                   "Castellan Metals", "Brightwater Labs", "Ridgeway Haulage"),
        suppliers=("Ashford Components", "Keystone Alloys", "Lumen Plastics"),
        items=(("NW-BEARING-10", "Bearing 10mm", 120.0),
               ("NW-SHAFT-20", "Drive Shaft 20", 340.0),
               ("NW-SEAL-05", "Seal Ring 5mm", 45.0),
               ("NW-PUMP-01", "Transfer Pump", 1800.0)),
        ambiguous_pair=("Meridian Holdings", "Meridian Holdings Ltd"),
        absent_customer="Thornbury Cement",
        absent_item="NW-VALVE-99"),
    "B": FirmData(
        customers=("Fairhaven Mutual", "Oakmere Society", "Stonebridge Union",
                   "Calderwood Trust", "Ellerby Provident", "Marchfield Guild"),
        suppliers=("Redgate Supplies", "Thistledown Works", "Vernon Fabrics"),
        items=(("AM-UNIT-A1", "Assessment Unit A1", 850.0),
               ("AM-UNIT-B2", "Assessment Unit B2", 2400.0),
               ("AM-UNIT-C3", "Assessment Unit C3", 5600.0),
               ("AM-UNIT-D4", "Assessment Unit D4", 320.0)),
        ambiguous_pair=("Whitfield Society", "Whitfield Societies"),
        absent_customer="Langdale Benefit",
        absent_item="AM-UNIT-Z9"),
    "C": FirmData(
        customers=("Aldridge Partners", "Bexley Vaughan", "Corran & Fitz",
                   "Delamere Group", "Ennismore LLP", "Faircourt Advisory"),
        suppliers=("Garnet Print", "Halloway Couriers", "Ivorson Systems"),
        items=(("CR-ADV-01", "Advisory Hour", 240.0),
               ("CR-AUD-02", "Audit Day", 1450.0),
               ("CR-TAX-03", "Tax Review", 680.0),
               ("CR-SEC-04", "Secretarial Retainer", 95.0)),
        ambiguous_pair=("Rowan Estates", "Rowan Estate"),
        absent_customer="Sedgwick Mills",
        absent_item="CR-VAL-99"),
}


def _post(client: Any, path: str, payload: dict[str, Any]) -> dict[str, Any]:
    r = client.post(path, json=payload)
    if r.status_code not in (200, 201):
        raise RuntimeError(f"{path} -> {r.status_code}: {r.text[:200]}")
    return r.json().get("data", {})


def seed_firm(site: Site, firm: Firm, data: FirmData) -> dict[str, Any]:
    """Write this firm's entity set into a freshly reset site."""
    from erpbench.adapter import ERPNextAdapter

    restore(site)
    adapter = ERPNextAdapter(base_url=site.base_url, site=site.name)
    try:
        return write_entities(adapter, firm, data)
    finally:
        adapter.close()


def write_entities(adapter: Any, firm: Firm, data: FirmData) -> dict[str, Any]:
    """Write a firm's entity set into whatever site the adapter points at.

    Split out from `seed_firm` because *which site, reset how* is deployment
    detail and *what a firm's world contains* is not. The native bench pool
    resets by restoring a per-site dump; a containerised ERPNext has no host
    bench to address that way. Both need these exact records, and the entity
    sets must stay identical across deployments or the cross-firm comparison
    measures the environment instead of the policy (SPEC §5).
    """
    client = adapter._client()
    created: dict[str, int] = {}

    names = list(data.customers) + list(data.ambiguous_pair)
    for name in names:
        _post(client, "/api/resource/Customer",
              {"customer_name": name, "customer_type": "Company",
               "customer_group": "Commercial", "territory": "All Territories"})
    created["Customer"] = len(names)

    for name in data.suppliers:
        _post(client, "/api/resource/Supplier",
              {"supplier_name": name, "supplier_group": "Services"})
    created["Supplier"] = len(data.suppliers)

    for code, item_name, rate in data.items:
        _post(client, "/api/resource/Item",
              {"item_code": code, "item_name": item_name,
               "item_group": "Products", "stock_uom": "Nos",
               "is_stock_item": 0})
        _post(client, "/api/resource/Item Price",
              {"item_code": code, "price_list": "Standard Selling",
               "price_list_rate": rate})
    created["Item"] = len(data.items)

    if firm.cost_centres:
        # Firm C books to cost centres; an instruction may name one, and a
        # document that omits it is wrong for C and irrelevant elsewhere.
        company = adapter.query("Company", fields=["name"], limit=1)[0]["name"]
        abbr = adapter.read("Company", company).get("abbr", "")
        for cc in ("Advisory", "Audit"):
            try:
                _post(client, "/api/resource/Cost Center",
                      {"cost_center_name": cc, "company": company,
                       "parent_cost_center": f"{company} - {abbr}",
                       "is_group": 0})
            except RuntimeError:
                pass          # already present from a previous seeding
        created["Cost Center"] = 2

    return created


def dump(site: Site, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("wb") as fh:
        subprocess.run(
            ["mariadb-dump", "-h", "127.0.0.1", "-u", site.db_name,
             f"-p{site.db_password}", "--single-transaction", "--quick",
             "--default-character-set=utf8mb4", site.db_name],
            check=True, stdout=fh)
    return dest


def build(firm_id: str, site_index: int = 1) -> dict[str, Any]:
    firm = FIRMS[firm_id]
    data = FIRM_DATA[firm_id]
    site = provision(site_index)
    created = seed_firm(site, firm, data)
    dest = FIRM_SEEDS / f"firm_{firm_id}.sql"
    dump(site, dest)
    manifest = {
        "firm_id": firm_id, "name": firm.name,
        "seed_file": str(dest.relative_to(ARTIFACTS.parent)),
        "seed_bytes": dest.stat().st_size,
        "created": created,
        "customers": list(data.customers),
        "suppliers": list(data.suppliers),
        "items": [list(i) for i in data.items],
        "ambiguous_pair": list(data.ambiguous_pair),
        "absent_customer": data.absent_customer,
        "absent_item": data.absent_item,
        "policy_sha": __import__("hashlib").sha1(
            firm.policy_text.encode()).hexdigest()[:16],
        "built_at_unix": int(time.time()),
    }
    (FIRM_SEEDS / f"firm_{firm_id}.json").write_text(json.dumps(manifest, indent=2))
    return manifest


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--firms", default="A,B,C")
    ap.add_argument("--site-index", type=int, default=1)
    args = ap.parse_args()
    for fid in args.firms.split(","):
        m = build(fid.strip(), args.site_index)
        print(json.dumps({k: m[k] for k in
                          ("firm_id", "name", "created", "seed_bytes")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
