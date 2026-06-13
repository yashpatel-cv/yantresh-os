"""ledger.py — Wall L1: write-ahead cost journal + the daily fuse.

Every call's intent is journaled to disk BEFORE dispatch (a crash-loop
restart resumes against remaining budget, never against zero). The fuse
file is the operator kill switch: set `"state": "blown"` and every
subsequent call in every process is refused before it spends a cent.

Walls L2 (gateway per-key max_budget) and L3 (provider prepaid cap) live
outside this codebase by design — see Crucible plan §A.3.
"""

from __future__ import annotations

import json
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import IO, Iterator

from bounds import Bounds, Escalate

# ----------------------------------------------------------------------
# Cross-platform advisory file lock for the fuse read-modify-write.
#
# Process-per-mission (Crucible §B.2) means N concurrent subprocesses can
# call settle()/check_fuse() against the SAME .yantresh/fuse.json. Without
# a lock, two read-modify-writes can interleave and lose an update — the
# daily fuse would undercount spend, the one Cost Defense invariant this
# whole file exists to uphold. fcntl (POSIX/containers) and msvcrt
# (Windows dev) are both stdlib — no new dependency.
# ----------------------------------------------------------------------
if sys.platform == "win32":  # pragma: no cover - exercised on Windows dev only
    import msvcrt

    def _lock(fh: IO[bytes]) -> None:
        fh.seek(0)
        msvcrt.locking(fh.fileno(), msvcrt.LK_LOCK, 1)

    def _unlock(fh: IO[bytes]) -> None:
        fh.seek(0)
        msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
else:
    import fcntl

    def _lock(fh: IO[bytes]) -> None:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)

    def _unlock(fh: IO[bytes]) -> None:
        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


@contextmanager
def _file_lock(lock_path: Path) -> Iterator[None]:
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as fh:
        fh.seek(0, 2)
        if fh.tell() == 0:
            fh.write(b"0")
            fh.flush()
        _lock(fh)
        try:
            yield
        finally:
            _unlock(fh)


@dataclass
class TaskSpend:
    tokens: int = 0
    usd: float = 0.0
    calls: int = 0


@dataclass
class Ledger:
    journal_path: Path
    fuse_path: Path
    daily_max_usd: float
    bounds: Bounds
    fallback_usd_per_1k: float = 0.0011
    _tasks: dict[str, TaskSpend] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.journal_path.parent.mkdir(parents=True, exist_ok=True)
        self.fuse_path.parent.mkdir(parents=True, exist_ok=True)
        self._fuse_lock_path = self.fuse_path.with_suffix(self.fuse_path.suffix + ".lock")
        self._replay_journal()

    # ---------------- fuse (operator kill switch) ----------------

    def _read_fuse(self) -> dict:
        today = date.today().isoformat()
        if self.fuse_path.is_file():
            try:
                fuse = json.loads(self.fuse_path.read_text())
            except (json.JSONDecodeError, OSError):
                # unreadable fuse fails CLOSED: treat as blown until an operator fixes it
                return {"date": today, "spent_usd": 0.0, "state": "blown"}
            if fuse.get("date") == today:
                return fuse
        return {"date": today, "spent_usd": 0.0, "state": "armed"}

    def _write_fuse(self, fuse: dict) -> None:
        self.fuse_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.fuse_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(fuse, indent=2))
        tmp.replace(self.fuse_path)  # atomic on POSIX

    def check_fuse(self) -> None:
        # Reads are lock-free: _write_fuse's tmp+replace is atomic, so a
        # concurrent writer never produces a torn read — only the
        # blow-the-fuse write below needs to serialize with settle().
        fuse = self._read_fuse()
        if fuse["state"] == "blown":
            raise Escalate("cost_fuse: fuse is blown — all spending halted")
        if fuse["spent_usd"] >= self.daily_max_usd:
            with _file_lock(self._fuse_lock_path):
                fuse = self._read_fuse()  # re-read under lock; another process may have already blown it
                if fuse["state"] != "blown" and fuse["spent_usd"] >= self.daily_max_usd:
                    fuse["state"] = "blown"
                    self._write_fuse(fuse)
            raise Escalate(
                f"cost_fuse: daily budget exhausted (${fuse['spent_usd']:.4f} >= ${self.daily_max_usd})"
            )

    # ---------------- per-task accounting ----------------

    def spend(self, task_id: str) -> TaskSpend:
        return self._tasks.setdefault(task_id, TaskSpend())

    def remaining_tokens(self, task_id: str) -> int:
        return max(self.bounds.max_tokens - self.spend(task_id).tokens, 0)

    def remaining_usd(self, task_id: str) -> float:
        return max(self.bounds.max_usd - self.spend(task_id).usd, 0.0)

    def preflight(self, task_id: str, model: