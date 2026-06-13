"""api.py — FastAPI seam over the Amatya/Karta orchestrator (v2).

agent.py, bounds.py, tools.py are imported only transitively (by the
cli.py subprocess this file spawns) — this file never imports Agent and
never touches the tool jail itself. That is the isolation design:

    Absolute Sandbox Isolation (process-per-mission)
    --------------------------------------------------
    Each mission is `python cli.py --workdir <mission_dir> --profile
    <profile> <task>`, run as its own OS process via
    asyncio.create_subprocess_exec. Filesystem jailing (tools._jail),
    bounds enforcement, and ledger settlement all happen *inside* that
    subprocess exactly as they do for a human running the CLI — zero
    changes to agent.py. Concurrency is therefore safe by construction:
    there is no shared Python-process global state to race. The only
    shared resource is the on-disk fuse, guarded by ledger.py's new
    cross-process file lock.

    Dual-lane API
    --------------------------------------------------
    /v1/missions       — private, X-API-Key required, profile="default",
                          full tool registry, own ledger/fuse.
    /v1/demo/missions  — public, no key, profile="demo" (tiny bounds,
                          read_file-only via YANTRESH_TOOL_ALLOWLIST), its
                          own ledger/fuse so public traffic can never
                          starve the private daily budget. Only mounted
                          if [bounds.demo]/[budget.demo] exist in
                          yantresh.toml (config.py: Config.demo).

    uvicorn api:app --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import asyncio
import hmac
import json
import os
import shutil
import sys
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, Field

from bounds import Bounds, Escalate
from config import Config, load_config
from ledger import Ledger

APP_DIR = Path(__file__).resolve().parent
CONFIG_PATH = os.environ.get("YANTRESH_CONFIG", "yantresh.toml")
API_KEY = os.environ.get("YANTRESH_API_KEY", "")
MAX_CONCURRENT_MISSIONS = max(1, int(os.environ.get("YANTRESH_MAX_CONCURRENCY", "2")))
MAX_CONCURRENT_DEMO_MISSIONS = max(1, int(os.environ.get("YANTRESH_MAX_DEMO_CONCURRENCY", "1")))
MAX_MISSION_RECORDS = 500           # bounded in-memory job table (Zero Bloat: no Redis)
MISSION_TTL_SECONDS = int(os.environ.get("YANTRESH_MISSION_TTL_SECONDS", "3600"))
SUBPROCESS_GRACE_SECONDS = 15        # slack added to bounds.max_seconds before SIGKILL

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class MissionRequest(BaseModel):
    task: str = Field(..., min_length=1, max_length=4000, description="Task for Amatya/Karta to execute")


class DemoMissionRequest(BaseModel):
    task: str = Field(..., min_length=1, max_length=500, description="Task for the read-only demo lane")


class MissionAccepted(BaseModel):
    mission_id: str
    status: Literal["queued"]


class MissionRecord(BaseModel):
    mission_id: str
    status: Literal["queued", "running", "complete", "escalated", "error"]
    submitted_at: float
    report: dict[str, Any] | None = None
    error: str | None = None


# ---------------------------------------------------------------------------
# Process-lifetime state
# ---------------------------------------------------------------------------

class Lane(BaseModel):
    """Everything that differs between the private and demo lanes."""
    model_config = {"arbitrary_types_allowed": True}

    profile: str
    bounds: Bounds
    ledger: Ledger
    tool_allowlist: tuple[str, ...]
    missions_root: Path
    sem: asyncio.Semaphore
    records: dict[str, MissionRecord]


class AppState:
    cfg: Config
    private: Lane
    demo: Lane | None


state = AppState()


def _build_lane(cfg: Config, profile: str, bounds: Bounds, budget, tool_allowlist: tuple[str, ...], concurrency: int) -> Lane:
    ledger = Ledger(
        journal_path=budget.journal_path,
        fuse_path=budget.fuse_path,
        daily_max_usd=budget.daily_max_usd,
        bounds=bounds,
        fallback_usd_per_1k=budget.fallback_usd_per_1k_tokens,
    )
    return Lane(
        profile=profile,
        bounds=bounds,
        ledger=ledger,
        tool_allowlist=tool_allowlist,
        missions_root=cfg.tools.workdir / "missions" / profile,
        sem=asyncio.Semaphore(concurrency),
        records={},
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not API_KEY:
        # Fail closed — same posture as ledger._read_fuse() on a corrupt fuse file.
        raise RuntimeError("YANTRESH_API_KEY is not set — refusing to start")

    cfg = load_config(CONFIG_PATH)  # profile="default" — Config.demo (if any) carries its own bounds/budget
    state.cfg = cfg
    state.private = _build_lane(cfg, "default", cfg.bounds, cfg.budget, (), MAX_CONCURRENT_MISSIONS)

    if cfg.demo is not None:
        state.demo = _build_lane(
            cfg, "demo", cfg.demo.bounds, cfg.demo.budget, cfg.demo.allowed_tools, MAX_CONCURRENT_DEMO_MISSIONS
        )
    else:
        state.demo = None

    yield


app = FastAPI(title="Yantresh OS API", version="2.0.0", lifespan=lifespan)


# ---------------------------------------------------------------------------
# Auth (Wall L0 — static token, constant-time compare; demo lane has none)
# ---------------------------------------------------------------------------

def verify_api_key(api_key: str | None = Depends(_api_key_header)) -> None:
    if api_key is None or not hmac.compare_digest(api_key, API_KEY):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid or missing X-API-Key")


# ---------------------------------------------------------------------------
# Mission lifecycle (shared by both lanes)
# ---------------------------------------------------------------------------

def _evict_if_full(records: dict[str, MissionRecord]) -> None:
    if len(records) < MAX_MISSION_RECORDS:
        return
    for mid, rec in sorted(records.items(), key=lambda kv: kv[1].submitted_at):
        if rec.status in ("complete", "escalated", "error"):
            del records[mid]
            return
    raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="mission table full")


async def _reap(mission_dir: Path, delay: float) -> None:
    await asyncio.sleep(delay)
    shutil.rmtree(mission_dir, ignore_errors=True)


async def _run_mission(lane: Lane, mission_id: str, task: str) -> None:
    """Spawn `cli.py` as an isolated subprocess — one process, one
    filesystem jail, one set of OS resource limits, per mission."""
    record = lane.records[mission_id]
    record.status = "running"
    mission_dir = lane.missions_root / mission_id

    env = dict(os.environ)
    if lane.tool_allowlist:
        env["YANTRESH_TOOL_ALLOWLIST"] = ",".join(lane.tool_allowlist)
    else:
        env.pop("YANTRESH_TOOL_ALLOWLIST", None)

    argv = [
        sys.executable, str(APP_DIR / "cli.py"),
        "--config", CONFIG_PATH,
        "--profile", lane.profile,
        "-