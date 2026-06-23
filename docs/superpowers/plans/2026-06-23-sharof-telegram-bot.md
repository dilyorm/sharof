# sharof Telegram AI Chat Bot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Telegram bot that uses OpenRouter free models (default DeepSeek V3) to chat in DMs, read groups, keep per-chat context in Postgres, summarize on demand, and auto-join group conversations through a quota-safe cheap gate.

**Architecture:** Async Python with aiogram 3 long-polling. Incoming messages are stored in Postgres, then classified (DM / group-always-reply / group-gate). A cheap gate (heuristics → cooldown → optional small LLM judge) decides group interjections. Replies pull last-K context and call OpenRouter via httpx with backoff. Components are split into focused modules: `config`, `db`, `llm`, `gate`, `bot`, `main`.

**Tech Stack:** Python 3.12, aiogram 3.x, asyncpg, httpx, Postgres, pytest, systemd.

## Global Constraints

- Python 3.12 (target server: Ubuntu 24.04 aarch64).
- aiogram 3.x (pin `aiogram>=3.13,<4`). Long-polling only — no webhook, no public HTTPS.
- All config via `.env` (python-dotenv). Never hardcode secrets.
- Default model: `deepseek/deepseek-chat-v3-0324:free` — swappable via `OPENROUTER_MODEL`.
- OpenRouter base URL: `https://openrouter.ai/api/v1/chat/completions`.
- Quota safety: no full LLM call in groups without passing always-reply OR the cheap gate. Heuristic + cooldown gate BEFORE any judge/reply LLM call.
- Reply language: auto-detect — reply in the user's language. Enforced via system prompt.
- Defaults: `CONTEXT_MSGS=20`, `INTERJECT_COOLDOWN_SEC=300`.
- TDD: write failing test first, frequent commits.
- DB schema lives in `schema.sql`; tests use a separate test database or transactional rollback (see Task 2).

---

## File Structure

- `requirements.txt` — pinned deps.
- `.env.example` — config template.
- `schema.sql` — Postgres schema.
- `sharof/__init__.py` — package marker.
- `sharof/config.py` — load + validate settings from env.
- `sharof/db.py` — asyncpg pool, schema init, message store/fetch, cooldown I/O.
- `sharof/llm.py` — OpenRouter calls (chat, summarize, judge) with backoff.
- `sharof/gate.py` — cheap-gate decision logic (pure where possible).
- `sharof/bot.py` — aiogram handlers wiring db/llm/gate.
- `sharof/main.py` — entrypoint: build pool, bot, start polling.
- `tests/test_config.py`, `tests/test_gate.py`, `tests/test_llm.py`, `tests/test_db.py` — unit tests.
- `deploy/sharof.service` — systemd unit.
- `deploy/README.md` — server setup + deploy steps.

---

### Task 1: Project scaffold + config module

**Files:**
- Create: `requirements.txt`
- Create: `.env.example`
- Create: `sharof/__init__.py`
- Create: `sharof/config.py`
- Create: `tests/__init__.py`
- Create: `tests/test_config.py`

**Interfaces:**
- Produces: `Settings` dataclass with fields `telegram_token: str`, `openrouter_api_key: str`, `openrouter_model: str`, `database_url: str`, `context_msgs: int`, `interject_cooldown_sec: int`, `trigger_words: list[str]`, `bot_username: str`. Function `load_settings(env: Mapping[str, str]) -> Settings`.

- [ ] **Step 1: Write `requirements.txt`**

```
aiogram>=3.13,<4
asyncpg>=0.29,<0.31
httpx>=0.27,<1
python-dotenv>=1.0,<2
pytest>=8,<9
pytest-asyncio>=0.23,<1
```

- [ ] **Step 2: Write `.env.example`**

```
TELEGRAM_TOKEN=
OPENROUTER_API_KEY=
OPENROUTER_MODEL=deepseek/deepseek-chat-v3-0324:free
DATABASE_URL=postgresql://sharof:CHANGEME@localhost:5432/sharof
CONTEXT_MSGS=20
INTERJECT_COOLDOWN_SEC=300
TRIGGER_WORDS=
BOT_USERNAME=
```

- [ ] **Step 3: Create `sharof/__init__.py` and `tests/__init__.py`** (empty files)

- [ ] **Step 4: Write the failing test** in `tests/test_config.py`

