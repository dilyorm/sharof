from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import asyncpg

_SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schema.sql"


@dataclass
class Message:
    user_id: int | None
    username: str | None
    text: str
    is_bot: bool


async def connect(dsn: str) -> asyncpg.Pool:
    return await asyncpg.create_pool(dsn, min_size=1, max_size=5)


async def init_schema(pool: asyncpg.Pool) -> None:
    sql = _SCHEMA_PATH.read_text(encoding="utf-8")
    async with pool.acquire() as con:
        await con.execute(sql)


async def store_message(
    pool: asyncpg.Pool,
    chat_id: int,
    chat_type: str,
    user_id: int | None,
    username: str | None,
    text: str,
    is_bot: bool,
) -> None:
    async with pool.acquire() as con:
        await con.execute(
            """
            INSERT INTO messages (chat_id, chat_type, user_id, username, text, is_bot)
            VALUES ($1, $2, $3, $4, $5, $6)
            """,
            chat_id, chat_type, user_id, username, text, is_bot,
        )


async def fetch_recent(pool: asyncpg.Pool, chat_id: int, limit: int) -> list[Message]:
    async with pool.acquire() as con:
        rows = await con.fetch(
            """
            SELECT user_id, username, text, is_bot FROM (
                SELECT id, user_id, username, text, is_bot
                FROM messages WHERE chat_id = $1
                ORDER BY ts DESC, id DESC LIMIT $2
            ) sub ORDER BY id ASC
            """,
            chat_id, limit,
        )
    return [
        Message(r["user_id"], r["username"], r["text"], r["is_bot"]) for r in rows
    ]


async def get_last_interject(pool: asyncpg.Pool, chat_id: int) -> datetime | None:
    async with pool.acquire() as con:
        return await con.fetchval(
            "SELECT last_interject_ts FROM chat_state WHERE chat_id = $1", chat_id
        )


async def set_last_interject(pool: asyncpg.Pool, chat_id: int, ts: datetime) -> None:
    async with pool.acquire() as con:
        await con.execute(
            """
            INSERT INTO chat_state (chat_id, last_interject_ts)
            VALUES ($1, $2)
            ON CONFLICT (chat_id) DO UPDATE SET last_interject_ts = EXCLUDED.last_interject_ts
            """,
            chat_id, ts,
        )
