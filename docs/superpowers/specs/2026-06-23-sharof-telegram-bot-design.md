# sharof — Telegram AI Chat Bot — Design

Date: 2026-06-23
Status: Approved

## Goal

Telegram bot powered by OpenRouter free models (default DeepSeek V3). Acts as a
general AI chat assistant. Answers DMs, reads group chats, keeps per-chat context,
summarizes on demand, and joins group conversations when relevant — without
burning OpenRouter free-tier rate limits.

## Stack

- Python 3.12 (async)
- aiogram 3 — Telegram client, long-polling (no public HTTPS required)
- asyncpg — Postgres access
- httpx — OpenRouter HTTP calls
- Postgres — installed locally on `oraclewps`
- systemd — process supervision, auto-restart
- Deploy target: `oraclewps` (Ubuntu 24.04, aarch64, Python 3.12)

aiogram 3 chosen over python-telegram-bot for clean async ergonomics.

## Behavior

### DM
Every message → AI reply. Context = that DM's recent history.

### Group — always reply (no exceptions)
- Bot is @mentioned, OR
- A user replies to one of the bot's messages.

### Group — auto-join ("join when needed"), quota-safe cheap gate
Bot reads and stores all group messages. To decide whether to interject without
calling the LLM on every message:

1. **Heuristics (free):** message ends with `?`, mentions bot name without `@`,
   or contains configured trigger words.
2. **Cooldown:** at most one auto-interjection per group per
   `INTERJECT_COOLDOWN_SEC` (default 300s). Caps spam and quota use.
3. **Judge (optional, cheap):** only when a heuristic matched AND cooldown has
   elapsed, make one small LLM judge call ("is it useful to reply here?").
4. If judge says yes → full LLM reply, update cooldown timestamp.

### Commands
- `/start` — short intro.
- `/summarize` — summarize last N messages of the current chat.

### Reply language
Auto-detect: reply in the language the user wrote (Uzbek / Russian / English / etc.).

### Telegram privacy mode
Must be DISABLED in BotFather so the bot receives all group messages, not only
commands and mentions. Documented in deploy notes.

## Modules

| file          | responsibility                                                        |
|---------------|-----------------------------------------------------------------------|
| `config.py`   | Load and validate `.env` settings.                                    |
| `db.py`       | asyncpg pool, schema init, store message, fetch last-K, cooldown I/O. |
| `llm.py`      | OpenRouter calls: chat reply, summarize, judge. 429/5xx backoff.      |
| `gate.py`     | Cheap-gate decision: heuristics + cooldown + judge orchestration.     |
| `bot.py`      | aiogram handlers: DM, group message, mention/reply, /summarize, /start.|
| `main.py`     | Wire components, start polling.                                        |
| `schema.sql`  | Database schema.                                                       |
| `.env.example`| Template config.                                                      |
| `deploy/`     | systemd unit + setup/deploy notes.                                    |

Each module has one purpose, communicates via explicit functions, and is testable
in isolation (llm and db mockable; gate is pure given inputs).

## Data flow

```
incoming update
  → store message in Postgres
  → classify: DM? group-always-reply? group-gate?
      DM / always-reply → proceed
      group-gate → gate.decide() → stop or proceed
  → fetch last-K messages for chat as context
  → llm.chat(context) → reply text
  → send reply, store bot message
```

## Database schema

```sql
CREATE TABLE messages (
    id         BIGSERIAL PRIMARY KEY,
    chat_id    BIGINT      NOT NULL,
    chat_type  TEXT        NOT NULL,   -- 'private' | 'group' | 'supergroup'
    user_id    BIGINT,
    username   TEXT,
    text       TEXT        NOT NULL,
    is_bot     BOOLEAN     NOT NULL DEFAULT FALSE,
    ts         TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_messages_chat_ts ON messages (chat_id, ts DESC);

CREATE TABLE chat_state (
    chat_id           BIGINT PRIMARY KEY,
    last_interject_ts TIMESTAMPTZ
);
```

## Error handling

- OpenRouter 429 / 5xx → exponential backoff with bounded retries.
- On exhausted retries for a real reply → send short "busy, try again later"
  message; for gate/judge failure → silently skip interjection.
- DB failure → log and skip; never crash the polling loop.
- All quota-heavy work is gated: no full LLM call without passing always-reply or
  the cheap gate.

## Configuration (.env)

| key                     | default                                   | meaning                          |
|-------------------------|-------------------------------------------|----------------------------------|
| `TELEGRAM_TOKEN`        | —                                         | BotFather token.                 |
| `OPENROUTER_API_KEY`    | —                                         | OpenRouter key.                  |
| `OPENROUTER_MODEL`      | `deepseek/deepseek-chat-v3-0324:free`     | Model id (swappable).            |
| `DATABASE_URL`          | `postgresql://sharof:...@localhost/sharof`| Postgres DSN.                    |
| `CONTEXT_MSGS`          | `20`                                      | Messages of history per reply.   |
| `INTERJECT_COOLDOWN_SEC`| `300`                                     | Min seconds between auto-joins.  |
| `TRIGGER_WORDS`         | (optional, comma list)                    | Extra auto-join heuristics.      |

## Deploy

1. `apt install postgresql`; create `sharof` db + user; load `schema.sql`.
2. Clone repo to `oraclewps`, create venv, `pip install -r requirements.txt`.
3. Fill `.env`.
4. Install systemd unit (`sharof.service`), enable + start. Auto-restart on failure.
5. In BotFather: disable group privacy mode so bot reads all group messages.

## Out of scope (YAGNI)

- Webhook mode (long-polling is enough for one server, no domain needed).
- Multi-model routing / fallback chains (single configurable model for now).
- Image / voice handling (text only).
- Admin dashboard.