```python
import pytest
from sharof.config import load_settings, Settings


def test_load_settings_parses_all_fields():
    env = {
        "TELEGRAM_TOKEN": "tok",
        "OPENROUTER_API_KEY": "key",
        "OPENROUTER_MODEL": "m/model:free",
        "DATABASE_URL": "postgresql://u:p@localhost/db",
        "CONTEXT_MSGS": "15",
        "INTERJECT_COOLDOWN_SEC": "120",
        "TRIGGER_WORDS": "sharof, bot, help",
        "BOT_USERNAME": "sharof_bot",
    }
    s = load_settings(env)
    assert isinstance(s, Settings)
    assert s.telegram_token == "tok"
    assert s.openrouter_model == "m/model:free"
    assert s.context_msgs == 15
    assert s.interject_cooldown_sec == 120
    assert s.trigger_words == ["sharof", "bot", "help"]
    assert s.bot_username == "sharof_bot"


def test_load_settings_uses_defaults():
    env = {
        "TELEGRAM_TOKEN": "tok",
        "OPENROUTER_API_KEY": "key",
        "DATABASE_URL": "postgresql://u:p@localhost/db",
    }
    s = load_settings(env)
    assert s.openrouter_model == "deepseek/deepseek-chat-v3-0324:free"
    assert s.context_msgs == 20
    assert s.interject_cooldown_sec == 300
    assert s.trigger_words == []
    assert s.bot_username == ""


def test_load_settings_missing_required_raises():
    with pytest.raises(ValueError):
        load_settings({"OPENROUTER_API_KEY": "key", "DATABASE_URL": "x"})
```

- [ ] **Step 5: Run test to verify it fails**

Run: `python -m pytest tests/test_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'sharof.config'`

- [ ] **Step 6: Write `sharof/config.py`**

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

DEFAULT_MODEL = "deepseek/deepseek-chat-v3-0324:free"


@dataclass(frozen=True)
class Settings:
    telegram_token: str
    openrouter_api_key: str
    openrouter_model: str
    database_url: str
    context_msgs: int
    interject_cooldown_sec: int
    trigger_words: list[str]
    bot_username: str


def _require(env: Mapping[str, str], key: str) -> str:
    val = env.get(key)
    if not val:
        raise ValueError(f"Missing required env var: {key}")
    return val


def load_settings(env: Mapping[str, str]) -> Settings:
    words_raw = env.get("TRIGGER_WORDS", "") or ""
    trigger_words = [w.strip() for w in words_raw.split(",") if w.strip()]
    return Settings(
        telegram_token=_require(env, "TELEGRAM_TOKEN"),
        openrouter_api_key=_require(env, "OPENROUTER_API_KEY"),
        openrouter_model=env.get("OPENROUTER_MODEL") or DEFAULT_MODEL,
        database_url=_require(env, "DATABASE_URL"),
        context_msgs=int(env.get("CONTEXT_MSGS") or 20),
        interject_cooldown_sec=int(env.get("INTERJECT_COOLDOWN_SEC") or 300),
        trigger_words=trigger_words,
        bot_username=env.get("BOT_USERNAME", "") or "",
    )
