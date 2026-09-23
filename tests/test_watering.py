"""Exercise durable watering plans, queued deliveries, and stale button protection."""

from datetime import UTC, date, datetime, time, timedelta
from typing import Literal
from uuid import UUID, uuid4

import pytest

from her_garden.models import PlantState
from her_garden.store import GardenStore
from her_garden.watering import Adjustment, WateringStore, series_on_or_after


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


@pytest.mark.parametrize(
    ("amount", "mode", "unit", "expected_due", "expected_anchor"),
    [
        (1, "once", "hour", "2026-09-22T11:00:00+00:00", "2026-09-22"),
        (2, "once", "hour", "2026-09-22T12:00:00+00:00", "2026-09-22"),
        (4, "once", "hour", "2026-09-22T14:00:00+00:00", "2026-09-22"),
        (1, "once", "day", "2026-09-23T09:00:00+00:00", "2026-09-22"),
        (2, "once", "day", "2026-09-24T09:00:00+00:00", "2026-09-22"),
        (1, "series", "day", "2026-09-23T09:00:00+00:00", "2026-09-23"),
        (2, "series", "day", "2026-09-24T09:00:00+00:00", "2026-09-24"),
    ],
)
async def test_group_buttons_change_every_plant_once(
    store: GardenStore,
    amount: Literal[1, 2, 4],
    mode: Adjustment,
    unit: Literal["day", "hour"],
    expected_due: str,
    expected_anchor: str,
) -> None:
    """One message per recipient lists all due plants and its button changes them together."""
    plants = [
        await store.create_plant(uuid4(), PlantState(name=name))
        for name in ("Crassula", "Monstera")
    ]
    plant_ids = [UUID(plant["entity_id"]) for plant in plants]
    watering = WateringStore(store, "UTC")
    before = datetime(2026, 9, 22, 8, tzinfo=UTC)
    due = before + timedelta(hours=2)
    for plant_id in plant_ids:
        await watering.set_schedule(uuid4(), plant_id, date(2026, 9, 22), 3, before)
    assert await watering.enqueue_due(due, frozenset({"first_plant", "second_plant"})) == 0
    await watering.register_recipient("first_plant", 1001)
    await watering.register_recipient("second_plant", 1002)
    assert await watering.enqueue_due(due, frozenset({"first_plant", "second_plant"})) == 4
    jobs = await watering.pending_notifications()
    assert len(jobs) == 4
    assert len({job["cycle_id"] for job in jobs}) == 1
    first_job = next(job for job in jobs if job["username"] == "first_plant")
    second_job = next(job for job in jobs if job["username"] == "second_plant")
    await watering.mark_sent(first_job["id"], 77)
    await watering.mark_sent(second_job["id"], 78)
    assert await watering.pending_notifications() == []
    changed = await watering.apply_notification_action(
        first_job["id"],
        first_job["username"],
        first_job["chat_id"],
        amount,
        mode,
        unit,
        due,
    )
    assert len(changed) == 2
    assert {item["plant_id"] for item in changed} == {str(plant_id) for plant_id in plant_ids}
    assert all(item["next_due_at"] == expected_due for item in changed)
    assert all(item["anchor_date"] == expected_anchor for item in changed)
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
    """A common time change leaves an already-snoozed group intact."""
    first = await store.create_plant(uuid4(), PlantState(name="Crassula"))
    second = await store.create_plant(uuid4(), PlantState(name="Monstera"))
    first_id, second_id = UUID(first["entity_id"]), UUID(second["entity_id"])
    watering = WateringStore(store, "UTC")
    now = datetime(2026, 9, 22, 8, tzinfo=UTC)
    for plant_id in (first_id, second_id):
        await watering.set_schedule(uuid4(), plant_id, date(2026, 9, 23), 3, now)
    await watering.register_recipient("first_plant", 1001)
    due = datetime(2026, 9, 23, 10, tzinfo=UTC)
    await watering.enqueue_due(due, frozenset({"first_plant"}))
    job = (await watering.pending_notifications())[0]
    await watering.mark_sent(job["id"], 77)
    await watering.apply_notification_action(job["id"], "first_plant", 1001, 4, "once", "hour", due)
    await watering.set_reminder_time(uuid4(), time(11, 30), now)
    assert await watering.get_reminder_time() == "11:30"
    assert (await watering.get_schedule(first_id))["next_due_at"] == "2026-09-23T14:00:00+00:00"  # type: ignore[index]
    assert (await watering.get_schedule(second_id))["next_due_at"] == "2026-09-23T14:00:00+00:00"  # type: ignore[index]


async def test_group_button_rejects_stale_plant_atomically(store: GardenStore) -> None:
    """A changed plan invalidates the whole button before any group member is moved."""
    plants = [
        await store.create_plant(uuid4(), PlantState(name=name))
        for name in ("Crassula", "Monstera")
    ]
    first_id, second_id = (UUID(plant["entity_id"]) for plant in plants)
    watering = WateringStore(store, "UTC")
    before = datetime(2026, 9, 22, 8, tzinfo=UTC)
    for plant_id in (first_id, second_id):
        await watering.set_schedule(uuid4(), plant_id, date(2026, 9, 22), 3, before)
    await watering.register_recipient("first_plant", 1001)
    await watering.enqueue_due(before + timedelta(hours=2), frozenset({"first_plant"}))
    job = (await watering.pending_notifications())[0]
    await watering.mark_sent(job["id"], 77)
    original = await watering.get_schedule(first_id)
    await watering.clear_schedule(uuid4(), second_id)
    with pytest.raises(ValueError, match="No enabled watering schedule"):
        await watering.apply_notification_action(
            job["id"], "first_plant", 1001, 1, "once", "day", before
        )
    assert await watering.get_schedule(first_id) == original


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
