"""The browser action space, shared by both the LLM loop and the recipes.

An action names an element either by ref (what a model sees in the
observation) or by role+name (what a recipe knows). Everything else about
execution is identical, so the two paths are comparable.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from shadow.config import Config
from shadow.route.observation import Observation, snapshot

ACTION_SCHEMA = """Reply with one JSON object and nothing else. No prose, no
code fences, no explanation before or after.

LOW-LEVEL ACTIONS — operate on a ref from the ELEMENTS list.

  {"action": "click",    "ref": "e12"}
  {"action": "type",     "ref": "e7", "text": "Acme", "enter": false}
  {"action": "select",   "ref": "e9", "value": "Overdue"}
  {"action": "press",    "key": "Enter"}
  {"action": "navigate", "url": "/app/sales-order"}
  {"action": "scroll",   "direction": "down"}
  {"action": "done",     "answer": "<final answer, or a short confirmation>"}

Use refs exactly as printed in ELEMENTS. A ref is only valid for the
snapshot it came from: after any action that changes the page, re-read
ELEMENTS rather than reusing an old ref.

FORM ACTIONS — address a field by its fieldname, not by ref. These are the
right tool for editing a document. They locate the widget, handle its
particular input behaviour, and wait for it to settle. Prefer them over
click/type on form pages: a click/type pair on a form widget frequently
targets the wrong node or races the widget's own JavaScript.

  {"action": "field",        "field": "qty",           "value": 12}
  {"action": "link",         "field": "customer",      "value": "Juniper Analytics"}
  {"action": "select_field", "field": "status",        "value": "Draft"}
  {"action": "save"}

  `field`        plain input or textarea.
  `link`         a foreign-key field. It is an autocomplete: this types the
                 value and then picks the matching entry from the dropdown.
                 Plain typing leaves such a field unset.
  `select_field` a dropdown (Select) field.
  `save`         saves the current document (Ctrl+S) and waits for the save
                 to complete. A created document does not exist until saved.

CHILD TABLES (GRIDS) — a document's line items live in a grid, and grid cells
cannot be reached by click/type: the row editor re-renders as you interact
with it, which invalidates refs and leaves cells that never become clickable.
Use the grid action instead.

  {"action": "grid", "field": "items", "row": 0, "column": "item_code",
   "value": "SH-SENSOR-01", "is_link": true}
  {"action": "grid", "field": "items", "row": 0, "column": "qty", "value": 12}

  `field`   the grid's own fieldname on the parent document (usually "items").
  `row`     zero-based index of the line. A new document starts with one
            empty row at index 0, so the first line is row 0.
  `column`  the fieldname of the cell within the row.
  `is_link` true when the cell is a foreign key (for example "item_code"),
            so the value is picked from the dropdown rather than just typed.

A TYPICAL DOCUMENT-CREATION SEQUENCE

  {"action": "navigate", "url": "/app/sales-order/new"}
  {"action": "link",  "field": "customer", "value": "Juniper Analytics"}
  {"action": "grid",  "field": "items", "row": 0, "column": "item_code",
   "value": "SH-SENSOR-01", "is_link": true}
  {"action": "grid",  "field": "items", "row": 0, "column": "qty", "value": 12}
  {"action": "save"}
  {"action": "done",  "answer": "created"}

