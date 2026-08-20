"""Deterministic seed data for the benchmark site.

Runs INSIDE the frappe environment:

    sudo -u frappe /home/frappe/frappe-bench/env/bin/python \
        oracle/frappe_seed.py --site shadow.localhost

Everything here is fixed: same customers, items, prices and documents every
time, so an oracle check can assert exact post-conditions and a reset
restores a known state.

Part of oracle/: it uses the documented ERPNext API surface and must never
be imported by shadow/distill/.
"""
from __future__ import annotations

import argparse
import sys

COMPANY = "Shadow Traders"
ABBR = "ST"
CURRENCY = "USD"

CUSTOMERS = [
    ("Acme Industrial", "Company", "United States"),
    ("Borealis Freight", "Company", "Canada"),
    ("Cobalt Robotics", "Company", "United States"),
    ("Delta Mining Co", "Company", "Australia"),
    ("Everest Outfitters", "Company", "United States"),
    ("Fjord Marine", "Company", "Norway"),
    ("Granite Construction", "Company", "United States"),
    ("Halcyon Media", "Company", "United Kingdom"),
    ("Ironwood Timber", "Company", "Canada"),
    ("Juniper Analytics", "Company", "United States"),
    ("Kestrel Aviation", "Company", "United States"),
    ("Lumen Optics", "Company", "Germany"),
]

SUPPLIERS = [
    ("Northwind Components", "United States"),
    ("Osprey Metals", "Canada"),
    ("Pacific Polymers", "United States"),
    ("Quarry Stone Ltd", "United Kingdom"),
    ("Redwood Fasteners", "United States"),
]

# (code, name, group, rate, is_stock)
ITEMS = [
    ("SH-BEARING-01", "Precision Bearing 20mm", "Products", 45.00, True),
    ("SH-BEARING-02", "Precision Bearing 32mm", "Products", 62.50, True),
    ("SH-GEAR-01", "Helical Gear 40T", "Products", 120.00, True),
    ("SH-GEAR-02", "Spur Gear 24T", "Products", 88.00, True),
    ("SH-SHAFT-01", "Drive Shaft 500mm", "Products", 210.00, True),
    ("SH-BELT-01", "Timing Belt 900mm", "Products", 34.75, True),
    ("SH-MOTOR-01", "Servo Motor 400W", "Products", 480.00, True),
    ("SH-MOTOR-02", "Stepper Motor NEMA23", "Products", 165.00, True),
    ("SH-SENSOR-01", "Proximity Sensor M12", "Products", 28.90, True),
    ("SH-SENSOR-02", "Load Cell 50kg", "Products", 143.00, True),
    ("SH-PANEL-01", "Control Panel Enclosure", "Products", 320.00, True),
    ("SH-CABLE-01", "Shielded Cable 10m", "Products", 22.40, True),
    ("SH-VALVE-01", "Pneumatic Valve 1/4in", "Products", 57.60, True),
    ("SH-PUMP-01", "Hydraulic Pump 12L", "Products", 690.00, True),
    ("SH-SERVICE-01", "On-site Commissioning", "Services", 950.00, False),
]

# (customer_index, [(item_index, qty)], days_from_today)
SALES_ORDERS = [
    (0, [(0, 10), (5, 4)], -30),
    (1, [(6, 2)], -25),
    (2, [(2, 6), (8, 12)], -21),
    (3, [(4, 3), (11, 20)], -18),
    (4, [(1, 8)], -14),
    (5, [(9, 5), (12, 6)], -11),
    (6, [(13, 1)], -8),
    (7, [(3, 9)], -5),
    (8, [(7, 4), (10, 2)], -3),
    (9, [(14, 1)], -1),
]

# (customer_index, [(item_index, qty)], posting_days_ago, due_days_from_today)
SALES_INVOICES = [
    (0, [(0, 5)], -40, -10),      # overdue
    (1, [(6, 1)], -35, -5),       # overdue
    (2, [(2, 3)], -30, 5),
    (3, [(4, 1)], -20, 10),
    (4, [(1, 4)], -15, -2),       # overdue
    (5, [(9, 2)], -10, 20),
    (6, [(13, 1)], -6, 24),
    (7, [(3, 2)], -2, 28),
]

OPENING_STOCK = 250  # units of every stock item, in the default warehouse


def log(msg: str) -> None:
    print(f"[seed] {msg}", flush=True)


def run_setup_wizard(frappe) -> None:
    from frappe.desk.page.setup_wizard.setup_wizard import setup_complete

    if frappe.db.get_single_value("System Settings", "setup_complete"):
        log("setup wizard already complete")
        return
    log("running setup wizard")
    setup_complete({
        "language": "English (United States)",
        "country": "United States",
        "timezone": "America/New_York",
        "currency": CURRENCY,
        "company_name": COMPANY,
        "company_abbr": ABBR,
        "chart_of_accounts": "Standard",
        "company_tagline": "Shadow benchmark company",
        "bank_account": "Cash",
        "fy_start_date": "2026-01-01",
        "fy_end_date": "2026-12-31",
        "setup_demo": 0,
        "full_name": "Administrator",
        "email": "admin@example.com",
        "password": "admin",
    })
    frappe.db.commit()


def ensure(frappe, doctype: str, name: str, payload: dict):
    if frappe.db.exists(doctype, name):
        return frappe.get_doc(doctype, name)
    doc = frappe.get_doc({"doctype": doctype, **payload})
    doc.insert(ignore_permissions=True, ignore_mandatory=True)
    return doc


