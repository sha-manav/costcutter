"""UI recipes: how a competent user performs each task template in the desk.

Recipes are generators over the browser action space. They yield actions and
receive the resulting Observation, which means the same recipe can drive

* `bench/generate_traffic.py` — producing demonstration traffic on OBSERVE
  templates, and
* the deterministic browser policy in `route/browser_agent.py` — standing in
  for an LLM when no model credentials are available.

Recipes read the page like an agent does: they parse the observation, they
do not query the API. Nothing here is visible to `shadow/distill/`.
"""
from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Any, Callable, Generator
from urllib.parse import quote

from shadow.route.observation import Observation

Recipe = Generator[dict[str, Any], Observation, None]

MONEY_RE = re.compile(r"\$\s*([\d,]+\.\d{2})")
SO_ID_RE = re.compile(r"SAL-ORD-\d{4}-\d{5}")
SINV_ID_RE = re.compile(r"ACC-SINV-\d{4}-\d{5}")


def _money(text: str) -> list[float]:
    return [float(m.replace(",", "")) for m in MONEY_RE.findall(text)]


def _url(route: str) -> str:
    return "/app/" + route


def _q(value: Any) -> str:
    return quote(str(value))


def _future_date(days: int = 14) -> str:
    """Frappe's default display format is dd-mm-yyyy."""
    return (date.today() + timedelta(days=days)).strftime("%d-%m-%Y")


def _rows_after_header(text: str) -> list[str]:
    """Everything after the datatable's '<n> of <m>' row-count marker."""
    parts = re.split(r"\n\d+ of \d+\n", text, maxsplit=1)
    return parts[1].splitlines() if len(parts) > 1 else text.splitlines()


# --------------------------------------------------------------------------
# read recipes
# --------------------------------------------------------------------------

def customer_outstanding(params: dict) -> Recipe:
    obs = yield {"action": "navigate",
                 "url": _url(f"sales-invoice?customer={_q(params['customer'])}")}
    obs = yield {"action": "wait", "ms": 2500}
    total = sum(_money(obs.text))
    yield {"action": "done", "answer": f"{total:.2f}"}


def overdue_invoice_count(params: dict) -> Recipe:
    obs = yield {"action": "navigate", "url": _url("sales-invoice?status=Overdue")}
    obs = yield {"action": "wait", "ms": 2500}
    count = len(set(SINV_ID_RE.findall(obs.text)))
    yield {"action": "done", "answer": str(count)}


def stock_on_hand(params: dict) -> Recipe:
    item = params["item_code"]
    obs = yield {"action": "navigate", "url": _url(f"bin?item_code={_q(item)}")}
    obs = yield {"action": "wait", "ms": 2500}
    # Bin list columns: ID | Item Code | Warehouse | Actual Qty | Ordered Qty
    lines = _rows_after_header(obs.text)
    total = 0.0
    for i, line in enumerate(lines):
        if line.strip() == item and i + 2 < len(lines):
            try:
                total += float(lines[i + 2].replace(",", ""))
            except ValueError:
                pass
    yield {"action": "done", "answer": f"{total:g}"}


def customer_order_count(params: dict) -> Recipe:
    obs = yield {"action": "navigate",
                 "url": _url(f"sales-order?customer={_q(params['customer'])}")}
    obs = yield {"action": "wait", "ms": 2500}
    yield {"action": "done", "answer": str(len(set(SO_ID_RE.findall(obs.text))))}


def item_price(params: dict) -> Recipe:
    obs = yield {"action": "navigate",
                 "url": _url(f"item-price?item_code={_q(params['item_code'])}"
                             f"&price_list={_q('Standard Selling')}")}
    obs = yield {"action": "wait", "ms": 2500}
    amounts = _money(obs.text)
    yield {"action": "done", "answer": f"{amounts[0]:.2f}" if amounts else "not found"}


def latest_order_total(params: dict) -> Recipe:
    obs = yield {"action": "navigate",
                 "url": _url(f"sales-order?customer={_q(params['customer'])}")}
    obs = yield {"action": "wait", "ms": 2500}
    amounts = _money(obs.text)
    yield {"action": "done", "answer": f"{amounts[0]:.2f}" if amounts else "not found"}


