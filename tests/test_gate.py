from datetime import datetime, timedelta, timezone

import pytest

from sharof import gate
from sharof.config import Settings
from sharof.db import Message


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


@pytest.mark.asyncio
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


@pytest.mark.asyncio
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


@pytest.mark.asyncio
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
