"""Browser observation: an indexed accessibility view of the page.

Text, not pixels. Screenshots cost an order of magnitude more tokens and are
less stable across renders, and this is the observation format a competent
browser agent would use, so the baseline is not strawmanned.

Every interactive element is tagged in the DOM with a stable ref, so an
action can name an element without the model inventing a CSS selector.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

SNAPSHOT_JS = r"""
() => {
  const INTERACTIVE = 'a[href], button, input, select, textarea, ' +
    '[role=button], [role=link], [role=textbox], [role=combobox], ' +
    '[role=checkbox], [role=option], [role=tab], [contenteditable=true], ' +
    '[data-fieldname] .control-input, .list-row, .grid-row';
  const seen = new Map();
  let counter = 0;

  const visible = (el) => {
    const r = el.getBoundingClientRect();
    if (r.width < 1 || r.height < 1) return false;
    const s = getComputedStyle(el);
    return s.visibility !== 'hidden' && s.display !== 'none' && s.opacity !== '0';
  };
  const label = (el) => {
    const aria = el.getAttribute('aria-label');
    if (aria) return aria.trim();
    const ph = el.getAttribute('placeholder');
    const fieldname = el.getAttribute('data-fieldname') ||
      (el.closest('[data-fieldname]') || {}).getAttribute?.('data-fieldname');
    const labelled = el.getAttribute('aria-labelledby');
    if (labelled) {
      const t = document.getElementById(labelled);
      if (t && t.innerText) return t.innerText.trim();
    }
    if (el.labels && el.labels.length) return el.labels[0].innerText.trim();
    const txt = (el.innerText || el.value || '').trim();
    if (txt) return txt.slice(0, 80);
    if (ph) return ph.trim();
    if (fieldname) return fieldname;
    return el.getAttribute('title') || el.getAttribute('name') || '';
  };
  const role = (el) => {
    const explicit = el.getAttribute('role');
    if (explicit) return explicit;
    const tag = el.tagName.toLowerCase();
    if (tag === 'a') return 'link';
    if (tag === 'button') return 'button';
    if (tag === 'select') return 'select';
    if (tag === 'textarea') return 'textbox';
    if (tag === 'input') {
      const t = (el.getAttribute('type') || 'text').toLowerCase();
      if (t === 'checkbox' || t === 'radio') return t;
      if (t === 'submit' || t === 'button') return 'button';
      return 'textbox';
    }
    if (el.classList.contains('list-row')) return 'row';
    if (el.classList.contains('grid-row')) return 'row';
    return 'generic';
  };

  const elements = [];
  for (const el of document.querySelectorAll(INTERACTIVE)) {
    if (!visible(el)) continue;
    const name = label(el);
    if (!name) continue;
    const key = role(el) + '|' + name + '|' + (el.getAttribute('data-fieldname') || '');
    if (seen.has(key)) continue;
    seen.set(key, true);
    const ref = 'e' + (++counter);
    el.setAttribute('data-shadow-ref', ref);
    const entry = { ref, role: role(el), name: name.replace(/\s+/g, ' ').slice(0, 90) };
    if (el.value !== undefined && el.value !== '' && el.type !== 'password') {
      entry.value = String(el.value).slice(0, 60);
    }
    elements.push(entry);
    if (counter > 220) break;
  }

  const main = document.querySelector('.layout-main-section, main, #body') || document.body;
  let text = (main.innerText || '').replace(/\n{2,}/g, '\n').trim();

  return {
    url: location.href,
    title: document.title,
    elements,
    text: text.slice(0, 6000),
  };
}
"""


@dataclass
class Observation:
    url: str
    title: str
    elements: list[dict[str, Any]]
    text: str

    def render(self, max_elements: int = 120, max_text: int = 3000) -> str:
        lines = [f"URL: {self.url}", f"TITLE: {self.title}", "ELEMENTS:"]
        for el in self.elements[:max_elements]:
            value = f' value="{el["value"]}"' if el.get("value") else ""
            lines.append(f'  [{el["ref"]}] {el["role"]} "{el["name"]}"{value}')
        if len(self.elements) > max_elements:
            lines.append(f"  … {len(self.elements) - max_elements} more elements")
        lines.append("PAGE TEXT:")
        lines.append(self.text[:max_text])
        return "\n".join(lines)

    def find(self, role: str | None = None, name: str | None = None,
             contains: bool = True) -> dict[str, Any] | None:
        for el in self.elements:
            if role and el["role"] != role:
                continue
            if name:
                target, candidate = name.lower(), el["name"].lower()
                if not (target in candidate if contains else target == candidate):
                    continue
            return el
        return None


def snapshot(page: Any) -> Observation:
    data = page.evaluate(SNAPSHOT_JS)
    return Observation(url=data["url"], title=data["title"],
                       elements=data["elements"], text=data["text"])
