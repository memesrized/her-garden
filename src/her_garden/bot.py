"""Optional private Telegram interface for watering plans and reminders.

Receives allowlisted usernames and a bot token from private environment variables. It reads
and changes the same PostgreSQL plans as MCP, sends due reminders, and persists deliveries.
Usernames authorize actions; chat IDs are only routing addresses for enrolled private chats.
"""

import asyncio
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time
from typing import Any, Literal
from uuid import NAMESPACE_URL, UUID, uuid5
from zoneinfo import ZoneInfo

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import BadRequest, Forbidden, TelegramError
from telegram.ext import (
    Application,
    ApplicationBuilder,
    ApplicationHandlerStop,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    TypeHandler,
    filters,
)

from her_garden.store import GardenStore, Record
from her_garden.watering import Adjustment, WateringStore

LOGGER = logging.getLogger(__name__)
USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9_]{5,32}$")
REMINDER_ACTIONS: dict[str, tuple[Literal[1, 2, 4], Adjustment, Literal["day", "hour"]]] = {
    "h1": (1, "once", "hour"),
    "h2": (2, "once", "hour"),
    "h4": (4, "once", "hour"),
    "d1": (1, "once", "day"),
    "d2": (2, "once", "day"),
    "s1": (1, "series", "day"),
    "s2": (2, "series", "day"),
}
BotApplication = Application[Any, Any, Any, Any, Any, Any]


@dataclass(frozen=True)
class BotSettings:
    """Keep bot-only credentials outside the MCP process and source tree."""

    token: str = field(repr=False)
    usernames: frozenset[str]
    database_url: str = field(repr=False)
    timezone_name: str = "UTC"

    @classmethod
    def from_env(cls) -> BotSettings:
        """Require an explicit token and comma-separated username allowlist."""
        token = os.environ["BOT_TOKEN"]
        raw_names = os.environ["TG_USERNAMES"].split(",")
        usernames = frozenset(name.removeprefix("@").casefold() for name in raw_names)
        if (
            not token
            or not raw_names
            or any(not USERNAME_PATTERN.fullmatch(name.removeprefix("@")) for name in raw_names)
        ):
            raise ValueError("BOT_TOKEN and valid TG_USERNAMES are required")
        if len(usernames) != len(raw_names):
            raise ValueError("TG_USERNAMES must not contain duplicates")
        timezone_name = os.environ.get("WATERING_TIMEZONE", "UTC")
        ZoneInfo(timezone_name)
        return cls(token, usernames, os.environ["DATABASE_URL"], timezone_name)


