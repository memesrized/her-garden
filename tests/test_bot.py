"""Check username allowlisting and grouped delivery without live Telegram."""

import asyncio
from typing import Any
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

import pytest
from telegram.error import TelegramError
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


@pytest.mark.parametrize("menu_failure", [False, True])
async def test_due_group_sends_one_message_with_all_plants(menu_failure: bool) -> None:
    """Menu setup failure cannot prevent due messages reaching authorized chats."""
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
    app.bot.set_my_commands = AsyncMock(
        return_value=True,
        side_effect=TelegramError("temporary") if menu_failure else None,
    )
    app.bot.set_chat_menu_button = AsyncMock(return_value=True)
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
    app.bot.set_my_commands.assert_awaited_once()
    if menu_failure:
        app.bot.set_chat_menu_button.assert_not_awaited()
        assert not bot.menu_ready
    else:
        app.bot.set_chat_menu_button.assert_awaited_once()
        assert bot.menu_ready
    commands = app.bot.set_my_commands.call_args.args[0]
    assert [command.command for command in commands] == [
        "plants",
        "watering_plan",
        "time",
        "help",
        "cancel",
    ]


async def test_plant_picker_groups_locations_and_pages() -> None:
    """A location with many plants stays on short pages and preserves its back link."""
    bot = GardenBot(BotSettings("test-only-token", frozenset(), "postgresql://test-only"))
    first_id, second_id = uuid4(), uuid4()
    locations = [
        {"id": str(first_id), "name": "Balcony"},
        {"id": str(second_id), "name": "Shelf"},
    ]
    plants = [
        {
            "id": str(uuid4()),
            "name": f"Crassula {index:02}",
            "location_id": str(first_id),
            "status": "active",
        }
        for index in range(13)
    ]
    plants.extend(
        [
            {"id": str(uuid4()), "name": "Monstera", "location_id": str(second_id)},
            {"id": str(uuid4()), "name": "Cutting"},
            {"id": str(uuid4()), "name": "Retired", "status": "dead"},
        ]
    )
    bot.garden.list_entities = AsyncMock(  # type: ignore[method-assign]
        side_effect=lambda kind: plants if kind == "plant" else locations
    )
    bot.watering.get_schedule = AsyncMock(return_value=None)  # type: ignore[method-assign]
    update: Any = Mock()
    update.callback_query = None
    update.effective_message.reply_text = AsyncMock()
    context: Any = Mock(user_data={})
    await bot.handle_plants(update, context)
    group_markup = update.effective_message.reply_text.call_args.kwargs["reply_markup"]
    assert [row[0].text for row in group_markup.inline_keyboard] == [
        "Balcony (13)",
        "Shelf (1)",
        "Без места (1)",
    ]

    update.callback_query = Mock()
    update.callback_query.answer = AsyncMock()
    update.callback_query.edit_message_text = AsyncMock()
    update.callback_query.data = f"l:{first_id}:0"
    context.user_data["edit"] = "time"
    await bot.handle_callback(update, context)
    assert context.user_data == {}
    first_markup = update.callback_query.edit_message_text.call_args.kwargs["reply_markup"]
    assert len(first_markup.inline_keyboard) == 14
    assert first_markup.inline_keyboard[12][0].text == "Далее →"
    update.callback_query.data = f"l:{first_id}:1"
    await bot.handle_callback(update, context)
    second_markup = update.callback_query.edit_message_text.call_args.kwargs["reply_markup"]
    assert len(second_markup.inline_keyboard) == 3
    assert second_markup.inline_keyboard[0][0].callback_data == f"p:{plants[12]['id']}:1"
    update.callback_query.data = f"p:{plants[12]['id']}:1"
    await bot.handle_callback(update, context)
    plant_markup = update.callback_query.edit_message_text.call_args.kwargs["reply_markup"]
    assert plant_markup.inline_keyboard[-1][0].callback_data == f"l:{first_id}:1"
    update.callback_query.data = "l:none:0"
    await bot.handle_callback(update, context)
    unassigned_markup = update.callback_query.edit_message_text.call_args.kwargs["reply_markup"]
    assert len(unassigned_markup.inline_keyboard) == 2
    assert unassigned_markup.inline_keyboard[0][0].text == "Cutting"


async def test_start_offers_visible_navigation_buttons() -> None:
    """The start screen lets an authorized user navigate without typing commands."""
    bot = GardenBot(BotSettings("test-only-token", frozenset(), "postgresql://test-only"))
    update: Any = Mock(callback_query=None)
    update.effective_message.reply_text = AsyncMock()
    await bot.handle_start(update, Mock())
    markup = update.effective_message.reply_text.call_args.kwargs["reply_markup"]
    assert [row[0].text for row in markup.inline_keyboard] == [
        "Растения",
        "График полива",
        "Общее время",
    ]