def items_below_price(params: dict) -> Recipe:
    obs = yield {"action": "navigate",
                 "url": _url(f"item-price?price_list={_q('Standard Selling')}")}
    obs = yield {"action": "wait", "ms": 3000}
    threshold = float(params["max_rate"])
    count = sum(1 for amount in _money(obs.text) if amount < threshold)
    yield {"action": "done", "answer": str(count)}


# --------------------------------------------------------------------------
# write recipes
# --------------------------------------------------------------------------

def create_customer(params: dict) -> Recipe:
    yield {"action": "navigate", "url": _url("customer/new")}
    yield {"action": "wait", "ms": 2500}
    yield {"action": "field", "field": "customer_name",
           "value": params["customer_name"]}
    yield {"action": "link", "field": "customer_group", "value": "Commercial"}
    yield {"action": "link", "field": "territory", "value": "All Territories"}
    yield {"action": "save"}
    obs = yield {"action": "wait", "ms": 1500}
    yield {"action": "done", "answer": f"created customer at {obs.url}"}


def create_supplier(params: dict) -> Recipe:
    yield {"action": "navigate", "url": _url("supplier/new")}
    yield {"action": "wait", "ms": 2500}
    yield {"action": "field", "field": "supplier_name",
           "value": params["supplier_name"]}
    yield {"action": "link", "field": "supplier_group", "value": "Services"}
    yield {"action": "save"}
    obs = yield {"action": "wait", "ms": 1500}
    yield {"action": "done", "answer": f"created supplier at {obs.url}"}


def create_item(params: dict) -> Recipe:
    yield {"action": "navigate", "url": _url("item/new")}
    yield {"action": "wait", "ms": 2500}
    yield {"action": "field", "field": "item_code", "value": params["item_code"]}
    yield {"action": "field", "field": "item_name", "value": params["item_name"]}
    yield {"action": "link", "field": "item_group", "value": "Products"}
    yield {"action": "save"}
    obs = yield {"action": "wait", "ms": 1500}
    yield {"action": "done", "answer": f"created item at {obs.url}"}


def create_sales_order(params: dict) -> Recipe:
    yield {"action": "navigate", "url": _url("sales-order/new")}
    yield {"action": "wait", "ms": 3000}
    yield {"action": "link", "field": "customer", "value": params["customer"]}
    yield {"action": "wait", "ms": 1200}
    # Delivery date is mandatory on every item row; setting it on the header
    # propagates it, which is what a user does.
    yield {"action": "field", "field": "delivery_date", "value": _future_date()}
    yield {"action": "grid", "field": "items", "row": 0, "column": "item_code",
           "value": params["item_code"], "is_link": True}
    yield {"action": "grid", "field": "items", "row": 0, "column": "qty",
           "value": params["qty"]}
    yield {"action": "save"}
    obs = yield {"action": "wait", "ms": 2000}
    yield {"action": "done", "answer": f"created sales order at {obs.url}"}


def create_sales_invoice(params: dict) -> Recipe:
    yield {"action": "navigate", "url": _url("sales-invoice/new")}
    yield {"action": "wait", "ms": 3000}
    yield {"action": "link", "field": "customer", "value": params["customer"]}
    yield {"action": "wait", "ms": 1200}
    yield {"action": "grid", "field": "items", "row": 0, "column": "item_code",
           "value": params["item_code"], "is_link": True}
    yield {"action": "grid", "field": "items", "row": 0, "column": "qty",
           "value": params["qty"]}
    yield {"action": "save"}
    obs = yield {"action": "wait", "ms": 2000}
    yield {"action": "done", "answer": f"created sales invoice at {obs.url}"}


# ---- record types written only by OBSERVE templates -----------------------
# Deliberately the same shape as create_customer: navigate to the new-document
# route, fill the fields, save. What differs between them is the record type
# and its fields, which is exactly the variation the write-transfer prediction
# needs.

def _create_document(route: str, fields: list[tuple[str, Any]],
                     links: list[tuple[str, Any]] | None = None) -> Recipe:
    yield {"action": "navigate", "url": _url(f"{route}/new")}
    yield {"action": "wait", "ms": 2500}
    for name, value in fields:
        yield {"action": "field", "field": name, "value": value}
    for name, value in links or []:
        yield {"action": "link", "field": name, "value": value}
    yield {"action": "save"}
    obs = yield {"action": "wait", "ms": 1500}
    yield {"action": "done", "answer": f"created at {obs.url}"}


