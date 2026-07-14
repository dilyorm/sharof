from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

DEFAULT_MODEL = "openai/gpt-oss-120b:free"
# The controller model must actually call tools. Measured against a real chat history
# where the bot had already answered PC commands in prose a few times, DeepSeek imitated
# its own past replies and called no tool in half the runs; gemini-2.5-flash-lite called
# the tool every time, for a third of the price. It is also multimodal, so it doubles as
# the vision model.
DEFAULT_TOOL_MODEL = "google/gemini-2.5-flash-lite"
DEFAULT_VISION_MODEL = "google/gemini-2.5-flash-lite"


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
    # remote controller
    owner_ids: frozenset[int] = frozenset()
    openrouter_tool_model: str = DEFAULT_TOOL_MODEL
    openrouter_vision_model: str = DEFAULT_VISION_MODEL
    pc_url: str = "http://127.0.0.1:9099"
    pc_secret: str = ""
    allow_shell: bool = True


def _require(env: Mapping[str, str], key: str) -> str:
    val = env.get(key)
    if not val:
        raise ValueError(f"Missing required env var: {key}")
    return val


def _int_set(raw: str) -> frozenset[int]:
    return frozenset(int(p) for p in raw.replace(" ", "").split(",") if p)


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
        owner_ids=_int_set(env.get("OWNER_IDS", "") or ""),
        openrouter_tool_model=env.get("OPENROUTER_TOOL_MODEL") or DEFAULT_TOOL_MODEL,
        openrouter_vision_model=env.get("OPENROUTER_VISION_MODEL") or DEFAULT_VISION_MODEL,
        pc_url=(env.get("PC_URL") or "http://127.0.0.1:9099").rstrip("/"),
        pc_secret=env.get("PC_SECRET", "") or "",
        allow_shell=(env.get("ALLOW_SHELL", "true") or "true").lower() != "false",
    )
