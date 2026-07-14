import json

import httpx
import pytest

from sharof import agent
from sharof.config import Settings
from sharof.db import Message

OPENROUTER = "openrouter.ai"


def _settings():
    return Settings(
        telegram_token="t", openrouter_api_key="k", openrouter_model="m:free",
        database_url="d", context_msgs=20, interject_cooldown_sec=300,
        trigger_words=[], bot_username="sharof_bot",
        owner_ids=frozenset({7}), pc_url="http://pc", pc_secret="s",
    )


def _assistant(content=None, tool_calls=None):
    msg = {"role": "assistant", "content": content}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    return httpx.Response(200, json={"choices": [{"message": msg}]})


def _call(name, args, cid="c1"):
    return [{
        "id": cid, "type": "function",
        "function": {"name": name, "arguments": json.dumps(args)},
    }]


@pytest.mark.asyncio
async def test_tool_call_then_final_answer():
    seen = {"llm": 0, "pc": None, "tool_msgs": []}

    def handler(request: httpx.Request) -> httpx.Response:
        if OPENROUTER in request.url.host:
            seen["llm"] += 1
            body = json.loads(request.content)
            seen["tool_msgs"] = [m for m in body["messages"] if m.get("role") == "tool"]
            if seen["llm"] == 1:
                assert any(t["function"]["name"] == "pc_intent" for t in body["tools"])
                return _assistant(tool_calls=_call("pc_intent", {"text": "open youtube"}))
            return _assistant(content="YouTube ochildi.")
        seen["pc"] = json.loads(request.content)
        return httpx.Response(200, json={"reply": "Opening YouTube"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        out = await agent.handle(
            client, _settings(), [Message(7, "dm", "open youtube on my pc", False)]
        )

    assert out == "YouTube ochildi."
    assert seen["pc"] == {"text": "open youtube"}
    assert seen["tool_msgs"][0]["content"] == "Opening YouTube"


@pytest.mark.asyncio
async def test_plain_chat_needs_no_tools():
    def handler(request):
        return _assistant(content="Salom!")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        out = await agent.handle(client, _settings(), [Message(7, "dm", "salom", False)])
    assert out == "Salom!"


@pytest.mark.asyncio
async def test_loop_stops_at_max_iters():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if OPENROUTER in request.url.host:
            calls["n"] += 1
            return _assistant(tool_calls=_call("pc_intent", {"text": "again"}))
        return httpx.Response(200, json={"reply": "ok"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        out = await agent.handle(client, _settings(), [Message(7, "dm", "loop", False)])

    assert calls["n"] == agent.MAX_ITERS
    assert "couldn't finish" in out.lower()
