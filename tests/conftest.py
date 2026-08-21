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
