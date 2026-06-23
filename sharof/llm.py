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
