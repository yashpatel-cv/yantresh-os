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
class Config:
    routes: dict[str, ModelRoute]
    bounds: Bounds
    budget: BudgetCfg
    tools: ToolsCfg
    call_timeout_seconds: float
    roles_dir: Path


def load_config(path: str | Path = "yantresh.toml") -> Config:
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"config not found: {path}")
    with path.open("rb") as fh:
        raw = tomllib.load(fh)

    routes: dict[str, ModelRoute] = {}
    for role, spec in raw.get("models", {}).items():
        routes[role] = ModelRoute(
            model=spec["model"],
            fallbacks=tuple(spec.get("fallbacks", [])),
            max_tokens=int(spec.get("max_tokens", 2048)),
            temperature=float(spec.get("temperature", 0.2)),
        )
    if not routes:
        raise ValueError("yantresh.toml: at least one [models.<role>] is required")

    b = raw.get("bounds", {})
    bounds = Bounds(
        max_plan_steps=int(b.get("max_plan_steps", 8)),
        max_retries=int(b.get("max_retries", 2)),
        max_replans=int(b.get("max_replans", 2)),
        max_tool_iters=int(b.get("max_tool_iters", 6)),
        max_seconds=float(b.get("max_seconds", 600)),
        max_tokens=int(b.get("max_tokens", 200_000)),
        max_usd=float(b.get("max_usd", 0.50)),
    )

    bud = raw.get("budget", {})
    base = path.parent
    budget = BudgetCfg(
        daily_max_usd=float(bud.get("daily_max_usd", 5.0)),
        journal_path=base / bud.get("journal_path", ".yantresh/ledger.jsonl"),
        fuse_path=base / bud.get("fuse_path", ".yantresh/fuse.json"),
        fallback_usd_per_1k_tokens=float(bud.get("fallback_usd_per_1k_tokens", 0.0011)),
    )

    t = raw.get("tools", {})
    tools = ToolsCfg(
        workdir=(base / t.get("workdir", "./workspace")).resolve(),
        output_cap_chars=int(t.get("output_cap_chars", 4000)),
        bash_timeout_seconds=int(t.get("bash_timeout_seconds", 20)),
    )

    return Config(
        routes=routes,
        bounds=bounds,
        budget=budget,
        tools=tools,
        call_timeout_seconds=float(raw.get("router", {}).get("call_timeout_seconds", 60)),
        roles_dir=base / "roles",
    )