class GardenBot:
    """Handle private user controls and poll durable reminder jobs."""

    def __init__(self, settings: BotSettings) -> None:
        self.settings = settings
        self.garden = GardenStore(settings.database_url)
        self.watering = WateringStore(self.garden, settings.timezone_name)
        self.worker: asyncio.Task[None] | None = None

    def build_application(self) -> BotApplication:
        """Register username access checks before commands and callbacks."""
        app: BotApplication = (
            ApplicationBuilder().token(self.settings.token).concurrent_updates(False).build()
        )
        app.add_handler(TypeHandler(Update, self.handle_access), group=-1)
        app.add_handler(CommandHandler("start", self.handle_start))
        app.add_handler(CommandHandler("plants", self.handle_plants))
        app.add_handler(CommandHandler("time", self.handle_time))
        app.add_handler(CommandHandler("cancel", self.handle_cancel))
        app.add_handler(CallbackQueryHandler(self.handle_callback))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_text))
        app.add_error_handler(self.handle_error)
        app.post_init = self.initialize
        app.post_shutdown = self.shutdown
        return app

    async def initialize(self, app: BotApplication) -> None:
        """Open storage only when this optional process is started."""
        await self.garden.open()
        self.worker = asyncio.create_task(self.run_reminders(app))

    async def shutdown(self, _app: BotApplication) -> None:
        """Stop the reminder poller and close database connections."""
        if self.worker is not None:
            self.worker.cancel()
            try:
                await self.worker
            except asyncio.CancelledError:
                pass
        await self.garden.close()

    async def handle_access(self, update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
        """Accept only private updates from currently allowed usernames."""
        user, chat = update.effective_user, update.effective_chat
        username = (user.username or "").casefold() if user else ""
        if chat is None or chat.type != "private" or username not in self.settings.usernames:
            if update.callback_query:
                await update.callback_query.answer("Нет доступа", show_alert=True)
            raise ApplicationHandlerStop
        await self.watering.register_recipient(username, chat.id)

    async def handle_start(self, update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
        """Explain controls after enrolling a permitted private chat."""
        if update.effective_message:
            await update.effective_message.reply_text(
                "Напоминания о поливе включены для этого чата. "
                "Откройте /plants для расписаний, /time для общего времени. "
                "Изменения расписания доступны также через MCP."
            )

    async def handle_plants(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Show a compact plant picker with stable IDs in callback payloads."""
        self._user_data(context).clear()
        plants = await self.garden.list_entities("plant")
        plants = [plant for plant in plants if plant.get("status") not in {"dead", "given_away"}]
        if not plants:
            await self._reply(update, "Пока нет активных растений.")
            return
        rows = [
            [InlineKeyboardButton(str(plant["name"])[:60], callback_data=f"p:{plant['id']}")]
            for plant in plants[:100]
        ]
        await self._reply(update, "Выберите растение:", InlineKeyboardMarkup(rows))

    async def handle_time(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Prompt for the shared reminder clock time."""
        current = await self.watering.get_reminder_time()
        self._user_data(context)["edit"] = "time"
        await self._reply(update, f"Общее время сейчас {current}. Введите новое время как HH:MM.")

    async def handle_cancel(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Discard a local input draft without changing the schedule."""
        self._user_data(context).clear()
        await self._reply(update, "Ввод отменён.")

    async def handle_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Route a plant selection or one of the seven durable reminder buttons."""
        query = update.callback_query
        assert query is not None
        await query.answer()
        data = str(query.data or "")
        try:
            if data.startswith("w:"):
                await self._handle_reminder(update, data)
            elif data.startswith("p:"):
                await self._show_plant(update, UUID(data[2:]))
            elif data.startswith("e:"):
                kind, raw_plant_id = data[2:].split(":", 1)
                await self._prompt_plant(update, context, kind, UUID(raw_plant_id))
            elif data.startswith("x:"):
                selected_plant_id = UUID(data[2:])
                await self.watering.clear_schedule(self._request_id(update), selected_plant_id)
                await self._show_plant(update, selected_plant_id)
        except (ValueError, KeyError) as error:
            await self._reply(update, str(error))

    async def handle_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Apply a previously requested cadence, anchor date, or common time."""
        user_data = self._user_data(context)
        kind = user_data.get("edit")
        if not kind or not update.effective_message:
            return
        value = (update.effective_message.text or "").strip()
        try:
            if kind == "time":
                parsed = time.fromisoformat(value)
                if parsed.second or parsed.microsecond or parsed.tzinfo or len(value) != 5:
                    raise ValueError("Введите время как HH:MM")
                await self.watering.set_reminder_time(
                    self._request_id(update), parsed, datetime.now(UTC)
                )
                await self._reply(update, f"Общее время напоминаний: {value}.")
            else:
                plant_id = UUID(str(user_data["plant_id"]))
                schedule = await self.watering.get_schedule(plant_id)
                if kind == "new":
                    parts = value.split()
                    if len(parts) != 2:
                        raise ValueError("Введите число дней и дату: 3 2026-10-01")
                    cadence, anchor = int(parts[0]), date.fromisoformat(parts[1])
                elif kind == "cadence" and schedule:
                    cadence, anchor = int(value), date.fromisoformat(schedule["anchor_date"])
                elif kind == "anchor" and schedule:
                    cadence, anchor = int(schedule["cadence_days"]), date.fromisoformat(value)
                else:
                    raise ValueError("Расписание устарело. Откройте /plants ещё раз")
                await self.watering.set_schedule(
                    self._request_id(update), plant_id, anchor, cadence, datetime.now(UTC)
                )
                await self._show_plant(update, plant_id)
            user_data.clear()
        except (ValueError, KeyError) as error:
            await self._reply(update, f"{error}\nПопробуйте снова или /cancel.")

    async def _show_plant(self, update: Update, plant_id: UUID) -> None:
        plants = await self.garden.list_entities("plant")
        plant = next((item for item in plants if item["id"] == str(plant_id)), None)
        if plant is None:
            raise ValueError("Растение больше недоступно")
        schedule = await self.watering.get_schedule(plant_id)
        if schedule is None or not schedule["enabled"]:
            rows = [
                [InlineKeyboardButton("Добавить расписание", callback_data=f"e:new:{plant_id}")]
            ]
            description = "Расписание не задано."
        else:
            local_due = datetime.fromisoformat(schedule["next_due_at"]).astimezone(
                ZoneInfo(self.settings.timezone_name)
            )
            description = (
                f"Каждые {schedule['cadence_days']} дн. с {schedule['anchor_date']}. "
                f"Следующее напоминание: {local_due:%Y-%m-%d %H:%M}."
            )
            rows = [
                [InlineKeyboardButton("Изменить интервал", callback_data=f"e:cadence:{plant_id}")],
                [
                    InlineKeyboardButton(
                        "Изменить дату начала", callback_data=f"e:anchor:{plant_id}"
                    )
                ],
                [InlineKeyboardButton("Отключить", callback_data=f"x:{plant_id}")],
            ]
        await self._reply(
            update,
            f"{plant['name']}\n{description}",
            InlineKeyboardMarkup(rows),
        )

    async def _prompt_plant(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE, kind: str, plant_id: UUID
    ) -> None:
        if kind not in {"new", "cadence", "anchor"}:
            raise ValueError("Неизвестное действие")
        user_data = self._user_data(context)
        user_data["edit"] = kind
        user_data["plant_id"] = str(plant_id)
        prompt = {
            "new": "Введите интервал и дату начала: 3 2026-10-01",
            "cadence": "Введите новый интервал в днях, например 3",
            "anchor": "Введите новую дату начала как YYYY-MM-DD",
        }[kind]
        await self._reply(update, prompt)

    async def _handle_reminder(self, update: Update, data: str) -> None:
        parts = data.split(":")
        if len(parts) != 3 or parts[2] not in REMINDER_ACTIONS:
            raise ValueError("Кнопка устарела")
        notification_id = UUID(parts[1])
        amount, mode, unit = REMINDER_ACTIONS[parts[2]]
        user, chat = update.effective_user, update.effective_chat
        assert user is not None and chat is not None and user.username is not None
        results = await self.watering.apply_notification_action(
            notification_id,
            user.username.casefold(),
            chat.id,
            amount,
            mode,
            unit,
            datetime.now(UTC),
        )
        query = update.callback_query
        assert query is not None
        try:
            message_text = str(getattr(query.message, "text", "") or "")
            await query.edit_message_text(
                text=f"{message_text[:3900]}\n\nПеренос применён ко всем растениям "
                f"этого напоминания ({len(results)})."
            )
        except BadRequest:
            await self._reply(update, f"Перенос применён. Растений: {len(results)}.")

    async def run_reminders(self, app: BotApplication) -> None:
        """Poll due plans and retry unsent delivery jobs after restarts."""
        while True:
            try:
                await self.watering.enqueue_due(datetime.now(UTC), self.settings.usernames)
                groups: dict[tuple[UUID, str, int], list[Record]] = {}
                for job in await self.watering.pending_notifications():
                    key = (job["cycle_id"], job["username"], job["chat_id"])
                    groups.setdefault(key, []).append(job)
                for jobs in groups.values():
                    await self._deliver(app, jobs)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                LOGGER.error("Reminder cycle failed (%s)", type(error).__name__)
            await asyncio.sleep(60)

    async def _deliver(self, app: BotApplication, jobs: list[Record]) -> None:
        """Send one reminder listing all still-current plants in a due group."""
        current = []
        for job in jobs:
            if (
                job["username"] not in self.settings.usernames
                or not job["enabled"]
                or job["active_cycle_id"] != job["cycle_id"]
                or job["archived"] == "true"
                or job["plant_status"] in {"dead", "given_away"}
            ):
                await self.watering.cancel_notification(job["id"])
            else:
                current.append(job)
        if not current:
            return
        first = current[0]
        try:
            chat = await app.bot.get_chat(first["chat_id"])
            if chat.type != "private" or (chat.username or "").casefold() != first["username"]:
                for job in current:
                    await self.watering.cancel_notification(job["id"])
                return
            message = await app.bot.send_message(
                chat_id=first["chat_id"],
                text=self._reminder_text(current),
                reply_markup=self._reminder_keyboard(first["id"]),
            )
        except Forbidden, BadRequest:
            for job in current:
                await self.watering.cancel_notification(job["id"])
            return
        except TelegramError as error:
            LOGGER.warning("Reminder send will retry (%s)", type(error).__name__)
            return
        await self.watering.mark_sent(first["id"], message.message_id)

    @staticmethod
    def _reminder_text(jobs: list[Record]) -> str:
        """Fit a due group into Telegram's single-message text limit."""
        lines = ["Пора полить:"]
        length = len(lines[0])
        for index, job in enumerate(jobs):
            name = str(job["plant_name"]).replace("\n", " ").replace("\r", " ")[:120]
            line = f"• {name}"
            if length + len(line) + 100 > 4000:
                lines.append(f"…и ещё {len(jobs) - index} растений; кнопки действуют на всех.")
                break
            lines.append(line)
            length += len(line) + 1
        return "\n".join(lines)

    @staticmethod
    def _reminder_keyboard(notification_id: UUID) -> InlineKeyboardMarkup:
        prefix = f"w:{notification_id.hex}:"
        return InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("На 1 час", callback_data=prefix + "h1"),
                    InlineKeyboardButton("На 2 часа", callback_data=prefix + "h2"),
                    InlineKeyboardButton("На 4 часа", callback_data=prefix + "h4"),
                ],
                [
                    InlineKeyboardButton("На 1 день", callback_data=prefix + "d1"),
                    InlineKeyboardButton("На 2 дня", callback_data=prefix + "d2"),
                ],
                [
                    InlineKeyboardButton("Сдвиг серии +1 день", callback_data=prefix + "s1"),
                    InlineKeyboardButton("Сдвиг серии +2 дня", callback_data=prefix + "s2"),
                ],
            ]
        )

    @staticmethod
    def _request_id(update: Update) -> UUID:
        return uuid5(NAMESPACE_URL, f"her-garden-bot:{update.update_id}")

    @staticmethod
    def _user_data(context: ContextTypes.DEFAULT_TYPE) -> dict[Any, Any]:
        data = context.user_data
        assert data is not None
        return data

    @staticmethod
    async def _reply(update: Update, text: str, markup: InlineKeyboardMarkup | None = None) -> None:
        message = update.effective_message
        if message:
            await message.reply_text(text, reply_markup=markup)

    @staticmethod
    async def handle_error(_update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Log only an error category, never incoming text or credentials."""
        LOGGER.error("Bot update failed (%s)", type(context.error).__name__)


def main() -> None:
    """Run one optional polling consumer for this bot token."""
    logging.basicConfig(level=logging.INFO)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    bot = GardenBot(BotSettings.from_env())
    bot.build_application().run_polling(
        allowed_updates=["message", "callback_query"],
        drop_pending_updates=False,
        bootstrap_retries=3,
    )