```

- [ ] **Step 7: Run test to verify it passes**

Run: `python -m pytest tests/test_config.py -v`
Expected: PASS (3 tests)

- [ ] **Step 8: Commit**

```bash
git add requirements.txt .env.example sharof/__init__.py sharof/config.py tests/__init__.py tests/test_config.py
git commit -m "feat(config): settings loader + project scaffold"
```

---

### Task 2: Database schema + db module

**Files:**
- Create: `schema.sql`
- Create: `sharof/db.py`
- Create: `tests/test_db.py`

**Interfaces:**
- Consumes: nothing from prior tasks (takes a DSN string).
- Produces:
  - `class Message` (dataclass): `user_id: int | None`, `username: str | None`, `text: str`, `is_bot: bool`.
  - `async def connect(dsn: str) -> asyncpg.Pool`
  - `async def init_schema(pool) -> None` — runs `schema.sql` (idempotent).
  - `async def store_message(pool, chat_id: int, chat_type: str, user_id: int | None, username: str | None, text: str, is_bot: bool) -> None`
  - `async def fetch_recent(pool, chat_id: int, limit: int) -> list[Message]` — oldest-first.
  - `async def get_last_interject(pool, chat_id: int) -> datetime | None`
  - `async def set_last_interject(pool, chat_id: int, ts: datetime) -> None`

**Note on tests:** Task 2 db tests require a live Postgres. They are marked `@pytest.mark.asyncio` and skip if `TEST_DATABASE_URL` env var is unset. This keeps the suite green on machines without Postgres while still being runnable on the server.

- [ ] **Step 1: Write `schema.sql`**

```sql
CREATE TABLE IF NOT EXISTS messages (
    id         BIGSERIAL PRIMARY KEY,
    chat_id    BIGINT      NOT NULL,
    chat_type  TEXT        NOT NULL,
    user_id    BIGINT,
    username   TEXT,
    text       TEXT        NOT NULL,
    is_bot     BOOLEAN     NOT NULL DEFAULT FALSE,
    ts         TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_messages_chat_ts ON messages (chat_id, ts DESC);

CREATE TABLE IF NOT EXISTS chat_state (
    chat_id           BIGINT PRIMARY KEY,
    last_interject_ts TIMESTAMPTZ
);
```

- [ ] **Step 2: Write the failing test** in `tests/test_db.py`

```python
import os
from datetime import datetime, timezone

import pytest

from sharof import db

pytestmark = pytest.mark.asyncio

TEST_DSN = os.environ.get("TEST_DATABASE_URL")
skip_no_db = pytest.mark.skipif(not TEST_DSN, reason="TEST_DATABASE_URL not set")


@pytest.fixture
async def pool():
    p = await db.connect(TEST_DSN)
    await db.init_schema(p)
    async with p.acquire() as con:
        await con.execute("TRUNCATE messages, chat_state")
    yield p
    await p.close()


@skip_no_db
async def test_store_and_fetch_recent_oldest_first(pool):
    await db.store_message(pool, 1, "group", 10, "alice", "first", False)
    await db.store_message(pool, 1, "group", 11, "bob", "second", False)
    await db.store_message(pool, 1, "group", None, "sharof", "reply", True)
    msgs = await db.fetch_recent(pool, 1, limit=10)
    assert [m.text for m in msgs] == ["first", "second", "reply"]
    assert msgs[2].is_bot is True
    assert msgs[0].username == "alice"


@skip_no_db
async def test_fetch_recent_limit_keeps_latest(pool):
    for i in range(5):
        await db.store_message(pool, 2, "group", 1, "u", f"m{i}", False)
    msgs = await db.fetch_recent(pool, 2, limit=2)
    assert [m.text for m in msgs] == ["m3", "m4"]


@skip_no_db
async def test_interject_roundtrip(pool):
    assert await db.get_last_interject(pool, 3) is None
    ts = datetime(2026, 1, 1, tzinfo=timezone.utc)
    await db.set_last_interject(pool, 3, ts)
    got = await db.get_last_interject(pool, 3)
    assert got == ts
    ts2 = datetime(2026, 2, 2, tzinfo=timezone.utc)
    await db.set_last_interject(pool, 3, ts2)
    assert await db.get_last_interject(pool, 3) == ts2
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python -m pytest tests/test_db.py -v`
Expected: FAIL — `ModuleNotFoundError` / `AttributeError` on `db.connect` (or SKIP if no `TEST_DATABASE_URL`; set it to a real DSN to actually run).

- [ ] **Step 4: Write `sharof/db.py`**

```python
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
```

- [ ] **Step 5: Run test to verify it passes**

Run: `TEST_DATABASE_URL=postgresql://sharof:pw@localhost/sharof_test python -m pytest tests/test_db.py -v`
Expected: PASS (3 tests). Without `TEST_DATABASE_URL`: 3 SKIPPED.

- [ ] **Step 6: Commit**

```bash
git add schema.sql sharof/db.py tests/test_db.py
git commit -m "feat(db): postgres schema + message/cooldown store"
```

---

### Task 3: LLM module (OpenRouter chat/summarize/judge with backoff)

**Files:**
- Create: `sharof/llm.py`
- Create: `tests/test_llm.py`

**Interfaces:**
- Consumes: `Settings` (for api key + model), `Message` list from `db`.
- Produces:
  - `SYSTEM_PROMPT: str` (instructs auto-detect language, concise helpful chat as "Sharof").
  - `def build_messages(history: list[Message], bot_username: str) -> list[dict]` — converts db Messages to OpenRouter chat format (bot msgs → assistant, others → user with `username: text` prefix), prepended with system prompt.
  - `async def chat(client: httpx.AsyncClient, settings, history: list[Message]) -> str`
  - `async def summarize(client: httpx.AsyncClient, settings, history: list[Message]) -> str`
  - `async def judge(client: httpx.AsyncClient, settings, history: list[Message]) -> bool` — returns True if the bot should interject.
  - `async def _post(client, settings, messages: list[dict], max_tokens: int) -> str` — shared call with retry/backoff on 429/5xx (3 attempts, exponential). Uses `asyncio.sleep`.

- [ ] **Step 1: Write the failing test** in `tests/test_llm.py`

```python
import httpx
import pytest

from sharof import llm
from sharof.config import Settings
from sharof.db import Message

pytestmark = pytest.mark.asyncio


def _settings():
    return Settings(
        telegram_token="t", openrouter_api_key="k",
        openrouter_model="m:free", database_url="d",
        context_msgs=20, interject_cooldown_sec=300,
        trigger_words=[], bot_username="sharof_bot",
    )


def test_build_messages_roles_and_system():
    hist = [
        Message(1, "alice", "hello", False),
        Message(None, "sharof", "hi there", True),
    ]
    out = llm.build_messages(hist, "sharof_bot")
    assert out[0]["role"] == "system"
    assert out[1]["role"] == "user"
    assert "alice" in out[1]["content"]
    assert out[2]["role"] == "assistant"
    assert out[2]["content"] == "hi there"


async def test_chat_returns_content_and_sends_auth(monkeypatch):
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json={
            "choices": [{"message": {"content": "answer"}}]
        })

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        result = await llm.chat(client, _settings(), [Message(1, "u", "q", False)])
    assert result == "answer"
    assert captured["auth"] == "Bearer k"


async def test_judge_parses_yes_no():
    def handler(request):
        return httpx.Response(200, json={
            "choices": [{"message": {"content": "YES"}}]
        })
    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        assert await llm.judge(client, _settings(), [Message(1, "u", "q?", False)]) is True


async def test_post_retries_on_429_then_succeeds(monkeypatch):
    calls = {"n": 0}

    async def fake_sleep(_):
        return None
    monkeypatch.setattr(llm.asyncio, "sleep", fake_sleep)

    def handler(request):
        calls["n"] += 1
        if calls["n"] < 2:
            return httpx.Response(429, json={"error": "rate"})
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        result = await llm._post(client, _settings(), [{"role": "user", "content": "x"}], 100)
    assert result == "ok"
    assert calls["n"] == 2


async def test_post_raises_after_exhausted(monkeypatch):
    async def fake_sleep(_):
        return None
    monkeypatch.setattr(llm.asyncio, "sleep", fake_sleep)

    def handler(request):
        return httpx.Response(503, json={"error": "down"})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(llm.LLMError):
            await llm._post(client, _settings(), [{"role": "user", "content": "x"}], 100)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_llm.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'sharof.llm'`

- [ ] **Step 3: Write `sharof/llm.py`**

```python
from __future__ import annotations

import asyncio

import httpx

from sharof.config import Settings
from sharof.db import Message

API_URL = "https://openrouter.ai/api/v1/chat/completions"

SYSTEM_PROMPT = (
    "You are Sharof, a helpful, friendly AI assistant in Telegram. "
    "Always reply in the same language the user wrote in (Uzbek, Russian, "
    "English, or any other). Be concise and natural, like a person in a chat. "
    "You can see recent chat history for context."
)

JUDGE_PROMPT = (
    "You are a gate deciding whether Sharof should jump into this group chat. "
    "Reply with exactly 'YES' if a helpful reply is clearly wanted or useful "
    "right now, otherwise reply 'NO'. Only YES or NO."
)

_MAX_ATTEMPTS = 3
_BASE_DELAY = 1.0


class LLMError(Exception):
    pass


def build_messages(history: list[Message], bot_username: str) -> list[dict]:
    msgs: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]
    for m in history:
        if m.is_bot:
            msgs.append({"role": "assistant", "content": m.text})
        else:
            name = m.username or "user"
            msgs.append({"role": "user", "content": f"{name}: {m.text}"})
    return msgs


async def _post(
    client: httpx.AsyncClient, settings: Settings, messages: list[dict], max_tokens: int
) -> str:
    headers = {
        "Authorization": f"Bearer {settings.openrouter_api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": settings.openrouter_model,
        "messages": messages,
        "max_tokens": max_tokens,
    }
    last_exc: Exception | None = None
    for attempt in range(_MAX_ATTEMPTS):
        try:
            resp = await client.post(API_URL, json=payload, headers=headers, timeout=60)
            if resp.status_code == 429 or resp.status_code >= 500:
                last_exc = LLMError(f"status {resp.status_code}")
                await asyncio.sleep(_BASE_DELAY * (2 ** attempt))
                continue
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"].strip()
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            last_exc = exc
            await asyncio.sleep(_BASE_DELAY * (2 ** attempt))
    raise LLMError(f"OpenRouter call failed after {_MAX_ATTEMPTS} attempts: {last_exc}")


async def chat(client: httpx.AsyncClient, settings: Settings, history: list[Message]) -> str:
    return await _post(client, settings, build_messages(history, settings.bot_username), 800)


async def summarize(client: httpx.AsyncClient, settings: Settings, history: list[Message]) -> str:
    convo = build_messages(history, settings.bot_username)
    convo.append({"role": "user", "content": "Summarize the conversation above briefly, in the chat's main language."})
    return await _post(client, settings, convo, 500)


async def judge(client: httpx.AsyncClient, settings: Settings, history: list[Message]) -> bool:
    msgs = [{"role": "system", "content": JUDGE_PROMPT}]
    msgs += build_messages(history, settings.bot_username)[1:]  # drop chat system prompt
    answer = await _post(client, settings, msgs, 5)
    return answer.strip().upper().startswith("YES")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_llm.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add sharof/llm.py tests/test_llm.py
git commit -m "feat(llm): openrouter chat/summarize/judge with backoff"
```

---

### Task 4: Cheap gate decision logic

**Files:**
- Create: `sharof/gate.py`
- Create: `tests/test_gate.py`

**Interfaces:**
- Consumes: `Settings` (trigger_words, bot_username, interject_cooldown_sec), `Message` history, `db` cooldown functions, `llm.judge`.
- Produces:
  - `def heuristic_hit(text: str, settings: Settings) -> bool` — pure. True if text ends with `?`, contains bot name (case-insensitive, without requiring `@`), or contains a trigger word.
  - `def cooldown_elapsed(last: datetime | None, now: datetime, cooldown_sec: int) -> bool` — pure.
  - `async def should_interject(pool, client, settings, chat_id: int, text: str, history: list[Message], now: datetime) -> bool` — orchestrates: heuristic → cooldown → judge. Returns True only if all pass. Does NOT update cooldown (caller does after a successful reply).

- [ ] **Step 1: Write the failing test** in `tests/test_gate.py`

```python
from datetime import datetime, timedelta, timezone

import pytest

from sharof import gate
from sharof.config import Settings
from sharof.db import Message

pytestmark = pytest.mark.asyncio


def _settings(**kw):
    base = dict(
        telegram_token="t", openrouter_api_key="k", openrouter_model="m",
        database_url="d", context_msgs=20, interject_cooldown_sec=300,
        trigger_words=["help"], bot_username="sharof_bot",
    )
    base.update(kw)
    return Settings(**base)


def test_heuristic_question_mark():
    assert gate.heuristic_hit("what is this?", _settings()) is True


def test_heuristic_bot_name():
    assert gate.heuristic_hit("hey sharof come here", _settings(bot_username="sharof_bot")) is True


def test_heuristic_trigger_word():
    assert gate.heuristic_hit("I need help now", _settings()) is True


def test_heuristic_miss():
    assert gate.heuristic_hit("just chatting away", _settings(trigger_words=[])) is False


def test_cooldown_none_elapsed():
    assert gate.cooldown_elapsed(None, datetime.now(timezone.utc), 300) is True


def test_cooldown_not_elapsed():
    now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    last = now - timedelta(seconds=100)
    assert gate.cooldown_elapsed(last, now, 300) is False


def test_cooldown_elapsed():
    now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    last = now - timedelta(seconds=400)
    assert gate.cooldown_elapsed(last, now, 300) is True


class FakePool:
    def __init__(self, last):
        self._last = last


async def test_should_interject_stops_on_heuristic_miss(monkeypatch):
    judge_called = {"n": 0}

    async def fake_judge(*a, **k):
        judge_called["n"] += 1
        return True
    monkeypatch.setattr(gate.llm, "judge", fake_judge)

    async def fake_get_last(pool, chat_id):
        return None
    monkeypatch.setattr(gate.db, "get_last_interject", fake_get_last)

    now = datetime.now(timezone.utc)
    result = await gate.should_interject(
        FakePool(None), None, _settings(trigger_words=[]),
        1, "no signal here", [], now,
    )
    assert result is False
    assert judge_called["n"] == 0


async def test_should_interject_stops_on_cooldown(monkeypatch):
    judge_called = {"n": 0}

    async def fake_judge(*a, **k):
        judge_called["n"] += 1
        return True
    monkeypatch.setattr(gate.llm, "judge", fake_judge)

    now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)

    async def fake_get_last(pool, chat_id):
        return now - timedelta(seconds=10)
    monkeypatch.setattr(gate.db, "get_last_interject", fake_get_last)

    result = await gate.should_interject(
        FakePool(None), None, _settings(), 1, "help?", [], now,
    )
    assert result is False
    assert judge_called["n"] == 0


async def test_should_interject_all_pass(monkeypatch):
    async def fake_judge(*a, **k):
        return True
    monkeypatch.setattr(gate.llm, "judge", fake_judge)

    async def fake_get_last(pool, chat_id):
        return None
    monkeypatch.setattr(gate.db, "get_last_interject", fake_get_last)

    now = datetime.now(timezone.utc)
    result = await gate.should_interject(
        FakePool(None), None, _settings(), 1, "help?", [], now,
    )
    assert result is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_gate.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'sharof.gate'`

- [ ] **Step 3: Write `sharof/gate.py`**

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_gate.py -v`
Expected: PASS (10 tests)

- [ ] **Step 5: Commit**

```bash
git add sharof/gate.py tests/test_gate.py
git commit -m "feat(gate): cheap-gate interject decision"
```

---

### Task 5: Bot handlers + entrypoint

**Files:**
- Create: `sharof/bot.py`
- Create: `sharof/main.py`

**Interfaces:**
- Consumes: everything above — `Settings`, `db`, `llm`, `gate`.
- Produces:
  - `def build_dispatcher(pool, client, settings) -> aiogram.Dispatcher` — registers handlers; injects pool/client/settings via closures.
  - `async def run(settings: Settings) -> None` in `main.py` — builds pool, schema, bot, dispatcher, starts polling.

**Note:** This task is wiring; aiogram polling and live Telegram/OpenRouter make full automated tests impractical. Verification is a manual smoke test (Step 4) plus a syntax/import check. Pure logic is already covered by Tasks 1–4.

- [ ] **Step 1: Write `sharof/bot.py`**

```python
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


def build_dispatcher(pool, client: httpx.AsyncClient, settings: Settings) -> Dispatcher:
    dp = Dispatcher()

    async def _reply_with_context(message: TgMessage) -> None:
        history = await db.fetch_recent(pool, message.chat.id, settings.context_msgs)
        try:
            answer = await llm.chat(client, settings, history)
        except llm.LLMError:
            log.warning("chat LLM failed for chat %s", message.chat.id)
            await message.reply("Band edaman, birozdan keyin urinib ko'ring. / Busy, try again soon.")
            return
        sent = await message.reply(answer)
        await db.store_message(
            pool, message.chat.id, message.chat.type, None,
            settings.bot_username or "sharof", answer, True,
        )
        _ = sent

    @dp.message(Command("start"))
    async def on_start(message: TgMessage) -> None:
        await message.reply(
            "Salom! Men Sharof — AI yordamchi. DM da yozing yoki guruhda "
            "@mention qiling. /summarize bilan suhbatni qisqartiraman."
        )

    @dp.message(Command("summarize"))
    async def on_summarize(message: TgMessage) -> None:
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

    @dp.message(F.text)
    async def on_text(message: TgMessage) -> None:
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
            and message.reply_to_message.from_user.is_bot
        )
        if is_mention or is_reply_to_bot:
            await _reply_with_context(message)
            return

        now = datetime.now(timezone.utc)
        history = await db.fetch_recent(pool, chat.id, settings.context_msgs)
        if await gate.should_interject(pool, client, settings, chat.id, message.text, history, now):
            await db.set_last_interject(pool, chat.id, now)
            await _reply_with_context(message)

    return dp
