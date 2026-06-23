from __future__ import annotations

import asyncio
import logging
import os

import httpx
from aiogram import Bot
from dotenv import load_dotenv

from sharof import db
from sharof.bot import build_dispatcher
from sharof.config import load_settings


async def run() -> None:
    logging.basicConfig(level=logging.INFO)
    load_dotenv()
    settings = load_settings(os.environ)

    pool = await db.connect(settings.database_url)
    await db.init_schema(pool)

    bot = Bot(token=settings.telegram_token)
    if not settings.bot_username:
        me = await bot.get_me()
        object.__setattr__(settings, "bot_username", me.username or "")
        logging.info("Resolved bot username: %s", settings.bot_username)

    async with httpx.AsyncClient() as client:
        dp = build_dispatcher(pool, client, settings)
        try:
            await dp.start_polling(bot)
        finally:
            await pool.close()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