def create_contact(params: dict) -> Recipe:
    yield from _create_document("contact", [
        ("first_name", params["first_name"]),
        ("last_name", params["last_name"]),
    ])


def create_lead(params: dict) -> Recipe:
    yield from _create_document("lead", [
        ("company_name", params["company_name"]),
        ("first_name", params["first_name"]),
    ])


def create_warehouse(params: dict) -> Recipe:
    yield from _create_document("warehouse", [
        ("warehouse_name", params["warehouse_name"]),
    ])


def create_item_group(params: dict) -> Recipe:
    yield from _create_document(
        "item-group", [("item_group_name", params["item_group_name"])],
        links=[("parent_item_group", "All Item Groups")])


def create_supplier_group(params: dict) -> Recipe:
    yield from _create_document(
        "supplier-group",
        [("supplier_group_name", params["supplier_group_name"])],
        links=[("parent_supplier_group", "All Supplier Groups")])


def create_territory(params: dict) -> Recipe:
    yield from _create_document(
        "territory", [("territory_name", params["territory_name"])],
        links=[("parent_territory", "All Territories")])


def update_item_price(params: dict) -> Recipe:
    obs = yield {"action": "navigate",
                 "url": _url(f"item-price?item_code={_q(params['item_code'])}"
                             f"&price_list={_q('Standard Selling')}")}
    obs = yield {"action": "wait", "ms": 2500}
    row = obs.find("link", params["item_code"]) or obs.find("row", params["item_code"])
    if row is None:
        yield {"action": "done", "answer": "item price row not found"}
        return
    yield {"action": "click", "ref": row["ref"]}
    yield {"action": "wait", "ms": 2500}
    yield {"action": "field", "field": "price_list_rate", "value": params["new_rate"]}
    yield {"action": "save"}
    obs = yield {"action": "wait", "ms": 1500}
    yield {"action": "done", "answer": f"updated price at {obs.url}"}


def update_customer_group(params: dict) -> Recipe:
    yield {"action": "navigate", "url": _url(f"customer/{_q(params['customer'])}")}
    yield {"action": "wait", "ms": 3000}
    yield {"action": "link", "field": "customer_group",
           "value": params["customer_group"]}
    yield {"action": "save"}
    obs = yield {"action": "wait", "ms": 1500}
    yield {"action": "done", "answer": f"updated customer at {obs.url}"}


RECIPES: dict[str, Callable[[dict], Recipe]] = {
    "T01_customer_outstanding": customer_outstanding,
    "T02_overdue_invoices": overdue_invoice_count,
    "T03_stock_on_hand": stock_on_hand,
    "T04_customer_open_orders": customer_order_count,
    "T05_item_price": item_price,
    "T06_latest_order_total": latest_order_total,
    "T07_items_below_price": items_below_price,
    "T08_create_customer": create_customer,
    "T09_create_sales_order": create_sales_order,
    "T10_create_supplier": create_supplier,
    "T11_update_item_price": update_item_price,
    "T12_create_item": create_item,
    "T13_update_customer_group": update_customer_group,
    "T14_create_sales_invoice": create_sales_invoice,
    "T15_create_contact": create_contact,
    "T16_create_lead": create_lead,
    "T17_create_warehouse": create_warehouse,
    "T18_create_item_group": create_item_group,
    "T19_create_supplier_group": create_supplier_group,
    "T20_create_territory": create_territory,
}


# --------------------------------------------------------------------------
# In-distribution (D…) recipes.
#
# These generate the demonstration traffic for the in-distribution regime.
# They drive the same desk surfaces as the T… recipes -- filtered list views
# for reads, flat document forms for writes -- because the point of that
# regime is to observe ordinary work, not unusual work.
# --------------------------------------------------------------------------

def d_customer_invoice_count(params: dict) -> Recipe:
    obs = yield {"action": "navigate",
                 "url": _url(f"sales-invoice?customer={_q(params['customer'])}")}
    obs = yield {"action": "wait", "ms": 2500}
    yield {"action": "done",
           "answer": str(len(set(SINV_ID_RE.findall(obs.text))))}


def d_customer_invoice_total(params: dict) -> Recipe:
    obs = yield {"action": "navigate",
                 "url": _url(f"sales-invoice?customer={_q(params['customer'])}")}
    obs = yield {"action": "wait", "ms": 2500}
    yield {"action": "done", "answer": f"{sum(_money(obs.text)):.2f}"}


