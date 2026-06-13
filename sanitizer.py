"""sanitizer.py — the trust boundary for everything a tool returns.

Tool output is untrusted input (Crucible §A.5). Before it re-enters model
context it is: (1) stripped of fence-marker forgeries, (2) neutralized
against known injection families, (3) truncated head+tail, and (4) wrapped
in a nonce fence the payload cannot fake. Repeated injection attempts in
one run escalate the task — someone is probing.
"""

from __future__ import annotations

import os
import re
import secrets
from dataclasses import dataclass, field

INJECTION_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"ignore\s+(?:all\s+|any\s+)?(?:previous|prior|above)\s+instructions",
        r"disregard\s+(?:all\s+|your\s+)?(?:previous|prior|system)\s+(?:instructions|prompt)",
        r"you\s+are\s+now\s+(?:a|an|in)\b",
        r"(?:^|\n)\s*system\s*:",
        r"new\s+instructions?\s*:",
        r"reveal\s+(?:your\s+)?(?:system\s+prompt|instructions|api\s+key)",
        r"mark\s+(?:this\s+)?(?:task|subtask)\s+(?:as\s+)?(?:complete|verified|done)",
    )
)

_FENCE_FORGERY = re.compile(r"<<\s*/?\s*(?:DATA|END)\s*:[^>]*>>", re.IGNORECASE)
_SECRETY_ENV = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL)", re.IGNORECASE)


def mask_secrets(text: str) -> str:
    """Redact any env-var value that looks like a credential (len >= 8).

    Keys live only in the router/gateway layer, but defense in depth says
    no log line or tool output ever carries one in clear text either.
    """
    for name, value in os.environ.items():
        if len(value) >= 8 and _SECRETY_ENV.search(name):
            text = text.replace(value, f"[MASKED:{name}]")
    return text


@dataclass
class Sanitizer:
    max_chars: int = 4000
    nonce: str = field(default_factory=lambda: secrets.token_hex(8))
    incidents: int = 0

    def quarantine(self, payload: str, source: str) -> str:
        """Sanitize + fence one tool output for re-entry into context."""
        payload = mask_secrets(payload)
        payload = _FENCE_FORGERY.sub("[STRIPPED:fence-forgery]", payload)

        for pattern in INJECTION_PATTERNS:
            payload, hits = pattern.subn("[NEUTRALIZED]", payload)
            self.incidents += hits

        if len(payload) > self.max_chars:
            head = payload[: self.max_chars // 2]
            tail = payload[-self.max_chars // 2 :]
            payload = f"{head}\n…[TRUNCATED {len(payload) - self.max_chars} chars]…\n{tail}"

        return (
            f"<<DATA:{self.nonce} source={source} trust=untrusted>>\n"
            f"{payload}\n"
            f"<<END:{self.nonce}>>"
        )
