"""Durable per-plant watering plans and Telegram delivery state.

Receives plant IDs, calendar dates, and authorized chat registrations; returns schedules and
notification jobs. Plans are separate from completed care events, so reminders never claim
that a plant was watered. One database transaction owns each schedule transition.
"""

from datetime import UTC, date, datetime, time, timedelta
from typing import Literal, cast
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from psycopg import AsyncConnection

from her_garden.store import GardenStore, Record

Adjustment = Literal["once", "series"]


def series_on_or_after(anchor_date: date, cadence_days: int, target: date) -> date:
    """Find the first date in an anchored series on or after a target date."""
    if target <= anchor_date:
        return anchor_date
    elapsed = (target - anchor_date).days
    steps = (elapsed + cadence_days - 1) // cadence_days
    return anchor_date + timedelta(days=steps * cadence_days)


def series_after(anchor_date: date, cadence_days: int, target: date) -> date:
    """Find the first date in an anchored series strictly after a target date."""
    return series_on_or_after(anchor_date, cadence_days, target + timedelta(days=1))


def scheduled_at(day: date, reminder_time: time, timezone: ZoneInfo) -> datetime:
    """Convert a plant's local calendar reminder to a timezone-aware instant."""
    return datetime.combine(day, reminder_time, tzinfo=timezone).astimezone(UTC)


