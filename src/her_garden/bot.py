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

from telegram import (
    BotCommand,
    BotCommandScopeAllPrivateChats,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    MenuButtonCommands,
    Update,
)
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
PLANTS_PER_PAGE = 12


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
        self.menu_ready = False

    def build_application(self) -> BotApplication:
        """Register username access checks before commands and callbacks."""
        app: BotApplication = (
            ApplicationBuilder().token(self.settings.token).concurrent_updates(False).build()
        )
        app.add_handler(TypeHandler(Update, self.handle_access), group=-1)
        app.add_handler(CommandHandler("start", self.handle_start))
        app.add_handler(CommandHandler("plants", self.handle_plants))
        app.add_handler(CommandHandler("time", self.handle_time))
        app.add_handler(CommandHandler("help", self.handle_help))
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
        await self._reply(
            update,
            "Напоминания о поливе включены для этого чата. "
            "Откройте растения или общее время кнопкой ниже. "
            "Изменения расписания доступны также через MCP.",
            self._menu_keyboard(),
        )

    async def handle_plants(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Show locations first so a large plant collection stays navigable."""
        self._user_data(context).clear()
        await self._show_locations(update)

    async def handle_help(self, update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
        """Explain the bot's controls without requiring command recall."""
        await self._reply(
            update,
            "Растения: выберите место, затем растение и измените его график. "
            "Общее время: задайте час плановых напоминаний. "
            "Кнопки под напоминанием переносят все растения в нём. "
            "Отменить незаконченный ввод можно командой /cancel.",
            self._menu_keyboard(),
        )

    async def _show_locations(self, update: Update) -> None:
        plants = await self._active_plants()
        if not plants:
            await self._show_menu(update, "Пока нет активных растений.", self._menu_keyboard())
            return
        locations = await self.garden.list_entities("location")
        counts = self._location_counts(plants, locations)
        rows = [
            [InlineKeyboardButton(f"{name[:50]} ({count})", callback_data=f"l:{key}:0")]
            for key, name, count in counts
        ]
        await self._show_menu(update, "Выберите место:", InlineKeyboardMarkup(rows))

    async def _show_location(self, update: Update, location_key: str, page: int) -> None:
        if page < 0:
            raise ValueError("Страница недоступна")
        locations = await self.garden.list_entities("location")
        names = {str(location["id"]): str(location["name"]) for location in locations}
        if location_key != "none" and location_key not in names:
            raise ValueError("Место больше недоступно. Откройте /plants снова")
        plants = await self._active_plants()
        selected = [plant for plant in plants if self._location_key(plant, names) == location_key]
        if not selected:
            await self._show_menu(
                update,
                "В этом месте больше нет активных растений.",
                InlineKeyboardMarkup([[InlineKeyboardButton("← К местам", callback_data="g")]]),
            )
            return
        total_pages = (len(selected) + PLANTS_PER_PAGE - 1) // PLANTS_PER_PAGE
        page = min(page, total_pages - 1)
        rows = [
            [InlineKeyboardButton(str(plant["name"])[:60], callback_data=f"p:{plant['id']}:{page}")]
            for plant in selected[page * PLANTS_PER_PAGE : (page + 1) * PLANTS_PER_PAGE]
        ]
        navigation = []
        if page:
            navigation.append(
                InlineKeyboardButton("← Назад", callback_data=f"l:{location_key}:{page - 1}")
            )
        if page + 1 < total_pages:
            navigation.append(
                InlineKeyboardButton("Далее →", callback_data=f"l:{location_key}:{page + 1}")
            )
        if navigation:
            rows.append(navigation)
        rows.append([InlineKeyboardButton("← К местам", callback_data="g")])
        name = names.get(location_key, "Без места")
        await self._show_menu(
            update,
            f"{name} · {len(selected)} растений · {page + 1}/{total_pages}",
            InlineKeyboardMarkup(rows),
        )

    async def _active_plants(self) -> list[Record]:
        plants = await self.garden.list_entities("plant")
        return [plant for plant in plants if plant.get("status") not in {"dead", "given_away"}]

    @staticmethod
    def _location_key(plant: Record, location_names: dict[str, str]) -> str:
        key = str(plant.get("location_id") or "none")
        return key if key in location_names else "none"

    @classmethod
    def _location_counts(
        cls, plants: list[Record], locations: list[Record]
    ) -> list[tuple[str, str, int]]:
        names = {str(location["id"]): str(location["name"]) for location in locations}
        counts: dict[str, int] = {}
        for plant in plants:
            key = cls._location_key(plant, names)
            counts[key] = counts.get(key, 0) + 1
        labels = [(key, names.get(key, "Без места"), count) for key, count in counts.items()]
        return sorted(labels, key=lambda item: (item[0] == "none", item[1].casefold(), item[0]))

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
            elif data == "g":
                self._user_data(context).clear()
                await self._show_locations(update)
            elif data == "t":
                await self.handle_time(update, context)
            elif data.startswith("l:"):
                self._user_data(context).clear()
                location_key, raw_page = data[2:].rsplit(":", 1)
                await self._show_location(update, location_key, int(raw_page))
            elif data.startswith("p:"):
                self._user_data(context).clear()
                plant_id, page = self._plant_and_page(data[2:])
                await self._show_plant(update, plant_id, page)
            elif data.startswith("e:"):
                kind, remainder = data[2:].split(":", 1)
                plant_id, page = self._plant_and_page(remainder)
                await self._prompt_plant(update, context, kind, plant_id, page)
            elif data.startswith("x:"):
                selected_plant_id, page = self._plant_and_page(data[2:])
                await self.watering.clear_schedule(self._request_id(update), selected_plant_id)
                await self._show_plant(update, selected_plant_id, page)
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
                await self._show_plant(update, plant_id, int(user_data.get("page", 0)))
            user_data.clear()
        except (ValueError, KeyError) as error:
            await self._reply(update, f"{error}\nПопробуйте снова или /cancel.")

    async def _show_plant(self, update: Update, plant_id: UUID, page: int = 0) -> None:
        plants = await self.garden.list_entities("plant")
        plant = next((item for item in plants if item["id"] == str(plant_id)), None)
        if plant is None:
            raise ValueError("Растение больше недоступно")
        locations = await self.garden.list_entities("location")
        names = {str(location["id"]): str(location["name"]) for location in locations}
        location_key = self._location_key(plant, names)
        schedule = await self.watering.get_schedule(plant_id)
        if schedule is None or not schedule["enabled"]:
            rows = [
                [
                    InlineKeyboardButton(
                        "Добавить расписание", callback_data=f"e:new:{plant_id}:{page}"
                    )
                ]
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
                [
                    InlineKeyboardButton(
                        "Изменить интервал", callback_data=f"e:cadence:{plant_id}:{page}"
                    )
                ],
                [
                    InlineKeyboardButton(
                        "Изменить дату начала", callback_data=f"e:anchor:{plant_id}:{page}"
                    )
                ],
                [InlineKeyboardButton("Отключить", callback_data=f"x:{plant_id}:{page}")],
            ]
        rows.append([InlineKeyboardButton("← К списку", callback_data=f"l:{location_key}:{page}")])
        await self._show_menu(
            update,
            f"{plant['name']}\n{description}",
            InlineKeyboardMarkup(rows),
        )

    async def _prompt_plant(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        kind: str,
        plant_id: UUID,
        page: int = 0,
    ) -> None:
        if kind not in {"new", "cadence", "anchor"}:
            raise ValueError("Неизвестное действие")
        user_data = self._user_data(context)
        user_data["edit"] = kind
        user_data["plant_id"] = str(plant_id)
        user_data["page"] = page
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
                if not self.menu_ready:
                    try:
                        await self._configure_menu(app)
                        self.menu_ready = True
                    except TelegramError as error:
                        LOGGER.warning("Menu setup will retry (%s)", type(error).__name__)
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

    @staticmethod
    async def _configure_menu(app: BotApplication) -> None:
        """Publish private-chat command hints and Telegram's native menu button."""
        commands = [
            BotCommand("plants", "Выбрать растение и настроить полив"),
            BotCommand("time", "Изменить общее время напоминаний"),
            BotCommand("help", "Показать возможности бота"),
            BotCommand("cancel", "Отменить ввод"),
        ]
        await app.bot.set_my_commands(commands, scope=BotCommandScopeAllPrivateChats())
        await app.bot.set_chat_menu_button(menu_button=MenuButtonCommands())

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
    def _plant_and_page(value: str) -> tuple[UUID, int]:
        parts = value.split(":")
        if len(parts) not in (1, 2):
            raise ValueError("Кнопка устарела")
        page = int(parts[1]) if len(parts) == 2 else 0
        if page < 0:
            raise ValueError("Страница недоступна")
        return UUID(parts[0]), page

    @staticmethod
    def _menu_keyboard() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("Растения", callback_data="g")],
                [InlineKeyboardButton("Общее время", callback_data="t")],
            ]
        )

    async def _show_menu(self, update: Update, text: str, markup: InlineKeyboardMarkup) -> None:
        query = update.callback_query
        if query:
            try:
                await query.edit_message_text(text=text, reply_markup=markup)
                return
            except BadRequest:
                pass
        await self._reply(update, text, markup)

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
