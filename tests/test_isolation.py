"""Rule 1, enforced structurally: synthesis may not see the oracle.

`shadow/distill/` turns observed traffic into tools. If it could import
`oracle/` it could read the documented API surface or the success checks,
and every synthesis number would be meaningless.
"""
from __future__ import annotations

import ast
import pytest
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
FORBIDDEN_ROOTS = {"oracle"}
GUARDED_PACKAGES = ["shadow/distill", "shadow/capture"]


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text())
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                found.add(node.module.split(".")[0])
    return found


def test_distill_does_not_import_oracle():
    offenders = []
    for package in GUARDED_PACKAGES:
        for path in (REPO_ROOT / package).rglob("*.py"):
            bad = _imports(path) & FORBIDDEN_ROOTS
            if bad:
                offenders.append(f"{path.relative_to(REPO_ROOT)} imports {sorted(bad)}")
    assert not offenders, (
        "the synthesis pipeline must never see the oracle:\n" + "\n".join(offenders))


def test_distill_does_not_reach_oracle_transitively():
    """A module distill imports must not drag the oracle in behind it."""
    import importlib
    import shadow.distill.emit  # noqa: F401
    import shadow.distill.filter  # noqa: F401
    import shadow.distill.induce  # noqa: F401
    import shadow.distill.provenance  # noqa: F401
    import shadow.distill.segment  # noqa: F401

    leaked = [name for name in sys.modules
              if name == "oracle" or name.startswith("oracle.")]
    assert not leaked, f"oracle modules loaded by importing distill: {leaked}"


def test_oracle_package_is_documented_as_ground_truth():
    readme = (REPO_ROOT / "oracle" / "README.md").read_text().lower()
    assert "never" in readme and "distill" in readme


# --- reset integrity -------------------------------------------------------
# 2026-08-26: MariaDB's InnoDB watchdog aborted the server mid-run when six
# shards reset concurrently (dict_sys.latch, innodb_fatal_semaphore_wait).
# Four sites were left holding 146-532 of 698 tables. Nothing detected the
# truncation; it surfaced three steps later as "ERPNext site unhealthy".

def test_reset_serializes_ddl_across_processes():
    """The drop/restore section must hold the cross-process lock.

    Without it, concurrent resets contend on one InnoDB dictionary latch until
    the watchdog kills the server. Asserted on the source because the failure
    only reproduces under real concurrent DDL against a real server.
    """
    import inspect

    from oracle import reset as reset_mod

    src = inspect.getsource(reset_mod.reset)
    assert "_ddl_lock()" in src, (
        "reset() must serialize its DDL with _ddl_lock(); concurrent "
        "drop+restore crashed MariaDB on 2026-08-26")


def test_reset_rejects_a_partial_restore(tmp_path, monkeypatch):
    """A restore that leaves fewer tables than the dump creates must raise."""
    from oracle import reset as reset_mod

    dump = tmp_path / "seed.sql"
    dump.write_bytes(b"CREATE TABLE `a` (i INT);\nCREATE TABLE `b` (i INT);\n")
    assert reset_mod._expected_tables(dump) == 2

    monkeypatch.setattr(reset_mod, "site_db", lambda site: ("db", "u", "p"))
    monkeypatch.setattr(reset_mod, "_kill_connections", lambda db: 0)

    class Done:
        returncode, stdout, stderr = 0, "1\n", ""

    monkeypatch.setattr(reset_mod.subprocess, "run", lambda *a, **k: Done())
    with pytest.raises(reset_mod.ResetIncomplete) as e:
        reset_mod.reset("s", source=dump)
    assert "1 of 2 tables" in str(e.value)
