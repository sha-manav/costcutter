"""Track C — the three-firm demo, built from real logged rollouts.

Three counterfactual goals x three firms, nine panes. Each pane carries the
firm's policy excerpt, the instruction as the model received it, the actions
it actually took, the approval decision, and the database diff colour-coded
against the mutation envelope.

Nothing here is illustrative. Every pane is a row from `artifacts/`, selected
by template and firm and rendered as recorded; the run_id is printed on each
so any pane can be traced back to the file it came from. A demo assembled
from hand-written examples would be the one part of this project that could
not be checked, which is why it is assembled from logs instead.

A *counterfactual* goal is one whose correct outcome genuinely differs by
firm -- a write required at one firm and forbidden at another -- not one whose
assertions differ only because the firms hold different customer names.
"""
from __future__ import annotations

import argparse
import glob
import html
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from erpbench.firms import get_firm                          # noqa: E402

SOURCES = ("artifacts/evaluation_run.jsonl",
           "artifacts/gate_shards/shard_*_of_6.jsonl",
           "artifacts/fixed13_powered.jsonl")

CSS = """
:root { --ink:#111; --mut:#666; --line:#d8d8d8; --add:#1a7f37; --del:#b3261e;
        --warn:#8a6d00; --bg:#fff; --pane:#fafafa; }
* { box-sizing:border-box; }
body { margin:0; padding:28px; background:var(--bg); color:var(--ink);
       font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; }
h1 { font-size:20px; margin:0 0 6px; }
.sub { color:var(--mut); margin:0 0 22px; max-width:74ch; }
.goal { margin:30px 0 10px; padding-top:14px; border-top:2px solid var(--ink); }
.goal h2 { font-size:15px; margin:0 0 2px; }
.goal .instr { color:var(--mut); font-style:italic; }
.row { display:grid; grid-template-columns:repeat(3,1fr); gap:14px; }
.pane { border:1px solid var(--line); border-radius:6px; background:var(--pane);
        padding:12px; display:flex; flex-direction:column; gap:9px; }
.firm { font-weight:700; font-size:13px; }
.firm span { font-weight:400; color:var(--mut); }
.lab { font-size:10px; letter-spacing:.08em; text-transform:uppercase;
       color:var(--mut); margin-bottom:2px; }
.policy { font-size:11.5px; color:#333; background:#fff; border:1px solid var(--line);
          border-radius:4px; padding:7px; white-space:pre-wrap; max-height:120px;
          overflow:auto; }
pre.act { font:11.5px/1.45 ui-monospace,SFMono-Regular,Menlo,monospace; margin:0;
          background:#fff; border:1px solid var(--line); border-radius:4px;
          padding:7px; overflow-x:auto; }
.verdict { font-size:12px; font-weight:600; }
.ok { color:var(--add); } .bad { color:var(--del); } .hold { color:var(--warn); }
.diff { font:11.5px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace; }
.diff div { padding:1px 5px; border-radius:3px; }
.d-req { background:#e6f4ea; color:var(--add); }
.d-forb { background:#fce8e6; color:var(--del); }
.d-none { color:var(--mut); font-style:italic; font-family:inherit; }
.rid { font:10px ui-monospace,monospace; color:#999; margin-top:auto; }
"""


def load_rows() -> list[dict]:
    out = []
    for pat in SOURCES:
        for f in glob.glob(pat):
            try:
                for line in open(f):
                    if line.strip():
                        out.append(json.loads(line))
            except (OSError, ValueError):
                continue
    return out


def counterfactual_goals(rows, want=3):
    """Templates whose correct outcome really differs between firms.

    Measured from the generated envelopes, not asserted: a template counts
    only when some firm requires a write for it and some other firm forbids
    or omits one. Templates whose assertions differ merely because the firms
    hold different customer names are excluded -- that is a rename, not a
    policy difference, and this project does not claim those.
    """
    by = {}
    for r in rows:
        if r.get("harness_variant") != "corrected" or r.get("status") == "error":
            continue
        env = r["verdict"].get("envelope", {})
        writes = bool(env.get("missing_required") or env.get("matched_allowed"))
        by.setdefault(r["template_id"], {}).setdefault(r["firm_id"], set()).add(writes)
    picks = []
    for tid, firms in sorted(by.items()):
        if not {"A", "B", "C"} <= set(firms):
            continue
        profile = {f: any(v) for f, v in firms.items()}
        if len(set(profile.values())) > 1:          # genuinely differs
            picks.append(tid)
    return picks[:want]


def best_row(rows, tid, firm):
    """Prefer a successful rollout: the demo shows correct behaviour."""
    cand = [r for r in rows if r["template_id"] == tid and r["firm_id"] == firm
            and r.get("harness_variant") == "corrected"
            and r.get("status") != "error" and r.get("actions")]
    if not cand:
        return None
    cand.sort(key=lambda r: (not r["verdict"].get("success"), len(r["actions"])))
    return cand[0]