```

- [ ] **Step 2: Write `sharof/main.py`**

```python
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
```

- [ ] **Step 3: Import/syntax check**

Run: `python -c "import sharof.bot, sharof.main; print('ok')"`
Expected: prints `ok` (needs deps installed: `pip install -r requirements.txt`).

- [ ] **Step 4: Manual smoke test**

With a real `.env` (test bot token, OpenRouter key, local Postgres):
Run: `python -m sharof.main`
- DM the bot "hello" → expect a reply.
- In a group (privacy mode off, bot added), reply to its message → expect a reply.
- Run `/summarize` → expect a summary.
Expected: replies arrive; messages stored in Postgres (`SELECT count(*) FROM messages;`).

- [ ] **Step 5: Commit**

```bash
git add sharof/bot.py sharof/main.py
git commit -m "feat(bot): aiogram handlers + polling entrypoint"
```

---

### Task 6: Deploy artifacts (systemd + setup docs)

**Files:**
- Create: `deploy/sharof.service`
- Create: `deploy/README.md`

**Interfaces:** none (ops artifacts).

- [ ] **Step 1: Write `deploy/sharof.service`**

```ini
[Unit]
Description=Sharof Telegram AI bot
After=network-online.target postgresql.service
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=/opt/sharof
EnvironmentFile=/opt/sharof/.env
ExecStart=/opt/sharof/.venv/bin/python -m sharof.main
Restart=always
RestartSec=5
User=sharof

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 2: Write `deploy/README.md`**

