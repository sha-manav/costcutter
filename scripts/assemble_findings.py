#!/usr/bin/env python3
"""Insert the generated result tables into FINDINGS.md.

Keeps every number in the report machine-generated: `docs/findings_body.md`
holds the prose, `artifacts/findings_tables.md` holds the tables, and this
splices them at the marker so nothing is hand-copied.

    python scripts/assemble_findings.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MARKER = "<!-- GENERATED TABLES -->"


def main() -> int:
    body = ROOT / "docs" / "findings_body.md"
    tables = ROOT / "artifacts" / "findings_tables.md"
    out = ROOT / "FINDINGS.md"

    if not body.exists():
        print(f"missing {body}", file=sys.stderr)
        return 2
    if not tables.exists():
        print(f"missing {tables}; run `python -m shadow.cli report` first",
              file=sys.stderr)
        return 2

    text = body.read_text()
    if MARKER not in text:
        print(f"{body} has no {MARKER} marker", file=sys.stderr)
        return 2
    out.write_text(text.replace(MARKER, tables.read_text().rstrip() + "\n"))
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
