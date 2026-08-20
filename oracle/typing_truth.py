"""Ground truth for parameter typing.

Frappe knows the real type of every field. This maps its DocField metadata
onto the JSON-Schema types the inducer emits, so "parameter typing accuracy"
is measured against the application's own schema rather than a guess.

Never imported by shadow/distill/.
"""
from __future__ import annotations

from typing import Any

from oracle.client import OracleClient

# Frappe fieldtype -> the JSON Schema type an inducer should land on.
FIELDTYPE_TO_JSON: dict[str, str] = {
    "Data": "string", "Small Text": "string", "Text": "string",
    "Long Text": "string", "Text Editor": "string", "Code": "string",
    "Link": "string", "Dynamic Link": "string", "Read Only": "string",
    "Password": "string", "Attach": "string", "Attach Image": "string",
    "Select": "string", "Barcode": "string", "Autocomplete": "string",
    "Int": "integer", "Long Int": "integer",
    "Float": "number", "Currency": "number", "Percent": "number",
    "Duration": "number", "Rating": "number",
    "Check": "boolean",
    "Date": "date", "Datetime": "date", "Time": "string",
}


def field_types(oc: OracleClient, doctype: str) -> dict[str, str]:
    """fieldname -> expected JSON type for one doctype.

    Read from the DocType document itself: `DocField` is a child table and
    the REST surface refuses to list it directly.
    """
    doc = oc.get("DocType", doctype)
    rows = list(doc.get("fields") or [])
    try:
        rows += oc.list("Custom Field", filters=[["dt", "=", doctype]],
                        fields=["fieldname", "fieldtype"], limit=500)
    except Exception:
        pass
    out: dict[str, str] = {}
    for row in rows:
        json_type = FIELDTYPE_TO_JSON.get(row.get("fieldtype", ""))
        if json_type and row.get("fieldname"):
            out[row["fieldname"]] = json_type
    # Every doctype has these framework fields.
    out.setdefault("name", "string")
    out.setdefault("docstatus", "integer")
    out.setdefault("idx", "integer")
    out.setdefault("owner", "string")
    out.setdefault("modified_by", "string")
    out.setdefault("creation", "date")
    out.setdefault("modified", "date")
    return out


def score_param_typing(oc: OracleClient, catalog: Any) -> dict[str, Any]:
    """Compare induced parameter types with the application's field types.

    Only parameters whose name matches a real field of a doctype the tool
    touches can be scored; the rest are reported as unscorable rather than
    counted as correct.
    """
    from shadow.verify.replay import watched_doctypes

    cache: dict[str, dict[str, str]] = {}
    total = correct = unscorable = 0
    mistakes: list[dict[str, Any]] = []
    unscored: list[dict[str, Any]] = []

    for spec in catalog.tools:
        doctypes = watched_doctypes(spec)
        known: dict[str, str] = {}
        for dt in doctypes:
            if dt not in cache:
                try:
                    cache[dt] = field_types(oc, dt)
                except Exception:
                    cache[dt] = {}
            known.update(cache[dt])
        for name, schema in spec.params_schema.get("properties", {}).items():
            expected = known.get(name)
            if expected is None:
                unscorable += 1
                unscored.append({"tool": spec.name, "param": name})
                continue
            total += 1
            induced = schema.get("type", "string")
            if schema.get("format") == "date":
                induced = "date"
            if induced == expected:
                correct += 1
            else:
                mistakes.append({"tool": spec.name, "param": name,
                                 "induced": induced, "expected": expected})
    return {
        "scored": total,
        "correct": correct,
        "accuracy": round(correct / total, 4) if total else 0.0,
        "unscorable": unscorable,
        "mistakes": mistakes[:25],
        "unscored": unscored[:25],
    }
