"""Test-suite defaults.

The suite must never call a paid API. `config.yaml` names a real provider
when a benchmark is being run, so every test gets the offline provider
forced unless it explicitly builds a client of its own — otherwise switching
the config for a run turns `pytest` into a billing event.
"""
from __future__ import annotations

import pathlib

import pytest

from erpbench import gate

from shadow.config import get_config


@pytest.fixture(autouse=True)
def offline_provider():
    cfg = get_config()
    original = cfg.models.provider
    cfg.models.provider = "offline"
    try:
        yield
    finally:
        cfg.models.provider = original


@pytest.fixture(autouse=True)
def artifacts_are_read_only(tmp_path, monkeypatch):
    """The suite must never write into the real `artifacts/`.

    It already did, twice, and neither was noticed by a passing test: a halt
    test overwrote artifacts/HALT.md with a fabricated reason, and the same
    test appended five invented rows to the spend ledger. Both survived
    review because they cost $0.00 and the suite stayed green — an audit
    trail that still adds up is exactly the kind of wrong that does not
    announce itself.

    Redirecting it for every test makes the protection structural.

    Scoped to `preflight.ARTIFACTS`, which is where both offenders wrote.
    `gate.ARTIFACTS` is deliberately left alone: it is also the *read* path
    for the firm seed images, and redirecting it makes every seeded test
    fail on a missing seed rather than on anything real.
    """
    from erpbench import preflight

    sandbox = tmp_path / "artifacts"
    sandbox.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(preflight, "ARTIFACTS", sandbox)
    yield sandbox


@pytest.fixture(autouse=True)
def firm_seeds_exist(tmp_path_factory, monkeypatch):
    """Let the suite run from a clean clone.

    The firm seed images are ~7.8MB of ERPNext dump each, produced by
    `build_firm_seeds_docker.py` against a live bench, and are gitignored.
    `gate.firm_seed` refuses to proceed without them -- correctly, since a
    missing seed silently measuring the wrong world is worse than a halt --
    so 24 gate tests failed on a fresh clone with no way to pass short of
    building ERPNext first.

    That made `pytest` a Docker-dependent step, which it should not be: the
    suite tests scoring, halting and bookkeeping logic, none of which needs a
    real dump. Stand-in files satisfy the existence check; every test that
    touches one uses a FakeAdapter that never reads it. A test that genuinely
    needs a real seed should build one explicitly rather than rely on the
    developer having run Docker earlier.
    """
    real = pathlib.Path(__file__).resolve().parent.parent / "artifacts" / "firm_seeds"
    if all((real / f"firm_{f}.sql").exists() for f in ("A", "B", "C")):
        return                                  # a built checkout; use the real ones
    stand_in = tmp_path_factory.mktemp("firm_seeds")
    for f in ("A", "B", "C"):
        (stand_in / f"firm_{f}.sql").write_text(
            f"-- stand-in seed for firm {f}; see conftest.firm_seeds_exist\n")
    monkeypatch.setattr(gate, "ARTIFACTS", stand_in.parent)
    monkeypatch.setattr(gate, "firm_seed",
                        lambda fid: stand_in / f"firm_{fid}.sql")
