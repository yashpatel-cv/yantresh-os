"""tools.py — the declarative tool registry.

Adding a tool to Yantresh = writing ONE type-hinted function with the
@tool decorator. Its OpenAI function schema is derived from the signature
and docstring; registration, jailing, and sanitization are automatic.
No core logic changes, ever. (Crucible §B.1)

Phase-0 surface: exactly three tools, all local, all jailed, none with
network access (lint rule + blocklist — the meter cannot be bypassed
by a tool that phones an LLM itself, §A.1#3).
"""

from __future__ import annotations

import inspect
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, get_type_hints


class ToolError(Exception):
    """Recoverable tool failure — reported to the worker, never fatal."""


_PY_TO_JSON = {str: "string", int: "integer", float: "number", bool: "boolean"}


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]
    fn: Callable[..., str]


REGISTRY: dict[str, ToolSpec] = {}

# ------------------------------------------------------------------
# Sandbox state (set once by the CLI / agent before any execution)
# ------------------------------------------------------------------
_WORKDIR: Path | None = None
_BASH_TIMEOUT = 20


def configure(workdir: Path, bash_timeout_seconds: int = 20) -> None:
    global _WORKDIR, _BASH_TIMEOUT
    workdir.mkdir(parents=True, exist_ok=True)
    _WORKDIR = workdir.resolve()
    _BASH_TIMEOUT = bash_timeout_seconds


def _jail(rel_path: str) -> Path:
    """Resolve a path strictly inside the workdir. Everything else dies here."""
    if _WORKDIR is None:
        raise ToolError("tools not configured: call tools.configure(workdir) first")
    if rel_path.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:", rel_path):
        raise ToolError(f"absolute paths are forbidden: {rel_path!r}")
    candidate = (_WORKDIR / rel_path).resolve()
    if not candidate.is_relative_to(_WORKDIR):
        raise ToolError(f"path escapes the workspace jail: {rel_path!r}")
    return candidate


# ------------------------------------------------------------------
# The decorator: signature -> JSON schema -> registry
# ------------------------------------------------------------------
def tool(fn: Callable[..., str]) -> Callable[..., str]:
    hints = get_type_hints(fn)
    hints.pop("return", None)
    properties: dict[str, Any] = {}
    for param in inspect.signature(fn).parameters.values():
        py_type = hints.get(param.name, str)
        if py_type not in _PY_TO_JSON:
            raise TypeError(f"@tool {fn.__name__}: unsupported param type {py_type!r}")
        properties[param.name] = {"type": _PY_TO_JSON[py_type]}

    description = (inspect.getdoc(fn) or fn.__name__).splitlines()[0]
    REGISTRY[fn.__name__] = ToolSpec(
        name=fn.__name__,
        description=description,
        parameters={
            "type": "object",
            "properties": properties,
            "required": list(properties),
        },
        fn=fn,
    )
    return fn


def openai_schemas() -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": spec.name,
                "description": spec.description,
                "parameters": spec.parameters,
            },
        }
        for spec in REGISTRY.values()
    ]


def execute(name: str, args: dict[str, Any]) -> str:
    """Run a registered tool. Returns raw output; the agent quarantines it."""
    spec = REGISTRY.get(name)
    if spec is None:
        raise ToolError(f"unknown tool: {name!r}")
    try:
        return spec.fn(**args)
    except ToolError:
        raise
    except TypeError as exc:
        raise ToolError(f"bad arguments for {name}: {exc}") from exc


# ------------------------------------------------------------------
# Phase-0 tools (the entire surface)
# ------------------------------------------------------------------
_BASH_BLOCKLIST: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\brm\s+(-[a-z]*\s+)*/(?:\s|$)",          # rm against filesystem root
        r"\bsudo\b|\bdoas\b",
        r"\b(curl|wget|nc|ncat|ssh|scp|telnet)\b", # no network: the meter is absolute
        r"\b(shutdown|reboot|halt|mkfs|fdisk)\b",
        r"\bdd\s+if=",
        r":\(\)\s*\{.*\};\s*:",                    # fork bomb
        r">\s*/dev/(sd|nvme|null\s*;)",
        r"\bchmod\s+(-[a-z]*\s+)*777\s+/",
        r"\bpython[0-9.]*\s+-c\s+.*socket",        # crude network-in-python guard
    )
)


@tool
def read_file(path: str) -> str:
    """Read a UTF-8 text file from the workspace and return its content."""
    target = _jail(path)
    if not target.is_file():
        raise ToolError(f"no such file: {path!r}")
    return target.read_text(encoding="utf-8", errors="replace")


@tool
def write_file(path: str, content: str) -> str:
    """Write UTF-8 text to a file in the workspace, creating parent dirs."""
    target = _jail(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return f"wrote {len(content)} chars to {path}"


@tool
def run_bash(command: str) -> str:
    """Run a shell command inside the workspace (20s timeout, no network)."""
    for pattern in _BASH_BLOCKLIST:
        if pattern.search(command):
            raise ToolError(f"command blocked by security policy: {command!r}")
    if _WORKDIR is None:
        raise ToolError("tools not configured")
    try:
        proc = subprocess.run(
            command,
            shell=True,
            cwd=_WORKDIR,
            capture_output=True,
            text=True,
            timeout=_BASH_TIMEOUT,
        )
    except subprocess.TimeoutExpired as exc:
        raise ToolError(f"command timed out after {_BASH_TIMEOUT}s") from exc
    output = (proc.stdout or "") + (("\n[stderr]\n" + proc.stderr) if proc.stderr else "")
    return f"[exit {proc.returncode}]\n{output.strip() or '(no output)'}"
