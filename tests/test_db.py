import os
from datetime import datetime, timezone

import pytest

from sharof import db

TEST_DSN = os.environ.get("TEST_DATABASE_URL")
skip_no_db = pytest.mark.skipif(not TEST_DSN, reason="TEST_DATABASE_URL not set")


@pytest.fixture
async def pool():
    if not TEST_DSN:
        pytest.skip("TEST_DATABASE_URL not set")
    p = await db.connect(TEST_DSN)
    await db.init_schema(p)
    async with p.acquire() as con:
        await con.execute("TRUNCATE messages, chat_state")
    yield p
    await p.close()


@skip_no_db
@pytest.mark.asyncio
async def test_store_and_fetch_recent_oldest_first(pool):
    await db.store_message(pool, 1, "group", 10, "alice", "first", False)
    await db.store_message(pool, 1, "group", 11, "bob", "second", False)
    await db.store_message(pool, 1, "group", None, "sharof", "reply", True)
    msgs = await db.fetch_recent(pool, 1, limit=10)
    assert [m.text for m in msgs] == ["first", "second", "reply"]
    assert msgs[2].is_bot is True
    assert msgs[0].username == "alice"


@skip_no_db
@pytest.mark.asyncio
async def test_fetch_recent_limit_keeps_latest(pool):
    for i in range(5):
        await db.store_message(pool, 2, "group", 1, "u", f"m{i}", False)
    msgs = await db.fetch_recent(pool, 2, limit=2)
    assert [m.text for m in msgs] == ["m3", "m4"]


@skip_no_db
@pytest.mark.asyncio
async def test_interject_roundtrip(pool):
    assert await db.get_last_interject(pool, 3) is None
    ts = datetime(2026, 1, 1, tzinfo=timezone.utc)
    await db.set_last_interject(pool, 3, ts)
    got = await db.get_last_interject(pool, 3)
    assert got == ts
    ts2 = datetime(2026, 2, 2, tzinfo=timezone.utc)
    await db.set_last_interject(pool, 3, ts2)
    assert await db.get_last_interject(pool, 3) == ts2
