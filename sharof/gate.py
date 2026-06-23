from __future__ import annotations

from datetime import datetime, timedelta

import httpx

from sharof import db, llm
from sharof.config import Settings
from sharof.db import Message


def heuristic_hit(text: str, settings: Settings) -> bool:
    low = text.lower()
    if text.rstrip().endswith("?"):
        return True
    if settings.bot_username:
        name = settings.bot_username.lower().lstrip("@")
        short = name[:-4] if name.endswith("_bot") else name
        if name in low or (short and short in low):
            return True
    for w in settings.trigger_words:
        if w.lower() in low:
            return True
    return False


def cooldown_elapsed(last: datetime | None, now: datetime, cooldown_sec: int) -> bool:
    if last is None:
        return True
    return now - last >= timedelta(seconds=cooldown_sec)


async def should_interject(
    pool,
    client: httpx.AsyncClient | None,
    settings: Settings,
    chat_id: int,
    text: str,
    history: list[Message],
    now: datetime,
) -> bool:
    if not heuristic_hit(text, settings):
        return False
    last = await db.get_last_interject(pool, chat_id)
    if not cooldown_elapsed(last, now, settings.interject_cooldown_sec):
        return False
    try:
        return await llm.judge(client, settings, history)
    except llm.LLMError:
        return False
