"""The two harnesses — SPEC §2 and §3.

Figure 1 is the spine of this project, and it rests on one claim: the
difference between S1 and S2 is *harness quality*, nothing else. That claim
survives only if the two variants differ in general ways.

    naive      undocumented actions · saves that cannot fail · verbose
               observations
    corrected  reusable domain primitives · concise observations · typed
               errors · recoverable failures

**Both variants execute through the same code path.** `_execute` does not
know which harness called it. The variants differ in three places only: the
action schema shown to the model, how an outcome is reported back, and how
much of the system's response is included. That is what makes the ablation
honest -- a naive run failing is never a capability the corrected harness
secretly added.

INSTRUCTIONS §9 names the invariant most likely to erode: it is always
tempting to add something to the corrected harness that makes one task
easier. Every primitive here is defined over doctypes and fields in general,
and none mentions a template, a firm, or a workflow.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

from erpbench.adapter import AdapterError, RequestRejected, SystemAdapter
from erpbench.instrumentation import Outcome, RunTrace

# --------------------------------------------------------------------------
# Action schemas. The only place the two variants differ in what the model
# is told it can do.
# --------------------------------------------------------------------------

NAIVE_SCHEMA = """Reply with one JSON object and nothing else.

  {"action": "query",  "doctype": "...", "filters": [["field","=","value"]]}
  {"action": "read",   "doctype": "...", "name": "..."}
  {"action": "create", "doctype": "...", "fields": {...}}
  {"action": "update", "doctype": "...", "name": "...", "fields": {...}}
  {"action": "save",   "doctype": "...", "name": "..."}
  {"action": "done",   "answer": "..."}
"""

# Everything the naive schema lists, plus the primitives that make ERP work
# expressible at all. Each is general: a child table is a child table
# whether it holds order lines or contact rows.
CORRECTED_SCHEMA = """Reply with one JSON object and nothing else. No prose,
no code fences, no explanation before or after.

READING

  {"action": "read_policy"}
      The operating policy of the firm you are working for. It governs
      approval thresholds, whether missing records may be created, what
      evidence a document must cite, and whether you may submit or only
      draft. Read it before writing anything.

  {"action": "query",  "doctype": "Customer",
   "filters": [["customer_name","like","%Acme%"]], "fields": ["name"]}
      Search. Returns matching rows. An empty result means no such record
      exists -- it does not mean you should invent one.

  {"action": "read",   "doctype": "Sales Order", "name": "SAL-ORD-0001"}
      One record in full, including its child tables.

WRITING

  {"action": "create", "doctype": "Customer",
   "fields": {"customer_name": "Acme", "customer_group": "Commercial"}}
      Create a record. Returns its name, or a typed error naming the field
      that was missing or invalid.

  {"action": "update", "doctype": "Sales Order", "name": "SAL-ORD-0001",
   "fields": {"po_no": "PO-77"}}
      Change fields on an existing record.

  {"action": "set_child", "doctype": "Sales Order", "name": "SAL-ORD-0001",
   "table": "items",
   "rows": [{"item_code": "WIDGET-1", "qty": 3, "rate": 100}]}
      Replace the rows of a child table. Line items, taxes and schedules all
      live in child tables; they cannot be set with `update`.

  {"action": "submit", "doctype": "Sales Order", "name": "SAL-ORD-0001"}
      Submit a draft, making it final. Some firms allow this and some
      require documents to stay in draft -- the policy says which.

STOPPING

  {"action": "escalate", "reason": "..."}
      Hand the task to a human and write nothing further. Use this when the
      policy requires approval you cannot give.

  {"action": "abstain",  "reason": "..."}
      Decline to act, writing nothing. Use this when the policy forbids the
      action the instruction asks for.

  {"action": "done",     "answer": "..."}
      Finish. If the task asked a question, the answer goes here.

GUIDANCE

  Every action either succeeds or returns a typed error saying why. An error
  is recoverable: read it, change what it names, and try again.
  If the same action fails twice the same way, it will not work the third
  time. Change approach.
  Doing nothing can be correct. Escalating or abstaining when the policy
  requires it counts as success; writing anyway does not.
