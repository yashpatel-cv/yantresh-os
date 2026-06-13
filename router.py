"""router.py — the ONE seam between agent logic and any model vendor.

Agent code never imports a provider SDK and never sees an API key.
litellm resolves credentials from env (DEEPSEEK_API_KEY, OPENAI_API_KEY,
OPENROUTER_API_KEY, …) per its standard conventions; swapping vendors is
an edit to yantresh.toml. Every call is: fuse-checked → WAL-journaled →
token-clamped → dispatched with a hard timeout and num_retries=0 →
settled into the ledger. Fallbacks are tried in order and each attempt
is billed honestly. (Crucible §A.1, §A.3)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import litellm

from bounds import Escalate
from config import Config
from ledger import Ledger

litellm.suppress_debug_info = True
# Hidden-multiplier defense: nothing retries beneath us.
litellm.num_retries = 0


def compute_clamp(route_max_tokens: int, remaining_task_tokens: int) -> int:
    """Dynamic clamp: a single call can never out-spend the task budget."""
    clamp = min(route_max_tokens, remaining_task_tokens)
    if clamp < 16:
        raise Escalate("token_budget: remaining budget too small for any useful call")
    return clamp


@dataclass
class RouterResult:
    text: str
    message: dict[str, Any]          # raw assistant message (tool_calls flow)
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    usd: float = 0.0


class ModelRouter:
    def __init__(self, cfg: Config, ledger: Ledger) -> None:
        self._cfg = cfg
        self._ledger = ledger

    def complete(
        self,
        role: str,
        messages: list[dict[str, Any]],
        task_id: str,
        tools: list[dict[str, Any]] | None = None,
        force_json: bool = False,
    ) -> RouterResult:
        route = self._cfg.routes.get(role)
        if route is None:
            raise Escalate(f"router_config: no [models.{role}] route defined")

        clamp = compute_clamp(route.max_tokens, self._ledger.remaining_tokens(task_id))
        last_error: Exception | None = None

        for model in (route.model, *route.fallbacks):
            self._ledger.preflight(task_id, model)  # fuse + budgets + WAL intent
            try:
                response = litellm.completion(
                    model=model,
                    messages=messages,
                    max_tokens=clamp,
                    temperature=route.temperature,
                    timeout=self._cfg.call_timeout_seconds,  # hung-socket defense
                    num_retries=0,
                    tools=tools,
                    response_format={"type": "json_object"} if force_json else None,
                    drop_params=True,  # degrade gracefully on vendors lacking a param
                )
            except Exception as exc:  # noqa: BLE001 — every vendor error = try next wall-approved model
                last_error = exc
                continue

            usage = getattr(response, "usage", None)
            input_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
            output_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
            try:
                usd: float | None = litellm.completion_cost(completion_response=response)
            except Exception:  # noqa: BLE001 — unknown model pricing
                usd = None
            usd = self._ledger.settle(task_id, model, input_tokens, output_tokens, usd)

            choice = response.choices[0]
            message = choice.message
            tool_calls = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                }
                for tc in (message.tool_calls or [])
            ]
            return RouterResult(
                text=message.content or "",
                message={
                    "role": "assistant",
                    "content": message.content,
                    **({"tool_calls": tool_calls} if tool_calls else {}),
                },
                tool_calls=tool_calls,
                model=model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                usd=usd,
            )

        raise Escalate(f"all_models_failed: {type(last_error).__name__}: {last_error}")
