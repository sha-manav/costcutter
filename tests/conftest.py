"""Test-suite defaults.

The suite must never call a paid API. `config.yaml` names a real provider
when a benchmark is being run, so every test gets the offline provider
forced unless it explicitly builds a client of its own — otherwise switching
the config for a run turns `pytest` into a billing event.
"""
from __future__ import annotations

import pytest

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
