"""Oracle post-condition checks — the only definition of task success.

Read tasks are graded on the answer the agent reported, compared against a
value computed here from the documented API. Write tasks are graded on
database state, so an agent that claims success without changing anything
fails.

Never imported by shadow/distill/.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable

from oracle.client import OracleClient

NUMBER_RE = re.compile(r"-?\d[\d,]*\.?\d*")


@dataclass
class CheckResult:
    ok: bool
    detail: str
    expected: Any = None
    actual: Any = None


def _extract_number(answer: str) -> float | None:
    if answer is None:
        return None
    matches = NUMBER_RE.findall(str(answer).replace(",", ""))
    if not matches:
        return None
    # The reported figure is normally the last number in a sentence.
    try:
        return float(matches[-1].replace(",", ""))
    except ValueError:
        return None


def _numeric(answer: str, expected: float, tol: float = 0.02) -> CheckResult:
    got = _extract_number(answer)
    if got is None:
        return CheckResult(False, f"no number in answer {answer!r}", expected, None)
    ok = abs(got - expected) <= max(tol, abs(expected) * 0.001)
    return CheckResult(ok, f"expected {expected}, got {got}", expected, got)


# --------------------------------------------------------------------------
# read checks
# --------------------------------------------------------------------------

def customer_outstanding(oc: OracleClient, params: dict, answer: str, **_) -> CheckResult:
    rows = oc.list("Sales Invoice",
                   filters=[["customer", "=", params["customer"]],
                            ["docstatus", "=", 1]],
                   fields=["outstanding_amount"], limit=500)
    expected = round(sum(float(r["outstanding_amount"] or 0) for r in rows), 2)
    return _numeric(answer, expected)


def overdue_invoice_count(oc: OracleClient, params: dict, answer: str, **_) -> CheckResult:
    rows = oc.list("Sales Invoice",
                   filters=[["docstatus", "=", 1], ["status", "=", "Overdue"]],
                   fields=["name"], limit=500)
    return _numeric(answer, float(len(rows)))


def stock_on_hand(oc: OracleClient, params: dict, answer: str, **_) -> CheckResult:
    rows = oc.list("Bin", filters=[["item_code", "=", params["item_code"]]],
                   fields=["actual_qty"], limit=500)
    expected = round(sum(float(r["actual_qty"] or 0) for r in rows), 3)
    return _numeric(answer, expected)


def customer_order_count(oc: OracleClient, params: dict, answer: str, **_) -> CheckResult:
    rows = oc.list("Sales Order", filters=[["customer", "=", params["customer"]]],
                   fields=["name"], limit=500)
    return _numeric(answer, float(len(rows)))


def item_price(oc: OracleClient, params: dict, answer: str, **_) -> CheckResult:
    rows = oc.list("Item Price",
                   filters=[["item_code", "=", params["item_code"]],
                            ["price_list", "=", "Standard Selling"]],
                   fields=["price_list_rate"], limit=5)
    if not rows:
        return CheckResult(False, "no item price row in seed data")
    return _numeric(answer, float(rows[0]["price_list_rate"]))


def latest_order_total(oc: OracleClient, params: dict, answer: str, **_) -> CheckResult:
    rows = oc.list("Sales Order", filters=[["customer", "=", params["customer"]]],
                   fields=["name", "grand_total", "transaction_date"],
                   limit=50, order_by="transaction_date desc, creation desc")
    if not rows:
        return CheckResult(False, "no sales orders for customer in seed data")
    return _numeric(answer, float(rows[0]["grand_total"]))


def items_below_price(oc: OracleClient, params: dict, answer: str, **_) -> CheckResult:
    rows = oc.list("Item Price",
                   filters=[["price_list", "=", "Standard Selling"],
                            ["price_list_rate", "<", params["max_rate"]]],
                   fields=["name"], limit=500)
    return _numeric(answer, float(len(rows)))


# --------------------------------------------------------------------------
# write checks
# --------------------------------------------------------------------------

def customer_created(oc: OracleClient, params: dict, answer: str, **_) -> CheckResult:
    rows = oc.list("Customer",
                   filters=[["customer_name", "=", params["customer_name"]]],
                   fields=["name", "customer_group"], limit=5)
    if not rows:
        return CheckResult(False, f"customer {params['customer_name']!r} not created")
    return CheckResult(True, f"customer {rows[0]['name']} exists", True, True)


def sales_order_created(oc: OracleClient, params: dict, answer: str, **_) -> CheckResult:
    rows = oc.list("Sales Order", filters=[["customer", "=", params["customer"]]],
                   fields=["name", "creation"], limit=50,
                   order_by="creation desc")
    for row in rows:
        doc = oc.get("Sales Order", row["name"])
        for item in doc.get("items", []):
            if (item.get("item_code") == params["item_code"]
                    and abs(float(item.get("qty", 0)) - float(params["qty"])) < 1e-6):
                return CheckResult(True, f"sales order {row['name']} matches",
                                   True, True)
    return CheckResult(False, "no sales order with the requested item and qty")


def supplier_created(oc: OracleClient, params: dict, answer: str, **_) -> CheckResult:
    rows = oc.list("Supplier",
                   filters=[["supplier_name", "=", params["supplier_name"]]],
                   fields=["name"], limit=5)
    return CheckResult(bool(rows),
                       f"supplier {params['supplier_name']!r} "
                       f"{'exists' if rows else 'missing'}")


def item_price_updated(oc: OracleClient, params: dict, answer: str, **_) -> CheckResult:
    rows = oc.list("Item Price",
                   filters=[["item_code", "=", params["item_code"]],
                            ["price_list", "=", "Standard Selling"]],
                   fields=["price_list_rate"], limit=5)
    if not rows:
        return CheckResult(False, "item price row missing")
    actual = float(rows[0]["price_list_rate"])
    expected = float(params["new_rate"])
    return CheckResult(abs(actual - expected) < 0.01,
                       f"expected rate {expected}, found {actual}", expected, actual)


def item_created(oc: OracleClient, params: dict, answer: str, **_) -> CheckResult:
    rows = oc.list("Item", filters=[["item_code", "=", params["item_code"]]],
                   fields=["name", "item_name"], limit=5)
    return CheckResult(bool(rows),
                       f"item {params['item_code']!r} "
                       f"{'exists' if rows else 'missing'}")


def customer_group_updated(oc: OracleClient, params: dict, answer: str, **_) -> CheckResult:
    rows = oc.list("Customer", filters=[["name", "=", params["customer"]]],
                   fields=["customer_group"], limit=5)
    if not rows:
        return CheckResult(False, "customer missing")
    actual = rows[0]["customer_group"]
    ok = actual == params["customer_group"]
    return CheckResult(ok, f"expected group {params['customer_group']}, found {actual}",
                       params["customer_group"], actual)


def sales_invoice_created(oc: OracleClient, params: dict, answer: str, **_) -> CheckResult:
    rows = oc.list("Sales Invoice", filters=[["customer", "=", params["customer"]]],
                   fields=["name"], limit=50, order_by="creation desc")
    for row in rows:
        doc = oc.get("Sales Invoice", row["name"])
        for item in doc.get("items", []):
            if (item.get("item_code") == params["item_code"]
                    and abs(float(item.get("qty", 0)) - float(params["qty"])) < 1e-6):
                return CheckResult(True, f"sales invoice {row['name']} matches")
    return CheckResult(False, "no sales invoice with the requested item and qty")


# ---- record types written only by OBSERVE templates -----------------------
# Added to test whether write tools transfer across record types. None of
# these is a record type an EVAL template writes.

def _exists(oc: OracleClient, doctype: str, field: str, value: str) -> CheckResult:
    rows = oc.list(doctype, filters=[[field, "=", value]], fields=["name"], limit=5)
    return CheckResult(bool(rows),
                       f"{doctype} {value!r} {'exists' if rows else 'missing'}")


def contact_created(oc: OracleClient, params: dict, answer: str, **_) -> CheckResult:
    rows = oc.list("Contact",
                   filters=[["first_name", "=", params["first_name"]]],
                   fields=["name", "last_name"], limit=5)
    if not rows:
        return CheckResult(False, f"contact {params['first_name']!r} not created")
    return CheckResult(True, f"contact {rows[0]['name']} exists")


def lead_created(oc: OracleClient, params: dict, answer: str, **_) -> CheckResult:
    return _exists(oc, "Lead", "company_name", params["company_name"])


def warehouse_created(oc: OracleClient, params: dict, answer: str, **_) -> CheckResult:
    return _exists(oc, "Warehouse", "warehouse_name", params["warehouse_name"])


def item_group_created(oc: OracleClient, params: dict, answer: str, **_) -> CheckResult:
    return _exists(oc, "Item Group", "item_group_name",
                   params["item_group_name"])


def supplier_group_created(oc: OracleClient, params: dict, answer: str,
                           **_) -> CheckResult:
    return _exists(oc, "Supplier Group", "supplier_group_name",
                   params["supplier_group_name"])


def territory_created(oc: OracleClient, params: dict, answer: str, **_) -> CheckResult:
    return _exists(oc, "Territory", "territory_name", params["territory_name"])


CHECKS: dict[str, Callable[..., CheckResult]] = {
    "customer_outstanding": customer_outstanding,
    "overdue_invoice_count": overdue_invoice_count,
    "stock_on_hand": stock_on_hand,
    "customer_order_count": customer_order_count,
    "item_price": item_price,
    "latest_order_total": latest_order_total,
    "items_below_price": items_below_price,
    "customer_created": customer_created,
    "sales_order_created": sales_order_created,
    "supplier_created": supplier_created,
    "item_price_updated": item_price_updated,
    "item_created": item_created,
    "customer_group_updated": customer_group_updated,
    "sales_invoice_created": sales_invoice_created,
    "contact_created": contact_created,
    "lead_created": lead_created,
    "warehouse_created": warehouse_created,
    "item_group_created": item_group_created,
    "supplier_group_created": supplier_group_created,
    "territory_created": territory_created,
}


def run_check(name: str, oc: OracleClient, params: dict, answer: str) -> CheckResult:
    fn = CHECKS.get(name)
    if fn is None:
        return CheckResult(False, f"no oracle check named {name!r}")
    try:
        return fn(oc, params, answer)
    except Exception as exc:  # an oracle failure must never read as success
        return CheckResult(False, f"oracle error: {type(exc).__name__}: {exc}")


# --------------------------------------------------------------------------
# In-distribution (D…) checks.
#
# Same style as above: every one reads the answer back out of the database
# through the REST API rather than trusting what the agent said.
# --------------------------------------------------------------------------

def customer_territory(oc: OracleClient, params: dict, answer: str, **_) -> CheckResult:
    rows = oc.list("Customer", filters=[["name", "=", params["customer"]]],
                   fields=["territory"], limit=1)
    if not rows:
        return CheckResult(False, f"no customer {params['customer']!r} in seed data")
    expected = str(rows[0]["territory"] or "")
    ok = expected.lower() in (answer or "").lower()
    return CheckResult(ok, f"expected {expected!r}, answer {answer[:60]!r}")


def customer_group_of(oc: OracleClient, params: dict, answer: str, **_) -> CheckResult:
    rows = oc.list("Customer", filters=[["name", "=", params["customer"]]],
                   fields=["customer_group"], limit=1)
    if not rows:
        return CheckResult(False, f"no customer {params['customer']!r} in seed data")
    expected = str(rows[0]["customer_group"] or "")
    ok = expected.lower() in (answer or "").lower()
    return CheckResult(ok, f"expected {expected!r}, answer {answer[:60]!r}")


def customers_in_group(oc: OracleClient, params: dict, answer: str, **_) -> CheckResult:
    rows = oc.list("Customer",
                   filters=[["customer_group", "=", params["customer_group"]]],
                   fields=["name"], limit=500)
    return _numeric(answer, float(len(rows)))


def item_group_of(oc: OracleClient, params: dict, answer: str, **_) -> CheckResult:
    rows = oc.list("Item", filters=[["name", "=", params["item_code"]]],
                   fields=["item_group"], limit=1)
    if not rows:
        return CheckResult(False, f"no item {params['item_code']!r} in seed data")
    expected = str(rows[0]["item_group"] or "")
    ok = expected.lower() in (answer or "").lower()
    return CheckResult(ok, f"expected {expected!r}, answer {answer[:60]!r}")


def items_in_group(oc: OracleClient, params: dict, answer: str, **_) -> CheckResult:
    rows = oc.list("Item", filters=[["item_group", "=", params["item_group"]]],
                   fields=["name"], limit=500)
    return _numeric(answer, float(len(rows)))


def item_stock_in_warehouse(oc: OracleClient, params: dict, answer: str,
                            **_) -> CheckResult:
    rows = oc.list("Bin", filters=[["item_code", "=", params["item_code"]],
                                   ["warehouse", "=", params["warehouse"]]],
                   fields=["actual_qty"], limit=20)
    expected = round(sum(float(r["actual_qty"] or 0) for r in rows), 3)
    return _numeric(answer, expected)


CHECKS.update({
    "customer_territory": customer_territory,
    "customer_group_of": customer_group_of,
    "customers_in_group": customers_in_group,
    "item_group_of": item_group_of,
    "items_in_group": items_in_group,
    "item_stock_in_warehouse": item_stock_in_warehouse,
})


def invoices_for_customer(oc: OracleClient, params: dict, answer: str,
                          **_) -> CheckResult:
    rows = oc.list("Sales Invoice",
                   filters=[["customer", "=", params["customer"]]],
                   fields=["name"], limit=500)
    return _numeric(answer, float(len(rows)))


def customer_invoice_total(oc: OracleClient, params: dict, answer: str,
                           **_) -> CheckResult:
    rows = oc.list("Sales Invoice",
                   filters=[["customer", "=", params["customer"]]],
                   fields=["grand_total"], limit=500)
    expected = round(sum(float(r["grand_total"] or 0) for r in rows), 2)
    return _numeric(answer, expected)


CHECKS.update({
    "invoices_for_customer": invoices_for_customer,
    "customer_invoice_total": customer_invoice_total,
})
