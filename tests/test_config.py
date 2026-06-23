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
