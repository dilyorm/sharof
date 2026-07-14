"""Tools the owner's agent can call: shell/files on this server, and the owner's
home PC through miki (reached over a reverse SSH tunnel on PC_URL)."""
from __future__ import annotations

import asyncio
import base64
import logging
import re
from pathlib import Path
from typing import Protocol

import httpx

from sharof.config import Settings

log = logging.getLogger("sharof")

MAX_OUT = 4000
_DEFAULT_TIMEOUT_MS = 15000
# A Claude run can take a long time; poll rather than hold an HTTP call open.
_JOB_POLL_SEC = 5
_JOB_MAX_WAIT_SEC = 3600


class Notifier(Protocol):
    """How a tool sends something to the chat on its own, outside the LLM reply."""

    async def text(self, body: str) -> None: ...
    async def photo(self, png: bytes, caption: str) -> None: ...

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
            "name": "claude_code",
            "description": (
                "Launch Claude Code on the owner's PC to work in a project folder: read, "
                "explain, or change code. Runs in the background — it may take minutes — and "
                "the result is sent to the chat when it finishes. To continue an earlier "
                "thread, pass the session_id from that run's result."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "project": {
                        "type": "string",
                        "description": r"Absolute folder on the PC, e.g. C:\Users\dm200\documents\kaggle\rogii",
                    },
                    "prompt": {"type": "string", "description": "What Claude should do."},
                    "session_id": {
                        "type": "string",
                        "description": "Optional: resume a previous Claude session.",
                    },
                },
                "required": ["project", "prompt"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "pc_screenshot",
            "description": "Capture the PC's screen and send it to the chat as a photo.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "pc_keys",
            "description": (
                "Type keystrokes into whatever window is focused on the PC (Windows SendKeys "
                "syntax: {ENTER}, {TAB}, ^c for Ctrl+C). Take a screenshot first if unsure "
                "what has focus."
            ),
            "parameters": {
                "type": "object",
                "properties": {"keys": {"type": "string"}},
                "required": ["keys"],
            },
        },
    },
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


async def _pc_get(
    client: httpx.AsyncClient, settings: Settings, path: str, timeout_s: float
) -> dict | str:
    try:
        resp = await client.get(
            f"{settings.pc_url}{path}",
            headers={"X-Miki-Secret": settings.pc_secret},
            timeout=timeout_s,
        )
    except httpx.ConnectError:
        return "PC offline (tunnel down)"
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


async def _watch_claude_job(
    client: httpx.AsyncClient, settings: Settings, job_id: str, notify: Notifier
) -> None:
    """Poll a Claude run to completion, then message the chat. Runs detached."""
    waited = 0
    while waited < _JOB_MAX_WAIT_SEC:
        await asyncio.sleep(_JOB_POLL_SEC)
        waited += _JOB_POLL_SEC
        body = await _pc_get(client, settings, f"/job?id={job_id}", 20)
        if isinstance(body, str):  # PC went away mid-run
            await notify.text(f"Claude job {job_id}: lost the PC — {body}")
            return
        if not body.get("done"):
            continue
        head = "Claude finished" if body.get("ok") else "Claude failed"
        session = body.get("session_id")
        tail = f"\n\nsession_id: {session}" if session else ""
        await notify.text(f"{head} in {body.get('project')}:\n\n{body.get('output')}{tail}")
        return
    await notify.text(f"Claude job {job_id} is still running after an hour — giving up on waiting.")


async def _claude_code(
    client: httpx.AsyncClient, settings: Settings, args: dict, notify: Notifier | None
) -> str:
    project = str(args.get("project", ""))
    payload = {
        "project": project,
        "prompt": str(args.get("prompt", "")),
        "session_id": args.get("session_id"),
    }
    body = await _pc_post(client, settings, "/claude", payload, 30)
    if isinstance(body, str):
        return body
    job_id = body.get("job_id")
    if notify is None:  # no chat to report back to; nothing would collect the result
        return f"started job {job_id}, but no way to report the result"
    asyncio.create_task(_watch_claude_job(client, settings, job_id, notify))
    return (
        f"Claude is running in {project} (job {job_id}). Tell the owner it started and that "
        f"the result will arrive as a separate message. Do not wait for it."
    )


async def _pc_screenshot(
    client: httpx.AsyncClient, settings: Settings, notify: Notifier | None
) -> str:
    body = await _pc_get(client, settings, "/screenshot", 45)
    if isinstance(body, str):
        return body
    if notify is None:
        return "captured, but there is no chat to send the image to"
    try:
        png = base64.b64decode(body.get("png_b64", ""))
    except ValueError:
        return "PC sent a broken screenshot"
    await notify.photo(png, "screen")
    return "screenshot sent to the chat"


async def execute(
    name: str,
    args: dict,
    client: httpx.AsyncClient,
    settings: Settings,
    notify: Notifier | None = None,
) -> str:
    """Run one tool call. Never raises — the model sees failures as text."""
    try:
        timeout_ms = float(args.get("timeout_ms") or _DEFAULT_TIMEOUT_MS)
        if name == "pc_intent":
            return await _pc_intent(client, settings, str(args.get("text", "")))
        if name == "claude_code":
            return await _claude_code(client, settings, args, notify)
        if name == "pc_screenshot":
            return await _pc_screenshot(client, settings, notify)
        if name == "pc_keys":
            body = await _pc_post(
                client, settings, "/keys", {"keys": str(args.get("keys", ""))}, 20
            )
            return body if isinstance(body, str) else "keys sent"
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
