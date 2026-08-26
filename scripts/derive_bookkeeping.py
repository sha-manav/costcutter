"""Measure what ERPNext writes on its own — SPEC §4.

An envelope enumerates what the *agent* may write. ERPNext writes more than
it is asked to: creating an Item also creates its default row, a UOM
conversion and a stock Bin; submitting an invoice writes ledger entries.
Those are consequences of a permitted action, not choices, and scoring them
as unexpected measures the ERP rather than the agent.

Naming them in a list is fragile — it misses whatever ERPNext starts writing
next. This establishes them **by construction** instead: perform a known-good
primary write against a seeded site, diff the whole database, and record
every doctype that appeared alongside it. Anything in that diff other than
the primary write is derived, by definition, because nothing else acted.

The output maps primary doctype -> derived doctypes, so scoring can require
*provenance*: a derived row is excused only when the write that causes it
actually happened and was itself permitted. A stray Comment with no
corresponding document write is still unexpected.

Run against a scratch site, never one a pool worker owns.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from erpbench.adapter import ERPNextAdapter                     # noqa: E402

# Known-good primary writes: the canonical correct action for each document
# type the corpus asks an agent to produce.
PROBES: list[tuple[str, str, dict, bool]] = [
    ("Customer", "Customer",
     {"customer_name": "Derived Probe Co", "customer_group": "Commercial",
      "territory": "All Territories"}, False),
    ("Supplier", "Supplier",
     {"supplier_name": "Derived Probe Supply", "supplier_group": "Services"},
     False),
    ("Item", "Item",
     {"item_code": "DERIVED-PROBE-1", "item_name": "Derived Probe Item",
      "item_group": "Products", "stock_uom": "Nos", "is_stock_item": 0}, False),
    ("Item Price", "Item Price",
     {"item_code": "DERIVED-PROBE-1", "price_list": "Standard Selling",
      "price_list_rate": 100}, False),
]


def _doc_probes(customer: str, item: str) -> list[tuple[str, str, dict, bool]]:
    """Documents, each created and then submitted, since submission is where
    most bookkeeping happens."""
    line = [{"item_code": item, "qty": 1, "rate": 100}]
    return [
        ("Quotation", "Quotation",
         {"quotation_to": "Customer", "party_name": customer, "items": line}, True),
        ("Sales Order", "Sales Order",
         {"customer": customer, "delivery_date": "2027-01-01", "items": line}, True),
        ("Sales Invoice", "Sales Invoice",
         {"customer": customer, "items": line}, True),
    ]


def probe(ad: ERPNextAdapter, label: str, doctype: str, fields: dict,
          submit: bool) -> dict:
    before = ad.snapshot()
    client = ad._client()
    r = client.post(f"/api/resource/{doctype}", json=fields)
    if r.status_code not in (200, 201):
        return {"primary": doctype, "ok": False,
                "error": r.text[:160], "derived": []}
    name = r.json().get("data", {}).get("name", "")
    if submit and name:
        client.put(f"/api/resource/{doctype}/{name}", json={"docstatus": 1})
    diff = ad.diff(before, ad.snapshot())
    seen = [row.doctype for row in diff.created + diff.updated]
    derived = sorted({d for d in seen if d != doctype})
    return {"primary": doctype, "ok": True, "derived": derived,
            "n_rows": len(seen)}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--site", default="frontend",
                    help="scratch site; never one a pool worker owns")
    args = ap.parse_args()

    ad = ERPNextAdapter(base_url="http://localhost:8080", site=args.site)
    if not ad.health():
        print(f"{args.site} is not healthy", file=sys.stderr)
        return 1
    ad.reset(REPO / "artifacts" / "firm_seeds" / "firm_A.sql")

    results = [probe(ad, *p) for p in PROBES]
    from erpbench.seeds import FIRM_DATA

    data = FIRM_DATA["A"]
    results += [probe(ad, *p)
                for p in _doc_probes(data.customers[0], data.items[0][0])]

    mapping: dict[str, list[str]] = {}
    for r in results:
        status = "ok" if r["ok"] else f"FAILED {r.get('error','')[:60]}"
        print(f"  {r['primary']:16} {status:12} derived: {r['derived']}")
        if r["ok"]:
            mapping[r["primary"]] = r["derived"]

    dest = REPO / "artifacts" / "derived_doctypes.json"
    dest.write_text(json.dumps(
        {"measured_against": "firm_A seed, ERPNext v15.99.1",
         "method": "known-good primary write, whole-database diff, nothing "
                   "else acting",
         "derived_by_primary": mapping}, indent=2, sort_keys=True) + "\n")
    print(f"\nwrote {dest.name}: {len(mapping)} primary writes, "
          f"{len({d for v in mapping.values() for d in v})} distinct derived doctypes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
