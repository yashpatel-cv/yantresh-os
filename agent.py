"""agent.py — the bounded Amatya/Karta loop.

plan → execute (tool loop) → artifact-verify → advance | retry | replan
Every transition strictly decreases the ranking function (bounds.py);
every breach raises the one Escalate that lands in the terminal handler.
There is no code path that loops forever or hangs silently.

Decoupling contract: this file knows nothing about vendors (router seam),
nothing about tool internals (registry), and contains no prompt text
(roles/*.md). It is pure mechanism. (Crucible §B.1)
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Protocol

import tools as toolbox
from bounds import Bounds, Escalate, assert_descent
from config import Config
from ledger import Ledger
from sanitizer import Sanitizer
from tools import ToolError

VALID_DONE_TYPES = {"file_exists", "file_contains", "cmd_ok"}
MAX_INJECTION_INCIDENTS = 3


class RouterLike(Protocol):
    """Anything that completes a chat for a role. Tests inject fakes here."""

    def complete(
        self,
        role: str,
        messages: list[dict[str, Any]],
        task_id: str,
        tools: list[dict[str, Any]] | None = None,
        force_json: bool = False,
    ) -> Any: ...


@dataclass(frozen=True)
class Subtask:
    goal: str
    done: dict[str, Any]


@dataclass
class RunReport:
    task_id: str
    status: str                      # complete | escalated
    reason: str
    steps_done: int
    plan_len: int
    replans: int
    llm_calls: int
    tokens: int
    usd: float
    events: list[dict[str, Any]] = field(default_factory=list)


def extract_json(text: str) -> dict[str, Any]:
    """Parse a JSON object from model output, tolerating fenced/wrapped text."""
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        parsed = json.loads(text[start : end + 1])
        if isinstance(parsed, dict):
            return parsed
    raise ValueError(f"no JSON object in model output: {text[:120]!r}")


class LoopGuard:
    """Deterministic loop detection: same (tool, args, output-head) fingerprint
    appearing 3x in a sliding window of 5 = the worker is going in circles.
    Pure computation — this guard cannot be 'down'. (Crucible §A.1#6)"""

    def __init__(self, window: int = 5, threshold: int = 3) -> None:
        self._window: deque[str] = deque(maxlen=window)
        self._threshold = threshold

    def looping(self, tool: str, args: dict[str, Any], output: str) -> bool:
        fingerprint = hashlib.sha256(
            f"{tool}|{json.dumps(args, sort_keys=True)}|{output[:200]}".encode()
        ).hexdigest()
        self._window.append(fingerprint)
        return self._window.count(fingerprint) >= self._threshold


def check_done(done: dict[str, Any]) -> tuple[bool, str]:
    """Execute a machine-checkable done criterion through the jailed tools.
    Artifacts are ground truth; worker prose never reaches this function."""
    kind = done.get("type")
    try:
        if kind == "file_exists":
            toolbox.execute("read_file", {"path": str(done["path"])})
            return True, f"file exists: {done['path']}"
        if kind == "file_contains":
            content = toolbox.execute("read_file", {"path": str(done["path"])})
            if str(done["text"]) in content:
                return True, f"file contains expected text: {done['path']}"
            return False, f"text {done['text']!r} not found in {done['path']}"
        if kind == "cmd_ok":
            output = toolbox.execute("run_bash", {"command": str(done["cmd"])})
            if output.startswith("[exit 0]"):
                return True, f"command succeeded: {done['cmd']}"
            return False, f"command failed: {output.splitlines()[0]}"
    except (ToolError, KeyError) as exc:
        return False, f"artifact check error: {exc}"
    return False, f"invalid done criterion type: {kind!r}"


class Agent:
    def __init__(self, cfg: Config, router: RouterLike, ledger: Ledger) -> None:
        self._cfg = cfg
        self._b: Bounds = cfg.bounds
        self._router = router
        self._ledger = ledger
        self._amatya = (cfg.roles_dir / "amatya.md").read_text(encoding="utf-8")
        self._karta = (cfg.roles_dir / "karta.md").read_text(encoding="utf-8")

    # ---------------- public ----------------

    def run(self, task: str) -> RunReport:
        task_id = uuid.uuid4().hex[:12]
        deadline = time.monotonic() + self._b.max_seconds
        sanitizer = Sanitizer(max_chars=self._cfg.tools.output_cap_chars)
        events: list[dict[str, Any]] = []
        step = retries = replans = 0
        plan: list[Subtask] = []

        def report(status: str, reason: str) -> RunReport:
            spend = self._ledger.spend(task_id)
            return RunReport(
                task_id=task_id, status=status, reason=reason,
                steps_done=step, plan_len=len(plan), replans=replans,
                llm_calls=spend.calls, tokens=spend.tokens, usd=round(spend.usd, 6),
                events=events,
            )

        try:
            plan = self._plan(task, task_id)
            seen_plans = {self._plan_hash(plan)}
            events.append({"event": "plan", "steps": [s.goal for s in plan]})
            feedback = ""

            while step < len(plan):
                self._tick(deadline)
                if sanitizer.incidents >= MAX_INJECTION_INCIDENTS:
                    raise Escalate("injection_probe: repeated injection attempts in tool output")

                vbase = dict(replans=replans, plan_len=len(plan), step=step, retries=retries)
                prev = self._b.variant(**vbase, iters=0)
                subtask = plan[step]

                worker_report = self._work(subtask, task_id, sanitizer, deadline, vbase, feedback)
                ok, reason = self._verify(subtask, worker_report, task_id)
                events.append({"event": "verify", "step": step, "ok": ok, "reason": reason})

                if ok:
                    step, retries, feedback = step + 1, 0, ""
                    assert_descent(prev, self._b.variant(
                        replans=replans, plan_len=len(plan), step=step, retries=retries, iters=0))
                elif retries < self._b.max_retries:
                    retries += 1
                    feedback = f"Previous attempt failed verification: {reason}"
                    assert_descent(prev, self._b.variant(
                        replans=replans, plan_len=len(plan), step=step, retries=retries, iters=0))
                else:
                    if replans >= self._b.max_replans:
                        raise Escalate(f"replan_ceiling: {reason}")
                    plan = self._replan(task, plan, reason, task_id)
                    plan_hash = self._plan_hash(plan)
                    if plan_hash in seen_plans:
                        raise Escalate("replan_duplicate: supervisor repeated a failed plan")
                    seen_plans.add(plan_hash)
                    replans, step, retries, feedback = replans + 1, 0, 0, ""
                    events.append({"event": "replan", "n": replans, "steps": [s.goal for s in plan]})
                    assert_descent(prev, self._b.variant(
                        replans=replans, plan_len=len(plan), step=step, retries=retries, iters=0))

            return report("complete", "all subtasks verified")
        except Escalate as exc:
            events.append({"event": "escalate", "reason": str(exc)})
            return report("escalated", str(exc))

    # ---------------- supervisor (Amatya) ----------------

    def _plan(self, task: str, task_id: str) -> list[Subtask]:
        request = json.dumps({"mode": "PLAN", "task": task, "max_subtasks": self._b.max_plan_steps})
        result = self._router.complete(
            "amatya",
            [{"role": "system", "content": self._amatya}, {"role": "user", "content": request}],
            task_id,
            force_json=True,
        )
        return self._parse_plan(result.text)

    def _replan(self, task: str, old_plan: list[Subtask], reason: str, task_id: str) -> list[Subtask]:
        request = json.dumps({
            "mode": "REPLAN",
            "task": task,
            "failed_plan": [s.goal for s in old_plan],
            "failure_reason": reason,
        })
        result = self._router.complete(
            "amatya",
            [{"role": "system", "content": self._amatya}, {"role": "user", "content": request}],
            task_id,
            force_json=True,
        )
        return self._parse_plan(result.text)

    def _parse_plan(self, text: str) -> list[Subtask]:
        try:
            items = extract_json(text).get("plan", [])
        except ValueError as exc:
            raise Escalate(f"plan_parse: {exc}") from exc
        if not items:
            raise Escalate("plan_parse: empty plan")
        if len(items) > self._b.max_plan_steps:
            raise Escalate(f"plan_too_long: {len(items)} > {self._b.max_plan_steps}")
        plan: list[Subtask] = []
        for item in items:
            goal = str(item.get("goal", "")).strip()
            done = item.get("done") or {}
            if not goal or done.get("type") not in VALID_DONE_TYPES:
                raise Escalate(f"plan_invalid: subtask missing goal or valid done criterion: {item!r}")
            plan.append(Subtask(goal=goal, done=done))
        return plan

    def _verify(self, subtask: Subtask, worker_report: str, task_id: str) -> tuple[bool, str]:
        """Artifacts first — ground truth no prose can override (Crucible §A.1#5)."""
        artifact_ok, artifact_detail = check_done(subtask.done)
        if not artifact_ok:
            return False, artifact_detail

        request = json.dumps({
            "mode": "VERIFY",
            "subtask": subtask.goal,
            "artifact_check": {"passed": True, "detail": artifact_detail},
            "worker_report": worker_report[:1000],
        })
        result = self._router.complete(
            "amatya",
            [{"role": "system", "content": self._amatya}, {"role": "user", "content": request}],
            task_id,
            force_json=True,
        )
        try:
            verdict = extract_json(result.text)
        except ValueError:
            # Artifact passed and the gate glitched: fail safe, burn a retry.
            return False, "verify_parse: unreadable supervisor verdict"
        return verdict.get("verdict") == "pass", str(verdict.get("reason", ""))[:200]

    # ---------------- worker (Karta) ----------------

    def _work(
        self,
        subtask: Subtask,
        task_id: str,
        sanitizer: Sanitizer,
        deadline: float,
        vbase: dict[str, int],
        feedback: str,
    ) -> str:
        guard = LoopGuard()
        user = f"SUBTASK: {subtask.goal}\nDONE WHEN: {json.dumps(subtask.done)}"
        if feedback:
            user += f"\nNOTE: {feedback}"
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": self._karta},
            {"role": "user", "content": user},
        ]
        iters = 0

        while True:
            self._tick(deadline)
            result = self._router.complete(
                "karta", messages, task_id, tools=toolbox.openai_schemas()
            )
            if not result.tool_calls:
                return result.text.strip() or "BLOCKED: worker returned empty response"

            messages.append(result.message)
            for call in result.tool_calls:
                if iters >= self._b.max_tool_iters:
                    return "BLOCKED: tool budget for this subtask exhausted"
                prev = self._b.variant(**vbase, iters=iters)
                iters += 1
                assert_descent(prev, self._b.variant(**vbase, iters=iters))

                name = call["function"]["name"]
                try:
                    args = json.loads(call["function"]["arguments"] or "{}")
                except json.JSONDecodeError:
                    args = {}
                try:
                    raw = toolbox.execute(name, args)
                except ToolError as exc:
                    raw = f"TOOL ERROR: {exc}"

                if guard.looping(name, args, raw):
                    return f"BLOCKED: loop detected — {name} repeating with identical results"

                messages.append({
                    "role": "tool",
                    "tool_call_id": call["id"],
                    "content": sanitizer.quarantine(raw, source=name),
                })

    # ---------------- shared ----------------

    @staticmethod
    def _plan_hash(plan: list[Subtask]) -> str:
        normalized = json.dumps(
            [{"goal": s.goal.lower().strip(), "done": s.done} for s in plan], sort_keys=True
        )
        return hashlib.sha256(normalized.encode()).hexdigest()

    def _tick(self, deadline: float) -> None:
        if time.monotonic() >= deadline:
            raise Escalate("wall_clock: run deadline exceeded")
