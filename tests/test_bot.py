"""Check username allowlisting without a live Telegram connection."""

from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest
from telegram.ext import ApplicationHandlerStop

from her_garden.bot import BotSettings, GardenBot


def test_bot_settings_accept_multiple_usernames(monkeypatch: pytest.MonkeyPatch) -> None:
    """The comma-separated allowlist is normalized and duplicates are rejected."""
    monkeypatch.setenv("BOT_TOKEN", "test-only-token")
    monkeypatch.setenv("TG_USERNAMES", "First_Plant,second_plant")
    monkeypatch.setenv("DATABASE_URL", "postgresql://test-only")
    settings = BotSettings.from_env()
    assert settings.usernames == frozenset({"first_plant", "second_plant"})
    monkeypatch.setenv("TG_USERNAMES", "First_Plant,first_plant")
    with pytest.raises(ValueError, match="duplicates"):
        BotSettings.from_env()


@pytest.mark.parametrize(
    ("username", "chat_type", "allowed"),
    [
        ("First_Plant", "private", True),
        ("second_plant", "private", True),
        ("unknown_plant", "private", False),
        (None, "private", False),
        ("First_Plant", "group", False),
    ],
)
async def test_access_uses_username_and_private_chat(
    username: str | None, chat_type: str, allowed: bool
) -> None:
    """A chat address alone never grants permission to bot controls."""
    bot = GardenBot(
        BotSettings(
            "test-only-token",
            frozenset({"first_plant", "second_plant"}),
            "postgresql://test-only",
        )
    )
    bot.watering.register_recipient = AsyncMock()  # type: ignore[method-assign]
    update: Any = Mock()
    update.effective_user.username = username
    update.effective_chat.type = chat_type
    update.effective_chat.id = 1001
    update.callback_query = None
    if allowed:
        await bot.handle_access(update, Mock())
        bot.watering.register_recipient.assert_awaited_once_with(
            "first_plant" if username == "First_Plant" else "second_plant", 1001
        )
    else:
        with pytest.raises(ApplicationHandlerStop):
            await bot.handle_access(update, Mock())
        bot.watering.register_recipient.assert_not_awaited()
