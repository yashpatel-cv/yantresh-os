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
import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from bounds import Bounds, Escalate


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
        fuse = self._read_fuse()
        if fuse["state"] == "blown":
            raise Escalate("cost_fuse: fuse is blown — all spending halted")
        if fuse["spent_usd"] >= self.daily_max_usd:
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

    def preflight(self, task_id: str, model: str) -> None:
        """Refuse dispatch when any wall would be breached; journal intent (WAL)."""
        self.check_fuse()
        if self.remaining_tokens(task_id) <= 0:
            raise Escalate("token_budget: task token budget exhausted")
        if self.remaining_usd(task_id) <= 0:
            raise Escalate("cost_cap: task USD budget exhausted")
        self._append({"kind": "intent", "ts": time.time(), "task": task_id, "model": model})

    def settle(self, task_id: str, model: str, input_tokens: int, output_tokens: int, usd: float | None) -> float:
        """Record actuals. Returns the USD charged (estimated if unknown)."""
        tokens = input_tokens + output_tokens
        if usd is None or usd <= 0:
            usd = tokens / 1000 * self.fallback_usd_per_1k
        spend = self.spend(task_id)
        spend.tokens += tokens
        spend.usd += usd
        spend.calls += 1

        fuse = self._read_fuse()
        fuse["spent_usd"] = round(fuse["spent_usd"] + usd, 6)
        self._write_fuse(fuse)

        self._append(
            {
                "kind": "settle",
                "ts": time.time(),
                "task": task_id,
                "model": model,
                "in": input_tokens,
                "out": output_tokens,
                "usd": round(usd, 6),
            }
        )
        return usd

    # ---------------- journal ----------------

    def _append(self, row: dict) -> None:
        with self.journal_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")
            fh.flush()

    def _replay_journal(self) -> None:
        """Crash-loop defense: restarts resume against money already spent."""
        if not self.journal_path.is_file():
            return
        for line in self.journal_path.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("kind") == "settle":
                spend = self.spend(row["task"])
                spend.tokens += int(row.get("in", 0)) + int(row.get("out", 0))
                spend.usd += float(row.get("usd", 0.0))
                spend.calls += 1
