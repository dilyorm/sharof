from __future__ import annotations

import logging
from datetime import datetime, timezone

import httpx
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message as TgMessage

from sharof import db, gate, llm
from sharof.config import Settings

log = logging.getLogger("sharof")


def build_dispatcher(pool, client: httpx.AsyncClient, settings: Settings, bot_id: int) -> Dispatcher:
    dp = Dispatcher()

    async def _reply_with_context(message: TgMessage) -> None:
        history = await db.fetch_recent(pool, message.chat.id, settings.context_msgs)
        try:
            answer = await llm.chat(client, settings, history)
        except llm.LLMError:
            log.warning("chat LLM failed for chat %s", message.chat.id)
            await message.reply("Band edaman, birozdan keyin urinib ko'ring. / Busy, try again soon.")
            return
        await message.reply(answer)
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
            await message.reply(summary)
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
