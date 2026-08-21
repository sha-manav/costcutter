"""The instance lock. Two harnesses resetting one database is a wrong run."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from shadow.bench.exclusive import InstanceBusy, instance_lock


def test_a_second_harness_is_refused(tmp_path: Path):
    lock = tmp_path / "lock"
    with instance_lock("first", lock):
        with pytest.raises(InstanceBusy):
            with instance_lock("second", lock):
                pass
    # Released on exit, so the next run is not blocked by the last one.
    with instance_lock("third", lock):
        assert lock.exists()
    assert not lock.exists()


def test_a_lock_left_by_a_dead_process_is_reclaimed(tmp_path: Path):
    """A harness killed by a wrapper timeout should not need a human to
    clear the lock before the next run."""
    lock = tmp_path / "lock"
    dead = 999_999
    assert not Path(f"/proc/{dead}").exists()
    lock.write_text(f"{dead} ghost-run")
    with instance_lock("next", lock):
        assert lock.read_text().startswith(str(os.getpid()))
