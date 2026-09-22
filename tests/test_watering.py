"""Exercise durable watering plans, queued deliveries, and stale button protection."""

from datetime import UTC, date, datetime, time, timedelta
from uuid import UUID, uuid4

import pytest

from her_garden.models import PlantState
from her_garden.store import GardenStore
from her_garden.watering import WateringStore, series_on_or_after


async def test_anchored_schedule_and_retry(store: GardenStore) -> None:
    """Changing a single reminder must not move the anchored series."""
    plant = await store.create_plant(uuid4(), PlantState(name="Crassula"))
    plant_id = UUID(plant["entity_id"])
    watering = WateringStore(store, "UTC")
    now = datetime(2026, 9, 22, 8, tzinfo=UTC)
    request_id = uuid4()
    first = await watering.set_schedule(request_id, plant_id, date(2026, 9, 21), 3, now)
    assert first["next_due_at"] == "2026-09-24T09:00:00+00:00"
    assert await watering.set_schedule(request_id, plant_id, date(2026, 9, 21), 3, now) == first
    with pytest.raises(ValueError, match="different input"):
        await watering.set_schedule(request_id, plant_id, date(2026, 9, 21), 4, now)

    postponed = await watering.adjust_schedule(uuid4(), plant_id, 2, "once", now)
    assert postponed["anchor_date"] == "2026-09-21"
    assert postponed["next_due_at"] == "2026-09-26T09:00:00+00:00"
    shifted = await watering.adjust_schedule(uuid4(), plant_id, 1, "series", now)
    assert shifted["anchor_date"] == "2026-09-22"
    assert shifted["next_due_at"] == "2026-09-28T09:00:00+00:00"
    assert series_on_or_after(date(2026, 9, 22), 3, date(2026, 9, 28)) == date(2026, 9, 28)


async def test_two_recipients_and_single_use_buttons(store: GardenStore) -> None:
    """Both enrolled users receive a job, while one action invalidates the shared cycle."""
    plant = await store.create_plant(uuid4(), PlantState(name="Crassula"))
    plant_id = UUID(plant["entity_id"])
    watering = WateringStore(store, "UTC")
    before = datetime(2026, 9, 22, 8, tzinfo=UTC)
    due = before + timedelta(hours=2)
    await watering.set_schedule(uuid4(), plant_id, date(2026, 9, 22), 3, before)
    assert await watering.enqueue_due(due, frozenset({"first_plant", "second_plant"})) == 0
    await watering.register_recipient("first_plant", 1001)
    await watering.register_recipient("second_plant", 1002)
    assert await watering.enqueue_due(due, frozenset({"first_plant", "second_plant"})) == 2
    jobs = await watering.pending_notifications()
    assert len(jobs) == 2
    for job in jobs:
        await watering.mark_sent(job["id"], 77)
    first_job = jobs[0]
    second_job = jobs[1]
    changed = await watering.apply_notification_action(
        first_job["id"],
        first_job["username"],
        first_job["chat_id"],
        1,
        "once",
        "hour",
        due,
    )
    assert changed["next_due_at"] == "2026-09-22T11:00:00+00:00"
    assert changed["anchor_date"] == "2026-09-22"
    with pytest.raises(ValueError, match="already been changed"):
        await watering.apply_notification_action(
            second_job["id"],
            second_job["username"],
            second_job["chat_id"],
            2,
            "series",
            "day",
            due,
        )
    with pytest.raises(ValueError, match="unavailable"):
        await watering.apply_notification_action(
            first_job["id"], "other_user", first_job["chat_id"], 1, "once", "day", due
        )


async def test_common_time_preserves_hour_snooze(store: GardenStore) -> None:
    """A time change affects ordinary dates but leaves a one-off hour delay intact."""
    first = await store.create_plant(uuid4(), PlantState(name="Crassula"))
    second = await store.create_plant(uuid4(), PlantState(name="Monstera"))
    first_id, second_id = UUID(first["entity_id"]), UUID(second["entity_id"])
    watering = WateringStore(store, "UTC")
    now = datetime(2026, 9, 22, 8, tzinfo=UTC)
    for plant_id in (first_id, second_id):
        await watering.set_schedule(uuid4(), plant_id, date(2026, 9, 23), 3, now)
    await watering.adjust_schedule(uuid4(), first_id, 4, "once", now, "hour")
    await watering.set_reminder_time(uuid4(), time(11, 30), now)
    assert await watering.get_reminder_time() == "11:30"
    assert (await watering.get_schedule(first_id))["next_due_at"] == "2026-09-23T13:00:00+00:00"  # type: ignore[index]
    assert (await watering.get_schedule(second_id))["next_due_at"] == "2026-09-23T11:30:00+00:00"  # type: ignore[index]


async def test_disabled_plants_do_not_queue(store: GardenStore) -> None:
    """Disabling a plan preserves it but suppresses outbound jobs."""
    plant = await store.create_plant(uuid4(), PlantState(name="Crassula"))
    plant_id = UUID(plant["entity_id"])
    watering = WateringStore(store, "UTC")
    before = datetime(2026, 9, 22, 8, tzinfo=UTC)
    await watering.set_schedule(uuid4(), plant_id, date(2026, 9, 22), 1, before)
    await watering.register_recipient("first_plant", 1001)
    await watering.clear_schedule(uuid4(), plant_id)
    assert await watering.enqueue_due(before + timedelta(hours=2), frozenset({"first_plant"})) == 0
