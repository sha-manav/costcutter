"""Mutation classification.

read        — every step is a GET, or a POST to a known list/report endpoint
write       — any step creates or updates state
destructive — deletes, cancels, submits, or anything irreversible

The Frappe wrinkle: its desk UI issues POSTs for list queries, so method
alone would classify half of a read-only workload as a write. The endpoint
patterns below correct for that.
"""
from __future__ import annotations

import re

from shadow.capture.schema import MutationClass, ToolStep

DESTRUCTIVE_RE = re.compile(r"(delete|cancel|remove|submit|amend|trash)", re.I)
# POST endpoints that are semantically reads.
READ_POST_RE = re.compile(
    r"(get_list|get_count|reportview\.get|search_link|search_widget|"
    r"get_doc|frappe\.client\.get\b|query_report|get_dashboard|"
    r"frappe\.desk\.search|get_data|run_query_report)", re.I)
WRITE_METHODS = {"POST", "PUT", "PATCH"}


def classify_step(step: ToolStep) -> MutationClass:
    path = step.path_template
    if step.method == "DELETE" or DESTRUCTIVE_RE.search(path):
        return "destructive"
    if step.method in WRITE_METHODS:
        return "read" if READ_POST_RE.search(path) else "write"
    return "read"


def classify_steps(steps: list[ToolStep]) -> MutationClass:
    order = {"read": 0, "write": 1, "destructive": 2}
    worst: MutationClass = "read"
    for step in steps:
        cls = classify_step(step)
        if order[cls] > order[worst]:
            worst = cls
    return worst