def seed_masters(frappe) -> None:
    from frappe.utils import add_days, nowdate

    for name, ctype, country in CUSTOMERS:
        ensure(frappe, "Customer", name, {
            "customer_name": name, "customer_type": ctype,
            "customer_group": "Commercial", "territory": "All Territories",
        })
    log(f"customers: {frappe.db.count('Customer')}")

    for name, country in SUPPLIERS:
        ensure(frappe, "Supplier", name, {
            "supplier_name": name, "supplier_group": "Services",
        })
    log(f"suppliers: {frappe.db.count('Supplier')}")

    for code, name, group, rate, is_stock in ITEMS:
        ensure(frappe, "Item", code, {
            "item_code": code, "item_name": name, "item_group": group,
            "stock_uom": "Nos", "is_stock_item": 1 if is_stock else 0,
            "include_item_in_manufacturing": 0,
            "description": name,
        })
        price_name = frappe.db.get_value(
            "Item Price", {"item_code": code, "price_list": "Standard Selling"})
        if not price_name:
            frappe.get_doc({
                "doctype": "Item Price", "item_code": code,
                "price_list": "Standard Selling", "price_list_rate": rate,
            }).insert(ignore_permissions=True)
    log(f"items: {frappe.db.count('Item')}")
    frappe.db.commit()


def default_warehouse(frappe) -> str:
    for candidate in (f"Stores - {ABBR}", f"Finished Goods - {ABBR}",
                      f"Work In Progress - {ABBR}"):
        if frappe.db.exists("Warehouse", candidate):
            return candidate
    return frappe.db.get_value("Warehouse", {"company": COMPANY, "is_group": 0}, "name")


def seed_stock(frappe) -> None:
    from frappe.utils import add_days, nowdate

    warehouse = default_warehouse(frappe)
    existing = frappe.db.count("Stock Entry", {"docstatus": 1})
    if existing:
        log(f"stock entries already present ({existing})")
        return
    entry = frappe.get_doc({
        "doctype": "Stock Entry",
        "stock_entry_type": "Material Receipt",
        "company": COMPANY,
        "posting_date": add_days(nowdate(), -60),
        "set_posting_time": 1,
        "items": [
            {"item_code": code, "qty": OPENING_STOCK, "t_warehouse": warehouse,
             "basic_rate": rate * 0.6}
            for code, _n, _g, rate, is_stock in ITEMS if is_stock
        ],
    })
    entry.insert(ignore_permissions=True)
    entry.submit()
    frappe.db.commit()
    log(f"opening stock posted to {warehouse}")


def seed_transactions(frappe) -> None:
    from frappe.utils import add_days, nowdate

    warehouse = default_warehouse(frappe)
    if frappe.db.count("Sales Order") == 0:
        for cust_i, lines, days in SALES_ORDERS:
            doc = frappe.get_doc({
                "doctype": "Sales Order",
                "customer": CUSTOMERS[cust_i][0],
                "company": COMPANY,
                "transaction_date": add_days(nowdate(), days),
                "delivery_date": add_days(nowdate(), days + 21),
                "currency": CURRENCY,
                "conversion_rate": 1,
                "selling_price_list": "Standard Selling",
                "price_list_currency": CURRENCY,
                "plc_conversion_rate": 1,
                "items": [
                    {"item_code": ITEMS[i][0], "qty": qty,
                     "rate": ITEMS[i][3], "warehouse": warehouse,
                     "delivery_date": add_days(nowdate(), days + 21)}
                    for i, qty in lines
                ],
            })
            doc.insert(ignore_permissions=True)
            doc.submit()
        frappe.db.commit()
    log(f"sales orders: {frappe.db.count('Sales Order')}")

    if frappe.db.count("Sales Invoice") == 0:
        for cust_i, lines, posted, due in SALES_INVOICES:
            doc = frappe.get_doc({
                "doctype": "Sales Invoice",
                "customer": CUSTOMERS[cust_i][0],
                "company": COMPANY,
                "posting_date": add_days(nowdate(), posted),
                "set_posting_time": 1,
                "due_date": add_days(nowdate(), due),
                "currency": CURRENCY,
                "conversion_rate": 1,
                "selling_price_list": "Standard Selling",
                "price_list_currency": CURRENCY,
                "plc_conversion_rate": 1,
                "update_stock": 0,
                "items": [
                    {"item_code": ITEMS[i][0], "qty": qty, "rate": ITEMS[i][3]}
                    for i, qty in lines
                ],
            })
            doc.insert(ignore_permissions=True)
            doc.submit()
        frappe.db.commit()
    log(f"sales invoices: {frappe.db.count('Sales Invoice')}")


def relax_settings(frappe) -> None:
    """Remove friction that would make UI and API runs diverge."""
    settings = frappe.get_doc("Stock Settings")
    settings.allow_negative_stock = 1
    settings.save(ignore_permissions=True)
    sel = frappe.get_doc("Selling Settings")
    sel.so_required = "No"
    sel.dn_required = "No"
    # Without a default selling price list, every new sales document fails
    # validation in the UI. A real deployment sets this in the setup wizard.
    sel.selling_price_list = "Standard Selling"
    sel.save(ignore_permissions=True)
    buy = frappe.get_doc("Buying Settings")
    buy.buying_price_list = "Standard Buying"
    buy.save(ignore_permissions=True)
    sys_settings = frappe.get_doc("System Settings")
    sys_settings.disable_document_sharing = 0
    sys_settings.save(ignore_permissions=True)
    frappe.db.commit()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--site", default="shadow.localhost")
    args = parser.parse_args()

    import frappe

    frappe.init(site=args.site)
    frappe.connect()
    frappe.set_user("Administrator")
    frappe.flags.in_setup_wizard = True
    try:
        run_setup_wizard(frappe)
        seed_masters(frappe)
        seed_stock(frappe)
        seed_transactions(frappe)
        relax_settings(frappe)
    finally:
        frappe.destroy()
    log("SEED_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
