"""cli.py — Yantresh entry point.

    python cli.py "create hello.txt containing 'namaste'"
    python cli.py --workdir ./workspace --config yantresh.toml "task…"

Exit codes: 0 = complete, 2 = escalated (structured, never a hang).
The JSON report is the Phase-0 dashboard.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict
from pathlib import Path

import tools as toolbox
from agent import Agent
from config import load_config
from ledger import Ledger
from router import ModelRouter

# minimal env-key sanity map; litellm resolves the actual keys itself
_KEY_HINTS = {"deepseek/": "DEEPSEEK_API_KEY", "openai/": "OPENAI_API_KEY",
              "openrouter/": "OPENROUTER_API_KEY", "anthropic/": "ANTHROPIC_API_KEY"}


def main() -> int:
    parser = argparse.ArgumentParser(prog="yantresh", description="bounded 1+1 agent runner")
    parser.add_argument("task", help="the task to execute")
    parser.add_argument("--config", default="yantresh.toml")
    parser.add_argument("--workdir", default=None, help="override the tool jail directory")
    parser.add_argument(
        "--profile", default="default", choices=("default", "demo"),
        help="bounds/budget profile from yantresh.toml (default: 'default')",
    )
    args = parser.parse_args()

    cfg = load_config(args.config, profile=args.profile)

    missing = {
        hint
        for route in cfg.routes.values()
        for model in (route.model, *route.fallbacks)
        for prefix, hint in _KEY_HINTS.items()
        if model.startswith(prefix) and not os.environ.get(hint)
    }
    if missing:
        print(f"error: missing env var(s): {', '.join(sorted(missing))} "
              f"(set them in your shell or a .env you source — never in code)", file=sys.stderr)
        return 1

    workdir = Path(args.workdir).resolve() if args.workdir else cfg.tools.workdir
    toolbox.configure(workdir, cfg.tools.bash_timeout_seconds)

    ledger = Ledger(
        journal_path=cfg.budget.journal_path,
        fuse_path=cfg.budget.fuse_path,
        daily_max_usd=cfg.budget.daily_max_usd,
        bounds=cfg.bounds,
        fallback_usd_per_1k=cfg.budget.fallback_usd_per_1k_tokens,
    )

    print(f"yantresh · worst-case LLM-call ceiling: {cfg.bounds.worst_case_llm_calls()}", file=sys.stderr)
    report = Agent(cfg