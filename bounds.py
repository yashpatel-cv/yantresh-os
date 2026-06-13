"""bounds.py — the termination core.

Non-termination is made unreachable by a ranking function over N^4:

    V(state) = (replans_left, steps_left, retries_left, tool_iters_left)

Every loop transition must STRICTLY decrease V in lexicographic order;
N^4 under lex order is well-founded, so no infinite run exists regardless
of model behavior. `assert_descent` enforces the proof at runtime — a
violation is a bug and terminates the run, never loops it.

Code-review law: no state transition may be added that does not strictly
decrease V. (Crucible plan §A.2)
"""

from __future__ import annotations

from dataclasses import dataclass

Variant = tuple[int, int, int, int]


class Escalate(Exception):
    """The single terminal escape hatch. Raising this always ends the run
    in a clean `escalated` state — it is control flow, not an error."""


@dataclass(frozen=True)
class Bounds:
    max_plan_steps: int = 8
    max_retries: int = 2
    max_replans: int = 2
    max_tool_iters: int = 6
    max_seconds: float = 600.0
    max_tokens: int = 200_000
    max_usd: float = 0.50

    def __post_init__(self) -> None:
        for name in ("max_plan_steps", "max_retries", "max_replans", "max_tool_iters"):
            if getattr(self, name) < 0:
                raise ValueError(f"Bounds.{name} must be >= 0")
        if self.max_seconds <= 0 or self.max_tokens <= 0 or self.max_usd <= 0:
            raise ValueError("time/token/usd bounds must be positive")

    def variant(self, *, replans: int, plan_len: int, step: int, retries: int, iters: int) -> Variant:
        """Current value of the ranking function V."""
        return (
            self.max_replans - replans,
            max(plan_len - step, 0),
            self.max_retries - retries,
            self.max_tool_iters - iters,
        )

    def worst_case_llm_calls(self) -> int:
        """Closed-form absolute ceiling on LLM calls per task (§A.1).

        (P+1) * [ 1 plan + S * (R+1) * (I worker turns + 1 verify) ]
        """
        s, r, p, i = self.max_plan_steps, self.max_retries, self.max_replans, self.max_tool_iters
        return (p + 1) * (1 + s * (r + 1) * (i + 1 + 1))


def assert_descent(prev: Variant, cur: Variant) -> None:
    """Enforce strict lexicographic decrease of the ranking function.

    Python tuple comparison IS lexicographic order on N^4, so the whole
    termination proof reduces to one comparison.
    """
    if not cur < prev:
        raise Escalate(f"descent_violation: V did not decrease ({prev} -> {cur})")
