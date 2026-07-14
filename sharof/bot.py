from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

import httpx
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import BufferedInputFile
from aiogram.types import Message as TgMessage

from sharof import agent, db, gate, llm
from sharof.config import Settings

log = logging.getLogger("sharof")

TG_LIMIT = 4000  # Telegram's hard cap is 4096

# Replies go out as plain text (no parse_mode), so any markdown the model emits
# shows up as literal **stars**. Strip the marks instead of trusting parse_mode,
# which rejects the whole message when the syntax is unbalanced.
_MD_MARKS = re.compile(r"\*\*|__|~~|`{1,3}")
_MD_HEADING = re.compile(r"^\s{0,3}#{1,6}\s*", re.M)
_MD_BULLET = re.compile(r"^(\s*)[*+]\s+", re.M)


def plain(text: str) -> str:
    text = _MD_MARKS.sub("", text)
    text = _MD_HEADING.sub("", text)
    return _MD_BULLET.sub(r"\1- ", text).strip()


def chunks(text: str) -> list[str]:
    """Split on line boundaries so a long reply doesn't get rejected."""
    out: list[str] = []
    for line in text.split("\n"):
        if out and len(out[-1]) + len(line) + 1 <= TG_LIMIT:
            out[-1] += "\n" + line
        else:
            while len(line) > TG_LIMIT:
                out.append(line[:TG_LIMIT])
                line = line[TG_LIMIT:]
            out.append(line)
    return [c for c in out if c.strip()] or ["(empty)"]


async def send(message: TgMessage, text: str) -> None:
    for chunk in chunks(plain(text)):
        await message.answer(chunk)


class ChatNotifier:
    """Lets a tool talk to the chat on its own — a finished Claude run, a screenshot."""

    def __init__(self, message: TgMessage, pool, settings: Settings) -> None:
        self._message = message
        self._pool = pool
        self._settings = settings

    async def text(self, body: str) -> None:
        await send(self._message, body)
        await db.store_message(
            self._pool, self._message.chat.id, self._message.chat.type, None,
            self._settings.bot_username or "sharof", body, True,
        )

    async def photo(self, png: bytes, caption: str) -> None:
        await self._message.answer_photo(
            BufferedInputFile(png, filename="screen.png"), caption=caption
        )


def build_dispatcher(pool, client: httpx.AsyncClient, settings: Settings, bot_id: int) -> Dispatcher:
    dp = Dispatcher()

    def _is_owner(message: TgMessage) -> bool:
        # Tools are remote code execution on two machines. Owner user ids only,
        # and never in a group (anyone can add the bot to one).
        return (
            message.chat.type == "private"
            and message.from_user is not None
            and message.from_user.id in settings.owner_ids
        )

    async def _reply_with_context(message: TgMessage) -> None:
        history = await db.fetch_recent(pool, message.chat.id, settings.context_msgs)
        try:
            if _is_owner(message):
                notifier = ChatNotifier(message, pool, settings)
                answer = await agent.handle(client, settings, history, notifier)
            else:
                answer = await llm.chat(client, settings, history)
        except llm.LLMError:
            log.warning("chat LLM failed for chat %s", message.chat.id)
            await message.reply("Band edaman, birozdan keyin urinib ko'ring. / Busy, try again soon.")
            return
        await send(message, answer)
        await db.store_message(
            pool, message.chat.id, message.chat.type, None,
            settings.bot_username or "sharof", answer, True,
        )

    @dp.message(Command("start"))
    async def on_start(message: TgMessage) -> None:
        try:
            await message.reply(
                "Salom! Men Sharof — AI yordamchi. DM da yozing yoki guruhda "
                "@mention qiling. /summarize bilan suhbatni qisqartiraman."
            )
        except Exception:
            log.exception("on_start failed for chat %s", message.chat.id)

    @dp.message(Command("summarize"))
    async def on_summarize(message: TgMessage) -> None:
        try:
            history = await db.fetch_recent(pool, message.chat.id, settings.context_msgs)
            if not history:
                await message.reply("Hali xabar yo'q. / No messages yet.")
                return
            try:
                summary = await llm.summarize(client, settings, history)
            except llm.LLMError:
                await message.reply("Band edaman, birozdan keyin. / Busy, try again.")
                return
            await send(message, summary)
        except Exception:
            log.exception("on_summarize failed for chat %s", message.chat.id)

    @dp.message(F.text)
    async def on_text(message: TgMessage) -> None:
        try:
            if not message.text:
                return
            chat = message.chat
            user = message.from_user
            await db.store_message(
                pool, chat.id, chat.type,
                user.id if user else None,
                user.username if user else None,
                message.text, False,
            )

            if chat.type == "private":
                await _reply_with_context(message)
                return

            # group / supergroup
            is_mention = bool(settings.bot_username) and (
                f"@{settings.bot_username.lstrip('@')}" in message.text
            )
            is_reply_to_bot = bool(
                message.reply_to_message
                and message.reply_to_message.from_user
                and message.reply_to_message.from_user.id == bot_id
            )
            if is_mention or is_reply_to_bot:
                await _reply_with_context(message)
                return

            now = datetime.now(timezone.utc)
            history = await db.fetch_recent(pool, chat.id, settings.context_msgs)
            if await gate.should_interject(pool, client, settings, chat.id, message.text, history, now):
                await db.set_last_interject(pool, chat.id, now)
                await _reply_with_context(message)
        except Exception:
            log.exception("on_text failed for chat %s", message.chat.id)

    return dp
