"""Tool-calling agent. Only ever runs for an owner in a private chat (see bot.py)."""
from __future__ import annotations

import json
import logging
import re

import httpx

from sharof import llm, tools
from sharof.config import Settings
from sharof.db import Message

log = logging.getLogger("sharof")

MAX_ITERS = 4

SYSTEM_PROMPT = (
    "You are Sharof, the owner's remote controller in Telegram. You run on a Linux "
    "server. The owner's Windows PC at home runs a helper called miki, which you reach "
    "with the pc_* tools.\n"
    "- pc_intent: simple desktop actions on the PC ('open youtube', 'play lofi', "
    "'search X', 'open telegram').\n"
    "- pc_run: anything else on the PC, as PowerShell. Listing folders, checking "
    "processes, reading files — do it here.\n"
    "- claude_code: launch Claude Code in a project folder on the PC to read or change "
    "code. It runs in the background and its result arrives as a separate message, so "
    "just confirm that it started. Pass session_id to continue an earlier run.\n"
    "- pc_look: read what is on the PC's screen and get it back in words — use it before "
    "pc_keys, or to check on a run. pc_screenshot: send the owner the picture itself.\n"
    "- pc_keys: type into the focused window on the PC.\n"
    "- server_run / read_file / write_file: this server, not the PC.\n"
    "If the message is just conversation, answer directly and call no tool.\n\n"
    "ACT, DON'T PROMISE: when the owner asks for something on the PC, call the tool in "
    "this turn. Never say you will do it in a moment — there is no later turn. Never "
    "claim the PC is offline unless a tool you called just now said so; earlier messages "
    "in this chat may be stale.\n\n"
    "FORMAT: Telegram shows your reply as plain text. Never use markdown — no **bold**, "
    "no ##headings, no backticks, no numbered/bulleted markdown lists. Write plain "
    "sentences; if you must list things, one per line with a leading '- '. Keep it "
    "short: a folder listing is a bare list of names, not a report. Reply in the user's "
    "language."
)


# Things the bot said in the past that are false once the tunnel is back up. Left in
# the history, the model imitates them: it answers "your PC is offline" and calls no
# tool at all (measured: 5 failures in 6 calls on a poisoned history).
_STALE_OFFLINE = re.compile(r"offline|tunnel down|not connected|can't reach your pc", re.I)


def _status(online: bool) -> str:
    if online:
        return (
            "PC STATUS RIGHT NOW: ONLINE. The tunnel is up and every pc_* tool works. "
            "Do not say the PC is offline. Call the tool."
        )
    return (
        "PC STATUS RIGHT NOW: OFFLINE. miki is not running on the PC, so pc_* tools will "
        "fail. Say so, and do not pretend to have run anything."
    )


def _clean_history(history: list[Message], bot_username: str, online: bool) -> list[dict]:
    msgs = llm.build_messages(history, bot_username)[1:]  # drop the chat system prompt
    if not online:
        return msgs
    return [
        m for m in msgs
        if not (m["role"] == "assistant" and _STALE_OFFLINE.search(m.get("content") or ""))
    ]


async def handle(
    client: httpx.AsyncClient,
    settings: Settings,
    history: list[Message],
    notify: tools.Notifier | None = None,
) -> str:
    """history ends with the owner's new message (bot.py stores it first)."""
    schemas = tools.schemas(settings)
    online = await tools.pc_online(client, settings)

    messages: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages += _clean_history(history, settings.bot_username, online)
    # Last thing the model reads before answering: what is actually true right now.
    # In the system prompt this lost to the weight of the chat history.
    messages.append({"role": "system", "content": _status(online)})

    for _ in range(MAX_ITERS):
        msg = await llm.tool_call(client, settings, messages, schemas)
        calls = msg.get("tool_calls") or []
        if not calls:
            return (msg.get("content") or "Bajarildi. / Done.").strip()
        messages.append(msg)
        for call in calls:
            fn = call.get("function", {})
            name = fn.get("name", "")
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except ValueError:
                args = {}
            result = await tools.execute(name, args, client, settings, notify)
            log.info("tool %s -> %s", name, result[:120].replace("\n", " "))
            messages.append({
                "role": "tool",
                "tool_call_id": call.get("id", ""),
                "content": result,
            })

    return "Juda ko'p qadam bo'ldi, tugatolmadim. / Too many steps, couldn't finish."
