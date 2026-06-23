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