def d_customer_order_count(params: dict) -> Recipe:
    obs = yield {"action": "navigate",
                 "url": _url(f"sales-order?customer={_q(params['customer'])}")}
    obs = yield {"action": "wait", "ms": 2500}
    yield {"action": "done", "answer": str(len(set(SO_ID_RE.findall(obs.text))))}


def d_item_stock_in_warehouse(params: dict) -> Recipe:
    item, warehouse = params["item_code"], params["warehouse"]
    obs = yield {"action": "navigate",
                 "url": _url(f"bin?item_code={_q(item)}&warehouse={_q(warehouse)}")}
    obs = yield {"action": "wait", "ms": 2500}
    lines = _rows_after_header(obs.text)
    total = 0.0
    for i, line in enumerate(lines):
        if line.strip() == item and i + 2 < len(lines):
            try:
                total += float(lines[i + 2].replace(",", ""))
            except ValueError:
                pass
    yield {"action": "done", "answer": f"{total:g}"}


def d_item_selling_price(params: dict) -> Recipe:
    item = params["item_code"]
    obs = yield {"action": "navigate",
                 "url": _url(f"item-price?item_code={_q(item)}"
                             "&price_list=Standard%20Selling")}
    obs = yield {"action": "wait", "ms": 2500}
    prices = _money(obs.text)
    yield {"action": "done", "answer": f"{prices[0]:.2f}" if prices else "0"}


def d_create_customer(params: dict) -> Recipe:
    yield {"action": "navigate", "url": _url("customer/new")}
    yield {"action": "wait", "ms": 2500}
    yield {"action": "field", "field": "customer_name",
           "value": params["customer_name"]}
    yield {"action": "link", "field": "customer_group", "value": "Commercial"}
    yield {"action": "link", "field": "territory", "value": "All Territories"}
    yield {"action": "save"}
    obs = yield {"action": "wait", "ms": 1500}
    yield {"action": "done", "answer": f"created customer at {obs.url}"}


def d_create_contact(params: dict) -> Recipe:
    yield {"action": "navigate", "url": _url("contact/new")}
    yield {"action": "wait", "ms": 2500}
    yield {"action": "field", "field": "first_name", "value": params["first_name"]}
    yield {"action": "field", "field": "last_name", "value": params["last_name"]}
    yield {"action": "save"}
    obs = yield {"action": "wait", "ms": 1500}
    yield {"action": "done", "answer": f"created contact at {obs.url}"}


def d_create_lead(params: dict) -> Recipe:
    yield {"action": "navigate", "url": _url("lead/new")}
    yield {"action": "wait", "ms": 2500}
    yield {"action": "field", "field": "lead_name", "value": params["lead_name"]}
    yield {"action": "save"}
    obs = yield {"action": "wait", "ms": 1500}
    yield {"action": "done", "answer": f"created lead at {obs.url}"}


def d_create_warehouse(params: dict) -> Recipe:
    yield {"action": "navigate", "url": _url("warehouse/new")}
    yield {"action": "wait", "ms": 2500}
    yield {"action": "field", "field": "warehouse_name",
           "value": params["warehouse_name"]}
    yield {"action": "save"}
    obs = yield {"action": "wait", "ms": 1500}
    yield {"action": "done", "answer": f"created warehouse at {obs.url}"}


def d_create_territory(params: dict) -> Recipe:
    yield {"action": "navigate", "url": _url("territory/new")}
    yield {"action": "wait", "ms": 2500}
    yield {"action": "field", "field": "territory_name",
           "value": params["territory_name"]}
    yield {"action": "link", "field": "parent_territory", "value": "All Territories"}
    yield {"action": "save"}
    obs = yield {"action": "wait", "ms": 1500}
    yield {"action": "done", "answer": f"created territory at {obs.url}"}


RECIPES.update({
    "D01_customer_invoice_count": d_customer_invoice_count,
    "D02_customer_invoice_total": d_customer_invoice_total,
    "D03_customer_order_count": d_customer_order_count,
    "D04_item_stock_in_warehouse": d_item_stock_in_warehouse,
    "D05_item_selling_price": d_item_selling_price,
    "D06_create_customer": d_create_customer,
    "D07_create_contact": d_create_contact,
    "D08_create_lead": d_create_lead,
    "D09_create_warehouse": d_create_warehouse,
    "D10_create_territory": d_create_territory,
})
