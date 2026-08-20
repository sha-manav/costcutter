"""The documented ERPNext/Frappe endpoint surface, for ground-truth scoring.

`endpoint_recall` asks: of the API endpoints the demonstrations actually
exercised, how many did synthesis turn into a callable tool? The list below
is the documented surface an integrator would find in the ERPNext REST API
docs; it is used to classify observed traffic, never to guide synthesis.

Reference: https://docs.frappe.io/framework/user/en/api/rest
"""
from __future__ import annotations

import re

# (regex, documented name, category)
DOCUMENTED_ENDPOINTS: list[tuple[re.Pattern[str], str, str]] = [
    (re.compile(r"^/api/resource/([^/]+)$"), "resource.list_or_create", "crud"),
    (re.compile(r"^/api/resource/([^/]+)/([^/]+)$"), "resource.read_update_delete", "crud"),
    (re.compile(r"^/api/method/login$"), "login", "auth"),
    (re.compile(r"^/api/method/logout$"), "logout", "auth"),
    (re.compile(r"^/api/method/frappe\.client\.get_list$"), "client.get_list", "query"),
    (re.compile(r"^/api/method/frappe\.client\.get_count$"), "client.get_count", "query"),
    (re.compile(r"^/api/method/frappe\.client\.get$"), "client.get", "query"),
    (re.compile(r"^/api/method/frappe\.client\.get_value$"), "client.get_value", "query"),
    (re.compile(r"^/api/method/frappe\.client\.set_value$"), "client.set_value", "write"),
    (re.compile(r"^/api/method/frappe\.client\.insert$"), "client.insert", "write"),
    (re.compile(r"^/api/method/frappe\.client\.save$"), "client.save", "write"),
    (re.compile(r"^/api/method/frappe\.client\.submit$"), "client.submit", "write"),
    (re.compile(r"^/api/method/frappe\.client\.cancel$"), "client.cancel", "write"),
    (re.compile(r"^/api/method/frappe\.client\.delete$"), "client.delete", "write"),
    (re.compile(r"^/api/method/frappe\.desk\.reportview\.get$"), "reportview.get", "query"),
    (re.compile(r"^/api/method/frappe\.desk\.reportview\.get_count$"), "reportview.get_count", "query"),
    (re.compile(r"^/api/method/frappe\.desk\.form\.save\.savedocs$"), "form.savedocs", "write"),
    (re.compile(r"^/api/method/frappe\.desk\.form\.load\.getdoc$"), "form.getdoc", "query"),
    (re.compile(r"^/api/method/frappe\.desk\.search\.search_link$"), "search.search_link", "query"),
    (re.compile(r"^/api/method/frappe\.desk\.search\.search_widget$"), "search.search_widget", "query"),
    (re.compile(r"^/api/method/frappe\.desk\.query_report\.run$"), "query_report.run", "report"),
    (re.compile(r"^/api/method/erpnext\..*"), "erpnext.whitelisted_method", "domain"),
    (re.compile(r"^/api/method/frappe\..*"), "frappe.whitelisted_method", "framework"),
]


def classify_endpoint(method: str, path: str) -> tuple[str, str]:
    """Map an observed request to a documented endpoint name and category."""
    for pattern, name, category in DOCUMENTED_ENDPOINTS:
        if pattern.match(path):
            if name == "resource.list_or_create":
                return (f"resource.{'create' if method == 'POST' else 'list'}", "crud")
            if name == "resource.read_update_delete":
                verb = {"GET": "read", "PUT": "update", "DELETE": "delete",
                        "POST": "update"}.get(method, method.lower())
                return (f"resource.{verb}", "crud")
            return (name, category)
    return ("undocumented", "other")