class WateringStore:
    """Share GardenStore's PostgreSQL pool between MCP writes and an optional bot."""

    def __init__(self, garden: GardenStore, timezone_name: str) -> None:
        self.garden = garden
        self.timezone = ZoneInfo(timezone_name)

    async def get_reminder_time(self) -> str:
        """Read the household-wide local clock time for ordinary reminders."""
        async with self.garden.pool.connection() as conn:
            row = await (
                await conn.execute("SELECT reminder_time FROM watering_settings WHERE singleton")
            ).fetchone()
        assert row is not None
        return cast(time, row["reminder_time"]).isoformat(timespec="minutes")

    async def set_reminder_time(
        self, request_id: UUID, reminder_time: time, now: datetime
    ) -> Record:
        """Change the common clock time while preserving temporary snoozes."""
        request = {
            "action": "set_time",
            "reminder_time": reminder_time.isoformat(timespec="minutes"),
        }
        async with self.garden.pool.connection() as conn:
            await self._lock(conn)
            previous = await self._previous(conn, request_id, request)
            if previous is not None:
                return previous
            rows = await (
                await conn.execute("SELECT * FROM watering_schedules FOR UPDATE")
            ).fetchall()
            await conn.execute(
                "UPDATE watering_settings SET reminder_time = %s WHERE singleton", (reminder_time,)
            )
            for row in rows:
                next_due = row["next_due_at"]
                if not row["manual_override"]:
                    due_day = next_due.astimezone(self.timezone).date()
                    next_due = scheduled_at(due_day, reminder_time, self.timezone)
                    if next_due < now:
                        next_due = scheduled_at(
                            series_after(
                                row["anchor_date"],
                                row["cadence_days"],
                                now.astimezone(self.timezone).date(),
                            ),
                            reminder_time,
                            self.timezone,
                        )
                await conn.execute(
                    "UPDATE watering_schedules SET reminder_time = %s, next_due_at = %s, "
                    "updated_at = now() WHERE plant_id = %s",
                    (reminder_time, next_due, row["plant_id"]),
                )
            result = {"reminder_time": reminder_time.isoformat(timespec="minutes")}
            await self._remember(conn, request_id, request, result)
            return result

    async def get_schedule(self, plant_id: UUID) -> Record | None:
        """Read one plant's plan, including a disabled plan when present."""
        async with self.garden.pool.connection() as conn:
            await self._plant(conn, plant_id)
            row = await (
                await conn.execute(
                    "SELECT * FROM watering_schedules WHERE plant_id = %s", (plant_id,)
                )
            ).fetchone()
        return self._public_schedule(row) if row else None

    async def list_schedules(self) -> list[Record]:
        """List enabled plans with plant names for MCP or Telegram selection."""
        async with self.garden.pool.connection() as conn:
            rows = await (
                await conn.execute(
                    "SELECT s.*, e.state->>'name' AS plant_name FROM watering_schedules s "
                    "JOIN entities e ON e.id = s.plant_id WHERE s.enabled "
                    "AND e.state->>'archived' IS DISTINCT FROM 'true' "
                    "ORDER BY lower(e.state->>'name'), s.plant_id"
                )
            ).fetchall()
        return [self._public_schedule(row) for row in rows]

    async def set_schedule(
        self,
        request_id: UUID,
        plant_id: UUID,
        anchor_date: date,
        cadence_days: int,
        now: datetime,
    ) -> Record:
        """Create or replace a plant's anchored series with retry-safe semantics."""
        if not 1 <= cadence_days <= 3650:
            raise ValueError("cadence_days must be between 1 and 3650")
        request = {
            "action": "set",
            "plant_id": str(plant_id),
            "anchor_date": anchor_date.isoformat(),
            "cadence_days": cadence_days,
        }
        async with self.garden.pool.connection() as conn:
            await self._lock(conn)
            previous = await self._previous(conn, request_id, request)
            if previous is not None:
                return previous
            await self._active_plant(conn, plant_id)
            setting = await (
                await conn.execute("SELECT reminder_time FROM watering_settings WHERE singleton")
            ).fetchone()
            assert setting is not None
            reminder_time = setting["reminder_time"]
            today = now.astimezone(self.timezone).date()
            next_day = series_on_or_after(anchor_date, cadence_days, today)
            next_due = scheduled_at(next_day, reminder_time, self.timezone)
            if next_due < now:
                next_due = scheduled_at(
                    series_after(anchor_date, cadence_days, today), reminder_time, self.timezone
                )
            row = await (
                await conn.execute(
                    "INSERT INTO watering_schedules "
                    "(plant_id, anchor_date, cadence_days, reminder_time, next_due_at) "
                    "VALUES (%s, %s, %s, %s, %s) "
                    "ON CONFLICT (plant_id) DO UPDATE SET "
                    "anchor_date = EXCLUDED.anchor_date, cadence_days = EXCLUDED.cadence_days, "
                    "reminder_time = EXCLUDED.reminder_time, next_due_at = EXCLUDED.next_due_at, "
                    "enabled = true, manual_override = false, "
                    "active_cycle_id = NULL, active_due_at = NULL, "
                    "updated_at = now() "
                    "RETURNING *",
                    (plant_id, anchor_date, cadence_days, reminder_time, next_due),
                )
            ).fetchone()
            assert row is not None
            result = self._public_schedule(row)
            await self._remember(conn, request_id, request, result)
            return result

    async def clear_schedule(self, request_id: UUID, plant_id: UUID) -> Record:
        """Disable reminders without deleting the old plan or its delivery records."""
        request = {"action": "clear", "plant_id": str(plant_id)}
        async with self.garden.pool.connection() as conn:
            await self._lock(conn)
            previous = await self._previous(conn, request_id, request)
            if previous is not None:
                return previous
            await self._plant(conn, plant_id)
            row = await (
                await conn.execute(
                    "UPDATE watering_schedules SET enabled = false, active_cycle_id = NULL, "
                    "active_due_at = NULL, updated_at = now() WHERE plant_id = %s RETURNING *",
                    (plant_id,),
                )
            ).fetchone()
            if row is None:
                raise ValueError("No watering schedule exists for this plant")
            result = self._public_schedule(row)
            await self._remember(conn, request_id, request, result)
            return result

    async def adjust_schedule(
        self,
        request_id: UUID,
        plant_id: UUID,
        days: int,
        mode: Adjustment,
        now: datetime,
        unit: Literal["day", "hour"] = "day",
    ) -> Record:
        """Postpone the next reminder or move the entire anchored series."""
        if not 1 <= days <= 30:
            raise ValueError("amount must be between 1 and 30")
        if unit == "hour" and mode != "once":
            raise ValueError("Hour adjustments cannot shift the series")
        request = {
            "action": "adjust",
            "plant_id": str(plant_id),
            "amount": days,
            "mode": mode,
            "unit": unit,
        }
        async with self.garden.pool.connection() as conn:
            await self._lock(conn)
            previous = await self._previous(conn, request_id, request)
            if previous is not None:
                return previous
            await self._active_plant(conn, plant_id)
            row = await self._schedule_for_update(conn, plant_id)
            result = await self._apply_adjustment(conn, row, days, mode, now, unit)
            await self._remember(conn, request_id, request, result)
            return result

    async def register_recipient(self, username: str, chat_id: int) -> None:
        """Remember a private chat for an already authorized username."""
        async with self.garden.pool.connection() as conn:
            await conn.execute(
                "INSERT INTO telegram_recipients (username, chat_id) VALUES (%s, %s) "
                "ON CONFLICT (username) DO UPDATE SET chat_id = EXCLUDED.chat_id, "
                "registered_at = now()",
                (username, chat_id),
            )

    async def enqueue_due(self, now: datetime, allowed_usernames: frozenset[str]) -> int:
        """Persist due jobs and advance plans only when a recipient has enrolled."""
        async with self.garden.pool.connection() as conn:
            await self._lock(conn)
            recipients = await (
                await conn.execute("SELECT username, chat_id FROM telegram_recipients")
            ).fetchall()
            recipients = [row for row in recipients if row["username"] in allowed_usernames]
            if not recipients:
                return 0
            schedules = await (
                await conn.execute(
                    "SELECT s.* FROM watering_schedules s JOIN entities e ON e.id = s.plant_id "
                    "WHERE s.enabled AND s.next_due_at <= %s "
                    "AND e.state->>'archived' IS DISTINCT FROM 'true' "
                    "AND e.state->>'status' NOT IN ('dead', 'given_away') "
                    "ORDER BY s.next_due_at, s.plant_id LIMIT 100 FOR UPDATE OF s",
                    (now,),
                )
            ).fetchall()
            for schedule in schedules:
                cycle_id = uuid4()
                for recipient in recipients:
                    await conn.execute(
                        "INSERT INTO watering_notifications "
                        "(id, plant_id, username, chat_id, cycle_id, due_at) "
                        "VALUES (%s, %s, %s, %s, %s, %s)",
                        (
                            uuid4(),
                            schedule["plant_id"],
                            recipient["username"],
                            recipient["chat_id"],
                            cycle_id,
                            schedule["next_due_at"],
                        ),
                    )
                await conn.execute(
                    "UPDATE watering_schedules SET next_due_at = %s, manual_override = false, "
                    "active_cycle_id = %s, active_due_at = %s, updated_at = now() "
                    "WHERE plant_id = %s",
                    (
                        scheduled_at(
                            series_after(
                                schedule["anchor_date"],
                                schedule["cadence_days"],
                                now.astimezone(self.timezone).date(),
                            ),
                            schedule["reminder_time"],
                            self.timezone,
                        ),
                        cycle_id,
                        schedule["next_due_at"],
                        schedule["plant_id"],
                    ),
                )
            return len(schedules) * len(recipients)

    async def pending_notifications(self) -> list[Record]:
        """Return outstanding jobs and enough state to reject stale deliveries."""
        async with self.garden.pool.connection() as conn:
            rows = await (
                await conn.execute(
                    "SELECT n.*, s.active_cycle_id, s.enabled, e.state->>'name' AS plant_name, "
                    "e.state->>'archived' AS archived, e.state->>'status' AS plant_status "
                    "FROM watering_notifications n "
                    "JOIN watering_schedules s ON s.plant_id = n.plant_id "
                    "JOIN entities e ON e.id = n.plant_id "
                    "WHERE n.status = 'pending' ORDER BY n.due_at, n.id LIMIT 200"
                )
            ).fetchall()
        return rows

    async def mark_sent(self, notification_id: UUID, message_id: int) -> None:
        """Record a successful Telegram send for later button validation."""
        async with self.garden.pool.connection() as conn:
            await conn.execute(
                "UPDATE watering_notifications SET status = 'sent', sent_at = now(), "
                "telegram_message_id = %s WHERE id = %s AND status = 'pending'",
                (message_id, notification_id),
            )

    async def cancel_notification(self, notification_id: UUID) -> None:
        """Discard a pending delivery that is no longer authorized or current."""
        async with self.garden.pool.connection() as conn:
            await conn.execute(
                "UPDATE watering_notifications SET status = 'cancelled' "
                "WHERE id = %s AND status = 'pending'",
                (notification_id,),
            )

    async def apply_notification_action(
        self,
        notification_id: UUID,
        username: str,
        chat_id: int,
        amount: Literal[1, 2, 4],
        mode: Adjustment,
        unit: Literal["day", "hour"],
        now: datetime,
    ) -> Record:
        """Accept only one action for the current sent reminder cycle."""
        async with self.garden.pool.connection() as conn:
            await self._lock(conn)
            notification = await (
                await conn.execute(
                    "SELECT * FROM watering_notifications WHERE id = %s", (notification_id,)
                )
            ).fetchone()
            if (
                notification is None
                or notification["status"] != "sent"
                or notification["username"] != username
                or notification["chat_id"] != chat_id
            ):
                raise ValueError("This reminder is unavailable")
            await self._active_plant(conn, notification["plant_id"])
            schedule = await self._schedule_for_update(conn, notification["plant_id"])
            if schedule["active_cycle_id"] != notification["cycle_id"]:
                raise ValueError("This reminder has already been changed")
            result = await self._apply_adjustment(conn, schedule, amount, mode, now, unit)
            await conn.execute(
                "UPDATE watering_notifications SET status = 'cancelled' "
                "WHERE cycle_id = %s AND status = 'pending'",
                (notification["cycle_id"],),
            )
            return result

    async def _apply_adjustment(
        self,
        conn: AsyncConnection[Record],
        schedule: Record,
        amount: int,
        mode: Adjustment,
        now: datetime,
        unit: Literal["day", "hour"] = "day",
    ) -> Record:
        """Apply one calendar adjustment while keeping completed events untouched."""
        today = now.astimezone(self.timezone).date()
        if unit == "hour":
            if mode != "once":
                raise ValueError("Hour adjustments cannot shift the series")
            base = now if schedule["active_due_at"] else max(now, schedule["next_due_at"])
            next_due = base + timedelta(hours=amount)
            anchor = schedule["anchor_date"]
        elif mode == "once":
            base = schedule["active_due_at"] or schedule["next_due_at"]
            due_day = max(base.astimezone(self.timezone).date(), today)
            next_due = scheduled_at(
                due_day + timedelta(days=amount), schedule["reminder_time"], self.timezone
            )
            anchor = schedule["anchor_date"]
        else:
            anchor = schedule["anchor_date"] + timedelta(days=amount)
            base = schedule["active_due_at"] or schedule["next_due_at"]
            due_day = base.astimezone(self.timezone).date() + timedelta(days=amount)
            series_day = series_on_or_after(anchor, schedule["cadence_days"], due_day)
            next_due = scheduled_at(series_day, schedule["reminder_time"], self.timezone)
            if next_due <= now:
                next_due = scheduled_at(
                    series_after(anchor, schedule["cadence_days"], today),
                    schedule["reminder_time"],
                    self.timezone,
                )
        row = await (
            await conn.execute(
                "UPDATE watering_schedules SET anchor_date = %s, next_due_at = %s, "
                "manual_override = %s, active_cycle_id = NULL, active_due_at = NULL, "
                "updated_at = now() "
                "WHERE plant_id = %s RETURNING *",
                (anchor, next_due, mode == "once", schedule["plant_id"]),
            )
        ).fetchone()
        assert row is not None
        return self._public_schedule(row)

    @staticmethod
    async def _lock(conn: AsyncConnection[Record]) -> None:
        await conn.execute("SELECT pg_advisory_xact_lock(731953)")

    @staticmethod
    async def _plant(conn: AsyncConnection[Record], plant_id: UUID) -> Record:
        row = await (
            await conn.execute(
                "SELECT state FROM entities WHERE id = %s AND kind = 'plant'", (plant_id,)
            )
        ).fetchone()
        if row is None:
            raise ValueError("Unknown plant")
        return cast(Record, row["state"])

    @classmethod
    async def _active_plant(cls, conn: AsyncConnection[Record], plant_id: UUID) -> Record:
        state = await cls._plant(conn, plant_id)
        if state.get("archived") or state.get("status") in {"dead", "given_away"}:
            raise ValueError("Watering reminders require an active plant")
        return state

    @staticmethod
    async def _schedule_for_update(conn: AsyncConnection[Record], plant_id: UUID) -> Record:
        row = await (
            await conn.execute(
                "SELECT * FROM watering_schedules WHERE plant_id = %s FOR UPDATE", (plant_id,)
            )
        ).fetchone()
        if row is None or not row["enabled"]:
            raise ValueError("No enabled watering schedule exists for this plant")
        return row

    @staticmethod
    async def _previous(
        conn: AsyncConnection[Record], request_id: UUID, request: Record
    ) -> Record | None:
        row = await (
            await conn.execute(
                "SELECT request, result FROM watering_requests WHERE request_id = %s", (request_id,)
            )
        ).fetchone()
        if row is None:
            return None
        if row["request"] != request:
            raise ValueError("request_id was already used with different input")
        return cast(Record, row["result"])

    @staticmethod
    async def _remember(
        conn: AsyncConnection[Record], request_id: UUID, request: Record, result: Record
    ) -> None:
        from psycopg.types.json import Jsonb

        await conn.execute(
            "INSERT INTO watering_requests (request_id, request, result) VALUES (%s, %s, %s)",
            (request_id, Jsonb(request), Jsonb(result)),
        )

    @staticmethod
    def _public_schedule(row: Record) -> Record:
        result: Record = {
            "plant_id": str(row["plant_id"]),
            "anchor_date": row["anchor_date"].isoformat(),
            "cadence_days": row["cadence_days"],
            "reminder_time": row["reminder_time"].isoformat(timespec="minutes"),
            "next_due_at": row["next_due_at"].isoformat(),
            "enabled": row["enabled"],
        }
        if row.get("plant_name") is not None:
            result["plant_name"] = row["plant_name"]
        return result