"""


@dataclass
class StepResult:
    outcome: Outcome
    observation: str
    detail: str = ""
    finished: bool = False
    answer: str | None = None
    escalated: bool = False
    abstained: bool = False


class Harness:
    """Executes an action against the system and reports the outcome.

    `variant` selects the schema and the reporting style. It never selects
    what is possible: `_execute` is shared.
    """

    def __init__(self, adapter: SystemAdapter, variant: str = "corrected",
                 policy_text: str = "") -> None:
        if variant not in ("naive", "corrected"):
            raise ValueError(f"unknown harness variant {variant!r}")
        self.adapter = adapter
        self.variant = variant
        self.policy_text = policy_text

    @property
    def schema(self) -> str:
        return CORRECTED_SCHEMA if self.variant == "corrected" else NAIVE_SCHEMA

    # ------------------------------------------------------------ execution
    def step(self, action: dict[str, Any], trace: RunTrace) -> StepResult:
        kind = str(action.get("action", "")).lower()
        t0 = time.time()

        if kind in ("escalate", "abstain"):
            reason = str(action.get("reason", ""))[:300]
            # The reason IS the answer. `wrote_nothing` and `answer_mentions`
            # are checked as a pair -- SPEC §4 counts silence as a crash, not
            # an abstention -- and they read the answer field. Leaving it
            # empty here scored every correct abstention as "stopped without
            # saying why", which lands hardest on Firm C, whose policy makes
            # abstention the right outcome most often. That is a harness
            # defect producing a capability conclusion, the exact failure
            # INSTRUCTIONS §7 says to suspect first.
            res = StepResult(Outcome.SUCCESS, f"{kind}: {reason}",
                             finished=True, answer=reason,
                             escalated=(kind == "escalate"),
                             abstained=(kind == "abstain"))
        elif kind == "done":
            res = StepResult(Outcome.SUCCESS, "done", finished=True,
                             answer=str(action.get("answer", "")))
        elif kind == "read_policy":
            if self.variant == "naive":
                # Undocumented in the naive schema. A model that guesses it
                # still gets it -- the ablation is about what is *told*, not
                # about hiding capability.
                trace.note_policy_consultation()
                res = StepResult(Outcome.SUCCESS, self.policy_text)
            else:
                trace.note_policy_consultation()
                res = StepResult(Outcome.SUCCESS, self.policy_text)
        else:
            res = self._execute(kind, action)

        trace.record(action, res.outcome, res.detail, time.time() - t0,
                     subgoal=self._subgoal(kind, action))
        return res

    @staticmethod
    def _subgoal(kind: str, action: dict[str, Any]) -> str:
        """Coarse identity used for recovery detection: the same intent
        against the same record, regardless of which fields were tried."""
        return f"{kind}:{action.get('doctype','')}:{action.get('name','')}"

    def _execute(self, kind: str, action: dict[str, Any]) -> StepResult:
        """Shared by both variants. Knows nothing about which called it."""
        try:
            if kind == "query":
                rows = self.adapter.query(
                    str(action.get("doctype", "")),
                    filters=action.get("filters"),
                    fields=action.get("fields") or ["name"],
                    limit=int(action.get("limit", 20)))
                return StepResult(Outcome.SUCCESS, self._render(rows))

            if kind == "read":
                doc = self.adapter.read(str(action.get("doctype", "")),
                                        str(action.get("name", "")))
                return StepResult(Outcome.SUCCESS, self._render(doc))

            if kind in ("create", "update", "set_child", "submit", "save"):
                return self._write(kind, action)

            return StepResult(Outcome.MALFORMED,
                              self._error(f"unknown action {kind!r}"),
                              detail=f"unknown action {kind!r}")
        except AdapterError:
            raise                                  # infrastructure -- not the agent's
        except RequestRejected as exc:
            # The system understood and refused: no such record, bad field,
            # unknown doctype. This is the agent's to recover from, and on
            # the `missing` entity axis it is the *correct* answer -- the
            # record genuinely is not there. Reported as a typed error so the
            # agent can read it and change approach.
            return StepResult(Outcome.TYPED_ERROR, self._error(str(exc)),
                              detail=str(exc)[:300])
        except Exception as exc:                   # pragma: no cover
            return StepResult(Outcome.TYPED_ERROR,
                              self._error(f"{type(exc).__name__}: {exc}"),
                              detail=str(exc)[:300])

    def _write(self, kind: str, action: dict[str, Any]) -> StepResult:
        doctype = str(action.get("doctype", ""))
        client = self.adapter._client()          # type: ignore[attr-defined]

        if kind == "create":
            r = client.post(f"/api/resource/{doctype}",
                            json=dict(action.get("fields") or {}))
        elif kind == "update":
            r = client.put(f"/api/resource/{doctype}/{action.get('name','')}",
                           json=dict(action.get("fields") or {}))
        elif kind == "set_child":
            r = client.put(f"/api/resource/{doctype}/{action.get('name','')}",
                           json={str(action.get("table", "items")):
                                 list(action.get("rows") or [])})
        elif kind == "submit":
            r = client.put(f"/api/resource/{doctype}/{action.get('name','')}",
                           json={"docstatus": 1})
        else:  # "save" -- the naive variant's write verb
            r = client.put(f"/api/resource/{doctype}/{action.get('name','')}",
                           json=dict(action.get("fields") or {}))

        if r.status_code in (200, 201):
            data = r.json().get("data", {})
            name = data.get("name", "")
            return StepResult(Outcome.SUCCESS,
                              self._render(data) if self.variant == "naive"
                              else f"ok: {doctype} {name}".strip())

        # --- the failure path, where the variants genuinely differ ---------
        if self.variant == "naive" and kind == "save":
            # SPEC §2: "saves that cannot fail". This is the defect being
            # ablated, reproduced exactly: the write did not happen and the
            # harness says it did, so the agent has nothing to recover from.
            return StepResult(Outcome.SUCCESS, "saved")

        reason = self._extract_reason(r)
        return StepResult(Outcome.TYPED_ERROR, self._error(reason),
                          detail=reason[:300])

    @staticmethod
    def _extract_reason(response: Any) -> str:
        """Pull the actionable sentence out of a Frappe error page."""
        try:
            payload = response.json()
        except Exception:
            return f"HTTP {response.status_code}"
        for key in ("_server_messages", "exception", "message", "exc_type"):
            value = payload.get(key)
            if not value:
                continue
            if key == "_server_messages":
                try:
                    msgs = json.loads(value)
                    parts = [json.loads(m).get("message", m)
                             if m.strip().startswith("{") else m for m in msgs]
                    return " ".join(str(p) for p in parts)[:400]
                except Exception:
                    return str(value)[:400]
            return str(value)[:400]
        return f"HTTP {response.status_code}"

    # -------------------------------------------------- reporting style
    def _error(self, reason: str) -> str:
        if self.variant == "naive":
            # Verbose and untyped: the reason is buried rather than named.
            return ("The request could not be completed. The server returned "
                    "an error while processing the operation. Full response: "
                    + reason)
        return f"ERROR: {reason}"

    def _render(self, payload: Any) -> str:
        """Observation rendering. The naive variant dumps everything; the
        corrected one returns the fields that carry information."""
        if self.variant == "naive":
            return json.dumps(payload, indent=2, default=str)[:12000]
        if isinstance(payload, list):
            return json.dumps(payload, default=str)[:3000]
        if isinstance(payload, dict):
            trimmed = {k: v for k, v in payload.items()
                       if not k.startswith("_")
                       and k not in ("doctype", "owner", "modified_by",
                                     "creation", "modified", "idx",
                                     "naming_series", "lft", "rgt",
                                     "old_parent", "is_group")
                       and v not in (None, "", 0, [])}
            return json.dumps(trimmed, default=str)[:3000]
        return str(payload)[:3000]
