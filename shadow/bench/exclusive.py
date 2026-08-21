"""One harness at a time against the instance under test.

Every benchmark harness resets the database between tasks. Two of them
running at once is not a slow run, it is a wrong one: a reset issued by one
harness lands in the middle of another's task and silently invalidates it.
That happened once during the Sonnet run and cost a task; the lock is here
so it cannot happen twice.
"""
from __future__ import annotations

import errno
import os
from contextlib import contextmanager
from pathlib import Path

LOCK_PATH = Path("/tmp/shadow-instance.lock")


class InstanceBusy(RuntimeError):
    pass


def _holder(path: Path) -> str:
    try:
        return path.read_text().strip() or "unknown"
    except OSError:
        return "unknown"


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


@contextmanager
def instance_lock(label: str, path: Path = LOCK_PATH):
    """Hold the instance for the duration of a harness run."""
    path.parent.mkdir(parents=True, exist_ok=True)
    while True:
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except OSError as exc:
            if exc.errno != errno.EEXIST:
                raise
            held = _holder(path)
            pid = held.split()[0] if held.split() else ""
            # A harness killed by a timeout leaves the file behind. A stale
            # lock should not need a human to clear it.
            if pid.isdigit() and not _alive(int(pid)):
                path.unlink(missing_ok=True)
                continue
            raise InstanceBusy(
                f"the instance is held by {held}; wait for it to finish or "
                f"remove {path} if that process is gone") from None
        break
    try:
        os.write(fd, f"{os.getpid()} {label}".encode())
        os.close(fd)
        yield
    finally:
        path.unlink(missing_ok=True)
