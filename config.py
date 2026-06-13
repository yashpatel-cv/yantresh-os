"""config.py — typed, frozen view of yantresh.toml.

Behavior lives in config; mechanism lives in code. Nothing else in the
codebase reads the TOML file or the environment directly (the router
delegates key handling to litellm's env conventions — no module ever
touches a raw secret).
"""

from __future__ import annotations

try:  # 3.11+ stdlib; tomli is the identical backport for older interpreters
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # type: ignore[no-redef]

from dataclasses import dataclass
from pathlib import Path

from bounds import Bounds


@dataclass(frozen=True)
class ModelRoute:
    model: str
    fallbacks: tuple[str, ...] = ()
    max_tokens: int = 2048
    temperature: float = 0.2


@dataclass(frozen=True)
class BudgetCfg:
    daily_max_usd: float
    journal_path: Path
    fuse_path: Path
    fallback_usd_per_1k_tokens: float


@dataclass(frozen=True)
class ToolsCfg:
    workdir: Path
    output_cap_chars: int
    bash_timeout_seconds: int


@dataclass(frozen=True)
class DemoCfg:
    """A second, tighter (bounds, budget, tool-allowlist) profile.

    Selected via `load_config(path, profile="demo")`, which substitutes
    `bounds`/`budget` wholesale — agent.py and ledger.py are unaware a
    profile system exists; they just see a smaller Bounds/BudgetCfg.
    `allowed_tools` is consumed by tools.py via YANTRESH_TOOL_ALLOWLIST
    (set by the caller, not read here — config.py stays env-free).
    """

    bounds: Bounds
    budget: BudgetCfg
    allowed_tools: tuple[str, ...]


@dataclass(frozen=True)
class Config:
    routes: dict[str, ModelRoute]
    bounds: Bounds
    budget: BudgetCfg
    tools: ToolsCfg
    call_timeout_seconds: float
    roles_dir: Path
    demo: DemoCfg | None = None


def _build_bounds(raw: dict, fallback: Bounds | None = None) -> Bounds:
    """Bounds from a TOML table, defaulting unset fields to `fallback`
    (the top-level [bounds]) or to Bounds()'s own defaults."""
    d = fallback or Bounds()
    return Bounds(
        max_plan_steps=int(raw.get("max_plan_steps", d.max_plan_steps)),
        max_retries=int(raw.get("max_retries", d.max_retries)),
        max_replans=int(raw.get("max_replans", d.max_replans)),
        max_tool_iters=int(raw.get("max_tool_iters", d.max_tool_iters)),
        max_seconds=float(raw.get("max_seconds", d.max_seconds)),
        max_tokens=int(raw.get("max_tokens", d.max_tokens)),
        max_usd=float(raw.get("max_usd", d.max_usd)),
    )


def _build_budget(raw: dict, base: Path, fallback: BudgetCfg | None = None) -> BudgetCfg:
    daily_default = fallback.daily_max_usd if fallback else 5.0
    fallback_rate = fallback.fallback_usd_per_1k_tokens if fallback else 0.0011
    return BudgetCfg(
        daily_max_usd=float(raw.get("daily_max_usd", daily_default)),
        journal_path=base / raw.get("journal_path", ".yantresh/ledger.jsonl"),
        fuse_path=base / raw.get("fuse_path", ".yantresh/fuse.json"),
        fallback_usd_per_1k_tokens=float(raw.get("fallback_usd_per_1k_tokens", fallback_rate)),
    )


def load_config(path: str | Path = "yantresh.toml", profile: str = "default") -> Config:
    path = Path(path)
    i