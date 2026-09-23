"""Check username allowlisting and grouped delivery without live Telegram."""

import asyncio
from typing import Any
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

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


async def test_due_group_sends_one_message_with_all_plants() -> None:
    """Two due plans produce one complete message for each authorized chat."""
    bot = GardenBot(
        BotSettings(
            "test-only-token",
            frozenset({"first_plant", "second_plant"}),
            "postgresql://test-only",
        )
    )
    cycle_id = uuid4()
    jobs = [
        {
            "id": uuid4(),
            "cycle_id": cycle_id,
            "username": username,
            "chat_id": chat_id,
            "plant_name": name,
            "enabled": True,
            "active_cycle_id": cycle_id,
            "archived": "false",
            "plant_status": "active",
        }
        for username, chat_id in (("first_plant", 1001), ("second_plant", 1002))
        for name in ("Crassula", "Monstera")
    ]
    bot.watering.enqueue_due = AsyncMock(return_value=4)  # type: ignore[method-assign]
    bot.watering.pending_notifications = AsyncMock(return_value=jobs)  # type: ignore[method-assign]
    bot.watering.mark_sent = AsyncMock()  # type: ignore[method-assign]
    bot._deliver = AsyncMock(wraps=bot._deliver)  # type: ignore[method-assign]
    app: Any = Mock()
    app.bot.get_chat = AsyncMock(
        side_effect=lambda chat_id: Mock(
            type="private", username="first_plant" if chat_id == 1001 else "second_plant"
        )
    )
    app.bot.send_message = AsyncMock(return_value=Mock(message_id=77))
    with patch("her_garden.bot.asyncio.sleep", new=AsyncMock(side_effect=asyncio.CancelledError)):
        with pytest.raises(asyncio.CancelledError):
            await bot.run_reminders(app)
    assert bot._deliver.await_count == 2
    assert app.bot.send_message.await_count == 2
    assert {call.kwargs["chat_id"] for call in app.bot.send_message.call_args_list} == {
        1001,
        1002,
    }
    for call in app.bot.send_message.call_args_list:
        sent = call.kwargs
        assert "Crassula" in sent["text"]
        assert "Monstera" in sent["text"]
        assert sum(len(row) for row in sent["reply_markup"].inline_keyboard) == 7
    assert bot.watering.mark_sent.await_count == 2
