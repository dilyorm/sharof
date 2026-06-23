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
