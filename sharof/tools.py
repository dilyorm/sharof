"""Tools the owner's agent can call: shell/files on this server, and the owner's
home PC through miki (reached over a reverse SSH tunnel on PC_URL)."""
from __future__ import annotations

import asyncio
import re
from pathlib import Path

import httpx

from sharof.config import Settings

MAX_OUT = 4000
_DEFAULT_TIMEOUT_MS = 15000

# A guard, not a sandbox. Obviously catastrophic commands, POSIX + PowerShell.
DENY = [
    re.compile(p, re.I)
    for p in (
        r"rm\s+-rf\s+/(\s|$)",
        r"\bmkfs\b",
        r":\(\)\s*\{.*\};\s*:",
        r"\bshutdown\b",
        r"\breboot\b",
        r"\bdd\s+if=",
        r">\s*/dev/sd",
        r"\bformat-volume\b",
        r"\bstop-computer\b",
        r"\bremove-item\b\s+[\"']?[a-z]:\\?[\"']?(\s|$)",
    )
]


def denied(command: str) -> bool:
    return any(p.search(command) for p in DENY)


def truncate(s: str) -> str:
    s = str(s or "")
    return s if len(s) <= MAX_OUT else s[:MAX_OUT] + "\n...[truncated]"


_SERVER_SHELL = {
    "type": "function",
    "function": {
        "name": "server_run",
        "description": "Run a shell command on the Linux server Sharof itself runs on.",
        "parameters": {
            "type": "object",
            "properties": {
                "command": {"type": "string"},
                "timeout_ms": {"type": "number"},
            },
            "required": ["command"],
        },
    },
}

_PC_SHELL = {
    "type": "function",
    "function": {
        "name": "pc_run",
        "description": (
            "Run a PowerShell command on the owner's Windows PC at home. Use for "
            "anything the PC intents can't express: files, processes, screenshots, status."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "command": {"type": "string"},
                "timeout_ms": {"type": "number"},
            },
            "required": ["command"],
        },
    },
}

_BASE_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "pc_intent",
            "description": (
                "Tell the owner's Windows PC to do a simple desktop action, in plain "
                "English: 'open youtube', 'play lofi beats', 'search quantum computing', "
                "'open telegram', 'open github.com'."
            ),
            "parameters": {
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a UTF-8 text file on the server.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Write text to a file on the server (overwrites).",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
                "required": ["path", "content"],
            },
        },
    },
]


def schemas(settings: Settings) -> list[dict]:
    if settings.allow_shell:
        return [_PC_SHELL, _SERVER_SHELL, *_BASE_TOOLS]
    return list(_BASE_TOOLS)


async def _server_run(command: str, timeout_ms: float) -> str:
    if denied(command):
        return "refused: destructive command"
    proc = await asyncio.create_subprocess_shell(
        command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout_ms / 1000)
    except asyncio.TimeoutError:
        proc.kill()
        return f"timed out after {timeout_ms / 1000:.0f}s"
    return truncate(
        f"exit {proc.returncode}\n"
        f"{out.decode(errors='replace')}{err.decode(errors='replace')}".strip()
    )


async def _pc_post(
    client: httpx.AsyncClient, settings: Settings, path: str, payload: dict, timeout_s: float
) -> dict | str:
    """Returns the JSON body, or an error string to hand back to the model."""
    try:
        resp = await client.post(
            f"{settings.pc_url}{path}",
            json=payload,
            headers={"X-Miki-Secret": settings.pc_secret},
            timeout=timeout_s,
        )
    except httpx.ConnectError:
        return "PC offline (tunnel down)"
    except httpx.TimeoutException:
        return f"PC did not answer within {timeout_s:.0f}s"
    except httpx.HTTPError as exc:
        return f"PC unreachable: {exc}"
    if resp.status_code == 401:
        return "PC rejected the request (secret mismatch)"
    if resp.status_code >= 400:
        return truncate(f"PC error {resp.status_code}: {resp.text}")
    try:
        return resp.json()
    except ValueError:
        return truncate(f"PC returned non-JSON: {resp.text}")


async def _pc_run(
    client: httpx.AsyncClient, settings: Settings, command: str, timeout_ms: float
) -> str:
    if denied(command):
        return "refused: destructive command"
    # Give the HTTP call slack over the PC-side command timeout.
    body = await _pc_post(
        client, settings, "/run",
        {"command": command, "timeout_ms": timeout_ms},
        timeout_ms / 1000 + 10,
    )
    if isinstance(body, str):
        return body
    return truncate(
        f"exit {body.get('code')}\n{body.get('stdout', '')}{body.get('stderr', '')}".strip()
    )


async def _pc_intent(client: httpx.AsyncClient, settings: Settings, text: str) -> str:
    body = await _pc_post(client, settings, "/intent", {"text": text}, 20)
    if isinstance(body, str):
        return body
    return truncate(body.get("reply") or "done")


async def execute(
    name: str, args: dict, client: httpx.AsyncClient, settings: Settings
) -> str:
    """Run one tool call. Never raises — the model sees failures as text."""
    try:
        timeout_ms = float(args.get("timeout_ms") or _DEFAULT_TIMEOUT_MS)
        if name == "pc_intent":
            return await _pc_intent(client, settings, str(args.get("text", "")))
        if name == "pc_run":
            if not settings.allow_shell:
                return "refused: shell is disabled"
            return await _pc_run(client, settings, str(args.get("command", "")), timeout_ms)
        if name == "server_run":
            if not settings.allow_shell:
                return "refused: shell is disabled"
            return await _server_run(str(args.get("command", "")), timeout_ms)
        if name == "read_file":
            return truncate(Path(args["path"]).read_text(encoding="utf-8", errors="replace"))
        if name == "write_file":
            path = Path(args["path"])
            path.write_text(str(args.get("content", "")), encoding="utf-8")
            return f"wrote {path}"
        return f"unknown tool: {name}"
    except Exception as exc:  # noqa: BLE001 - tool errors are data, not crashes
        return truncate(f"error: {type(exc).__name__}: {exc}")