````markdown
# Deploy sharof on oraclewps

## 1. Postgres
```bash
sudo apt update && sudo apt install -y postgresql
sudo -u postgres psql <<'SQL'
CREATE USER sharof WITH PASSWORD 'CHANGE_ME';
CREATE DATABASE sharof OWNER sharof;
SQL
```

## 2. Code
```bash
sudo mkdir -p /opt/sharof && sudo chown $USER /opt/sharof
git clone https://github.com/dilyorm/sharof.git /opt/sharof
cd /opt/sharof
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env
# edit .env: TELEGRAM_TOKEN, OPENROUTER_API_KEY, DATABASE_URL (with the password above)
```

## 3. Schema
Schema auto-creates on first start (init_schema). To load manually:
```bash
psql "$DATABASE_URL" -f schema.sql
```

## 4. systemd
```bash
sudo useradd -r -s /usr/sbin/nologin sharof || true
sudo chown -R sharof /opt/sharof
sudo cp deploy/sharof.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now sharof
sudo systemctl status sharof
journalctl -u sharof -f
```

## 5. BotFather — REQUIRED
Disable group privacy so the bot reads all group messages:
`/setprivacy` → select bot → **Disable**. Then re-add the bot to groups.

## Update
```bash
cd /opt/sharof && git pull && .venv/bin/pip install -r requirements.txt
sudo systemctl restart sharof
```
````