GUIDANCE

  Prefer `navigate` to a known desk route over clicking through menus.
  Read the page text before answering a question; do not guess a number.
  If an action fails twice in the same way, it will not start working on the
  third attempt. Change approach: switch from click/type to a form action,
  navigate somewhere else, or finish with what you have.
  When the goal is a question, finish with the answer. When it is a change,
  make the change, save it, and then finish."""


class ActionError(RuntimeError):
    pass


@dataclass
class ActionOutcome:
    ok: bool
    detail: str
    observation: Observation | None = None
    done: bool = False
    answer: str | None = None


def _resolve(page: Any, obs: Observation, action: dict[str, Any]):
    ref = action.get("ref")
    if not ref and (action.get("role") or action.get("name")):
        el = obs.find(action.get("role"), action.get("name"))
        if el is None and action.get("name"):
            el = obs.find(None, action.get("name"))
        if el is None:
            raise ActionError(
                f"no element matching role={action.get('role')!r} "
                f"name={action.get('name')!r}")
        ref = el["ref"]
    if not ref:
        raise ActionError("action needs a ref or a role/name")
    locator = page.locator(f'[data-shadow-ref="{ref}"]')
    if locator.count() == 0:
        raise ActionError(f"ref {ref} is no longer on the page")
    return locator.first


def settle(page: Any, timeout: int = 8000) -> None:
    try:
        page.wait_for_load_state("networkidle", timeout=timeout)
    except Exception:
        pass


# --------------------------------------------------------------------------
# Frappe-aware composite actions.
#
# Recipes use these; the LLM action space above does not include them. They
# collapse a field edit into one step, which makes the scripted baseline
# *cheaper* than an LLM driving the same UI — the comparison stays honest
# because that bias runs against the result we are trying to show.
# --------------------------------------------------------------------------

def _field_input(page: Any, fieldname: str, root: Any = None):
    sel = (f'[data-fieldname="{fieldname}"] input:not([type=hidden]), '
           f'[data-fieldname="{fieldname}"] textarea')
    loc = (root or page).locator(sel).first
    # A field below the fold is present but not visible, and waiting for
    # visibility on it just burns the timeout. Scroll first.
    try:
        loc.scroll_into_view_if_needed(timeout=5000)
    except Exception:
        pass
    loc.wait_for(state="visible", timeout=15000)
    return loc


def fill_field(page: Any, fieldname: str, value: Any) -> None:
    loc = _field_input(page, fieldname)
    loc.click(timeout=15000)
    loc.fill("")
    loc.type(str(value), delay=15)
    loc.press("Escape")
    page.wait_for_timeout(150)


def fill_link(page: Any, fieldname: str, value: Any) -> None:
    """Link (foreign key) fields are awesomplete widgets: type, then pick."""
    loc = _field_input(page, fieldname)
    loc.click(timeout=15000)
    loc.fill("")
    loc.type(str(value), delay=25)
    option = page.locator(
        f'[data-fieldname="{fieldname}"] .awesomplete li').filter(
        has_text=str(value)).first
    try:
        option.wait_for(state="visible", timeout=8000)
        option.click()
    except Exception:
        # Fall back to keyboard selection when the dropdown renders elsewhere.
        loc.press("Enter")
    page.wait_for_timeout(300)


def fill_select(page: Any, fieldname: str, value: Any) -> None:
    page.locator(f'[data-fieldname="{fieldname}"] select').first.select_option(
        str(value))
    page.wait_for_timeout(150)


def fill_grid(page: Any, grid_field: str, row: int, column: str,
              value: Any, is_link: bool = False) -> None:
    # Frappe's grid heading also carries .grid-row; only data rows have
    # data-idx, so match on that or the first edit targets the header.
    grid = page.locator(
        f'[data-fieldname="{grid_field}"] .grid-row[data-idx]').nth(row)
    grid.wait_for(state="visible", timeout=15000)
    cell = grid.locator(f'[data-fieldname="{column}"]').first
    if cell.count() == 0 or not _visible(cell):
        # A grid shows only a few columns inline; the rest live in the row
        # editor. Without this, a mandatory column that happens to be hidden
        # -- Delivery Date on a Sales Order line -- is simply unreachable,
        # and the document can never be saved.
        _fill_in_row_editor(page, grid, column, value, is_link)
        return
    cell.click(timeout=15000)
    inp = cell.locator("input, textarea").first
    inp.wait_for(state="visible", timeout=10000)
    _type_into(page, inp, value, is_link, scope=cell)
    page.wait_for_timeout(400)


def _visible(loc: Any) -> bool:
    try:
        return bool(loc.is_visible(timeout=1500))
    except Exception:
        return False


def _fill_in_row_editor(page: Any, grid_row: Any, column: str, value: Any,
                        is_link: bool) -> None:
    """Open a grid row's expanded editor and fill a column that is not
    shown inline."""
    opener = grid_row.locator(".btn-open-row, .grid-row-open, .edit-grid-row").first
    if opener.count() == 0:
        raise ActionError(
            f"column {column!r} is not shown in the grid row and the row "
            "editor could not be opened")
    opener.click(timeout=15000)
    form = page.locator(".grid-form-body, .form-in-grid").first
    form.wait_for(state="visible", timeout=10000)
    inp = _field_input(page, column, root=form)
    inp.click(timeout=15000)
    _type_into(page, inp, value, is_link, scope=form)
    # Collapse the editor again so the next action sees the normal grid.
    close = page.locator(".grid-footer-toolbar .btn-open-row, "
                         ".grid-form-body .octicon-triangle-up, "
                         ".grid-row-open .btn-open-row").first
    try:
        if close.count():
            close.click(timeout=4000)
    except Exception:
        page.keyboard.press("Escape")
    page.wait_for_timeout(400)


def _type_into(page: Any, inp: Any, value: Any, is_link: bool,
               scope: Any = None) -> None:
    inp.fill("")
    inp.type(str(value), delay=25)
    if is_link:
        option = page.locator(".awesomplete li").filter(
            has_text=str(value)).first
        try:
            option.wait_for(state="visible", timeout=8000)
            option.click()
            return
        except Exception:
            inp.press("Enter")
            return
    inp.press("Escape")


def _dismiss_modal(page: Any) -> bool:
    """Close a blocking Frappe dialog. Returns whether one was there.

    A failed save leaves its validation dialog open, and the modal backdrop
    intercepts pointer events. Every subsequent click then times out against
    an element that is present, visible and unreachable -- which reads as a
    broken selector and is really an un-dismissed dialog. One template spent
    its entire step budget this way.
    """
    modal = page.locator(".modal.show").first
    try:
        if not modal.is_visible(timeout=800):
            return False
    except Exception:
        return False
    for closer in (".modal.show .btn-modal-close", ".modal.show .modal-header .close",
                   ".modal.show .btn-primary"):
        try:
            btn = page.locator(closer).first
            if btn.count() and btn.is_visible(timeout=500):
                btn.click(timeout=3000)
                page.wait_for_timeout(300)
                break
        except Exception:
            continue
    else:
        page.keyboard.press("Escape")
        page.wait_for_timeout(300)
    try:
        page.locator(".modal-backdrop").first.wait_for(state="detached", timeout=4000)
    except Exception:
        pass
    return True


def save_document(page: Any) -> str:
    """Save the current document and report what actually happened.

    This used to press Ctrl+S and return unconditionally, so a save blocked
    by a validation error reported "save ok". The model, told it had
    succeeded, pressed save twenty more times and ran out of budget with the
    document still unsaved -- a whole template scored 0/9 on it. An action
    that cannot fail is an action the agent cannot recover from.
    """
    page.keyboard.press("Control+s")
    page.wait_for_timeout(1200)
    settle(page, timeout=15000)

    # Frappe reports a blocked save in a modal: mandatory fields, a
    # validation exception, a permission error.
    try:
        dialog = page.locator(".modal.show .modal-body, .msgprint").first
        if dialog.is_visible(timeout=1500):
            message = " ".join((dialog.inner_text() or "").split())[:400]
            _dismiss_modal(page)
            if message:
                return f"NOT SAVED: {message}"
    except Exception:
        pass

    # No modal: the document is saved unless the desk still says otherwise.
    try:
        pill = page.locator(
            ".indicator-pill, .title-area .indicator").first
        state = " ".join((pill.inner_text() or "").split()).lower()
    except Exception:
        state = ""
    if "not saved" in state:
        return ("NOT SAVED: the document is still in an unsaved state and no "
                "error was shown. A required field is probably empty.")
    return f"saved ({state})" if state else "saved"


def perform(page: Any, obs: Observation, action: dict[str, Any],
            cfg: Config) -> ActionOutcome:
    kind = str(action.get("action", "")).lower()
    try:
        if kind == "done":
            _dismiss_modal(page)
            return ActionOutcome(True, "done", done=True,
                                 answer=str(action.get("answer", "")))
        # Anything that touches the page needs the overlay gone first.
        if kind != "navigate":
            _dismiss_modal(page)
        if kind == "navigate":
            url = str(action.get("url", ""))
            if url.startswith("/"):
                url = cfg.app.base_url.rstrip("/") + url
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            settle(page)
        elif kind == "click":
            _resolve(page, obs, action).click(timeout=15000)
            settle(page)
        elif kind == "type":
            loc = _resolve(page, obs, action)
            loc.click(timeout=15000)
            loc.fill("")
            loc.type(str(action.get("text", "")), delay=12)
            if action.get("enter"):
                loc.press("Enter")
            settle(page, timeout=5000)
        elif kind == "select":
            _resolve(page, obs, action).select_option(str(action.get("value", "")))
            settle(page, timeout=5000)
        elif kind == "press":
            page.keyboard.press(str(action.get("key", "Enter")))
            settle(page, timeout=5000)
        elif kind == "scroll":
            delta = -700 if str(action.get("direction", "down")) == "up" else 700
            page.mouse.wheel(0, delta)
        elif kind == "field":
            fill_field(page, action["field"], action["value"])
        elif kind == "link":
            fill_link(page, action["field"], action["value"])
        elif kind == "select_field":
            fill_select(page, action["field"], action["value"])
        elif kind == "grid":
            fill_grid(page, action["field"], int(action.get("row", 0)),
                      action["column"], action["value"],
                      bool(action.get("is_link", False)))
        elif kind == "save":
            detail = save_document(page)
            if detail.startswith("NOT SAVED"):
                return ActionOutcome(False, detail, snapshot(page))
            return ActionOutcome(True, detail, snapshot(page))
        elif kind == "wait":
            page.wait_for_timeout(int(action.get("ms", 800)))
        else:
            return ActionOutcome(False, f"unknown action {kind!r}", snapshot(page))
    except ActionError as exc:
        return ActionOutcome(False, str(exc), snapshot(page))
    except Exception as exc:
        return ActionOutcome(False, f"{type(exc).__name__}: {exc}", snapshot(page))
    return ActionOutcome(True, f"{kind} ok", snapshot(page))
