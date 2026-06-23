import httpx
import pytest

from sharof import llm
from sharof.config import Settings
from sharof.db import Message

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


@pytest.mark.asyncio
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


@pytest.mark.asyncio
async def test_judge_parses_yes_no():
    def handler(request):
        return httpx.Response(200, json={
            "choices": [{"message": {"content": "YES"}}]
        })
    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        assert await llm.judge(client, _settings(), [Message(1, "u", "q?", False)]) is True


@pytest.mark.asyncio
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


@pytest.mark.asyncio
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