- [ ] **Step 3: Commit**

```bash
git add deploy/sharof.service deploy/README.md
git commit -m "chore(deploy): systemd unit + server setup docs"
```

---

## Self-Review

**Spec coverage:**
- DM every-message reply → Task 5 `on_text` private branch. ✓
- Group mention/reply always answer → Task 5 mention/reply branch. ✓
- Group auto-join cheap gate (heuristic→cooldown→judge) → Task 4 + Task 5. ✓
- `/summarize` → Task 5 `on_summarize` + Task 3 `summarize`. ✓
- `/start` → Task 5. ✓
- Auto-detect language → Task 3 SYSTEM_PROMPT. ✓
- Postgres storage + schema → Task 2. ✓
- OpenRouter DeepSeek default, swappable → Task 1 config + Task 3. ✓
- Backoff on 429/5xx → Task 3 `_post`. ✓
- Cooldown persistence → Task 2 + Task 4 + Task 5 (set after reply). ✓
- Deploy: postgres install, venv, systemd, privacy-mode note → Task 6. ✓
- Config keys all present → Task 1 `.env.example` + `Settings`. ✓

**Placeholder scan:** No TBD/TODO. `CHANGE_ME` in deploy docs is intentional user-supplied secret. ✓

**Type consistency:** `Message` dataclass fields consistent across db/llm/gate tests. `should_interject` signature matches caller in `bot.py`. `_post(client, settings, messages, max_tokens)` consistent. `judge`/`chat`/`summarize` all `(client, settings, history)`. ✓

**Note on `bot_username` mutation:** `Settings` is frozen; `main.py` uses `object.__setattr__` to fill resolved username when not set in env. Acceptable one-time bootstrap; alternatively set `BOT_USERNAME` in `.env` to avoid it.