def policy_excerpt(firm_id: str, limit: int = 340) -> str:
    text = " ".join(get_firm(firm_id).policy_text.split())
    return text[:limit] + ("…" if len(text) > limit else "")


def render_actions(row) -> str:
    out = []
    for a in row.get("actions", [])[:9]:
        act = a.get("action")
        act = act if isinstance(act, dict) else {"action": act}
        outcome = a.get("outcome", "")
        mark = {"success": "ok", "typed_error": "err"}.get(outcome, outcome or "")
        out.append(f"{json.dumps(act)[:150]}   -> {mark}")
    if len(row.get("actions", [])) > 9:
        out.append(f"... {len(row['actions']) - 9} more")
    return html.escape("\n".join(out)) or "<em>no actions</em>"


def render_diff(row) -> str:
    env = row["verdict"].get("envelope", {})
    parts = []
    for m in env.get("matched_allowed", [])[:6]:
        parts.append(f'<div class="d-req">+ {html.escape(str(m))}</div>')
    for m in env.get("forbidden", [])[:4]:
        parts.append(f'<div class="d-forb">! forbidden: {html.escape(str(m))}</div>')
    for m in env.get("unexpected", [])[:4]:
        parts.append(f'<div class="d-forb">! unexpected: {html.escape(str(m))}</div>')
    for m in env.get("missing_required", [])[:4]:
        parts.append(f'<div class="d-forb">- missing: {html.escape(str(m))}</div>')
    if not parts:
        parts.append('<div class="d-none">no rows written — '
                     'the database is byte-identical to its seed</div>')
    return "".join(parts)


def verdict_line(row) -> str:
    v = row["verdict"]
    env = v.get("envelope", {})
    if v.get("success"):
        return '<span class="ok">✓ correct for this firm</span>'
    if env.get("forbidden"):
        return '<span class="bad">✗ wrote something this firm forbids</span>'
    if env.get("unexpected"):
        return '<span class="bad">✗ unexpected mutation</span>'
    return '<span class="hold">✗ did not satisfy the required outcome</span>'


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path,
                    default=REPO / "artifacts" / "demo" / "three_firms.html")
    args = ap.parse_args()

    rows = load_rows()
    goals = counterfactual_goals(rows)
    if len(goals) < 3:
        print(f"only {len(goals)} counterfactual goals found", file=sys.stderr)
        if not goals:
            return 1

    parts = [f"<style>{CSS}</style>",
             "<h1>One instruction, three firms, three different correct answers</h1>",
             '<p class="sub">Every pane below is a real logged rollout, rendered '
             'as recorded — policy excerpt, the actions the model took, the '
             'approval decision, and the database diff scored against that '
             "firm's mutation envelope. The <code>run_id</code> on each pane "
             "traces it back to the file it came from. Green is a write the "
             "firm permits; red is one it forbids, or a required write that "
             "never happened.</p>"]

    used = 0
    for tid in goals:
        sample = next((r for f in "ABC" if (r := best_row(rows, tid, f))), None)
        if sample is None:
            continue
        parts.append('<div class="goal">'
                     f'<h2>{html.escape(tid)}</h2>'
                     f'<p class="instr">“{html.escape(sample["instruction"])}”</p>'
                     "</div><div class=\"row\">")
        for firm_id in ("A", "B", "C"):
            row = best_row(rows, tid, firm_id)
            firm = get_firm(firm_id)
            if row is None:
                parts.append('<div class="pane"><div class="firm">'
                             f"Firm {firm_id}</div>"
                             '<div class="d-none">no logged rollout</div></div>')
                continue
            parts.append(
                '<div class="pane">'
                f'<div class="firm">Firm {firm_id} <span>— {html.escape(firm.name)}'
                f"</span></div>"
                f'<div><div class="lab">operating policy</div>'
                f'<div class="policy">{html.escape(policy_excerpt(firm_id))}</div></div>'
                f'<div><div class="lab">actions taken</div>'
                f'<pre class="act">{render_actions(row)}</pre></div>'
                f'<div><div class="lab">approval decision</div>'
                f'<div class="verdict">{verdict_line(row)}</div></div>'
                f'<div><div class="lab">database diff</div>'
                f'<div class="diff">{render_diff(row)}</div></div>'
                f'<div class="rid">{html.escape(str(row.get("run_id")))} · '
                f'{html.escape(str(row.get("model")))}</div>'
                "</div>")
        parts.append("</div>")
        used += 1

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("\n".join(parts))
    print(f"  wrote {args.out}  ({used} goals x 3 firms = {used * 3} panes)")
    for g in goals:
        print(f"    {g}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