async def test_watering_plan_shows_only_active_schedules_in_local_time() -> None:
    """A short plan groups locations and excludes unscheduled or inactive plants."""
    bot = GardenBot(
        BotSettings("test-only-token", frozenset(), "postgresql://test-only", "Europe/Berlin")
    )
    location_id = str(uuid4())
    first_id, second_id, unused_id, dead_id = (str(uuid4()) for _ in range(4))
    plants = [
        {"id": first_id, "name": "Crassula", "location_id": location_id},
        {"id": second_id, "name": "Monstera"},
        {"id": unused_id, "name": "No plan", "location_id": location_id},
        {"id": dead_id, "name": "Retired", "status": "dead"},
    ]
    schedules = [
        {
            "plant_id": plant_id,
            "anchor_date": "2026-10-01",
            "cadence_days": cadence,
            "next_due_at": "2026-10-04T16:00:00+00:00",
        }
        for plant_id, cadence in ((first_id, 3), (second_id, 5), (dead_id, 7))
    ]
    bot.garden.list_entities = AsyncMock(  # type: ignore[method-assign]
        side_effect=lambda kind: (
            plants if kind == "plant" else [{"id": location_id, "name": "Shelf"}]
        )
    )
    bot.watering.list_schedules = AsyncMock(return_value=schedules)  # type: ignore[method-assign]
    update: Any = Mock(callback_query=None)
    update.effective_message.reply_text = AsyncMock()
    context: Any = Mock(user_data={"edit": "time"})
    await bot.handle_watering_plan(update, context)
    text = update.effective_message.reply_text.call_args.args[0]
    assert context.user_data == {}
    assert "Shelf" in text and "Без места" in text
    assert "Crassula" in text and "Monstera" in text
    assert "No plan" not in text and "Retired" not in text
    assert "каждые 3 дн. с 01.10.2026" in text
    assert text.count("04.10.2026 18:00") == 2

    bot.watering.list_schedules.return_value = []
    await bot.handle_watering_plan(update, context)
    assert update.effective_message.reply_text.call_args.args[0] == (
        "Нет растений с включённым графиком полива."
    )


async def test_watering_plan_pages_large_location_and_unassigned_group() -> None:
    """Long plans show location buttons and at most ten entries per message."""
    bot = GardenBot(BotSettings("test-only-token", frozenset(), "postgresql://test-only"))
    location_id = str(uuid4())
    plants = [
        {"id": str(uuid4()), "name": f"Crassula {index:02}", "location_id": location_id}
        for index in range(12)
    ]
    plants.append({"id": str(uuid4()), "name": "Unassigned"})
    plants.append({"id": str(uuid4()), "name": "No plan"})
    schedules = [
        {
            "plant_id": plant["id"],
            "anchor_date": "2026-10-01",
            "cadence_days": 3,
            "next_due_at": "2026-10-04T09:00:00+00:00",
        }
        for plant in plants[:-1]
    ]
    bot.garden.list_entities = AsyncMock(  # type: ignore[method-assign]
        side_effect=lambda kind: (
            plants if kind == "plant" else [{"id": location_id, "name": "Balcony"}]
        )
    )
    bot.watering.list_schedules = AsyncMock(return_value=schedules)  # type: ignore[method-assign]
    update: Any = Mock(callback_query=None)
    update.effective_message.reply_text = AsyncMock()
    context: Any = Mock(user_data={})
    await bot.handle_watering_plan(update, context)
    markup = update.effective_message.reply_text.call_args.kwargs["reply_markup"]
    assert [row[0].text for row in markup.inline_keyboard] == ["Balcony (12)", "Без места (1)"]

    update.callback_query = Mock()
    update.callback_query.answer = AsyncMock()
    update.callback_query.edit_message_text = AsyncMock()
    update.callback_query.data = f"wpl:{location_id}:0"
    await bot.handle_callback(update, context)
    first = update.callback_query.edit_message_text.call_args
    assert "Crassula 00" in first.kwargs["text"]
    assert "Crassula 09" in first.kwargs["text"]
    assert "Crassula 10" not in first.kwargs["text"]
    assert first.kwargs["text"].count("Следующее:") == 10
    assert first.kwargs["reply_markup"].inline_keyboard[0][0].callback_data == (
        f"wpl:{location_id}:1"
    )

    update.callback_query.data = f"wpl:{location_id}:1"
    await bot.handle_callback(update, context)
    second = update.callback_query.edit_message_text.call_args
    assert "Crassula 10" in second.kwargs["text"]
    assert "Crassula 11" in second.kwargs["text"]
    assert second.kwargs["text"].count("Следующее:") == 2
    assert second.kwargs["reply_markup"].inline_keyboard[-1][0].callback_data == "wp"

    update.callback_query.data = "wpl:none:0"
    await bot.handle_callback(update, context)
    assert "Unassigned" in update.callback_query.edit_message_text.call_args.kwargs["text"]
    assert "No plan" not in update.callback_query.edit_message_text.call_args.kwargs["text"]
