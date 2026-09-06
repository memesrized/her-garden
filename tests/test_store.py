"""Verify PostgreSQL transactions, correction replay and model-facing semantics."""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import psycopg
import pytest
from pydantic import ValidationError

from her_garden.models import InventoryEvent, PlantEvent, PlantState
from her_garden.store import GardenStore


async def test_retry_and_concurrent_creation(store: GardenStore) -> None:
    request_id = uuid4()
    plant = PlantState(name="Crassula", aliases=["jade"])
    first, second = await asyncio.gather(
        store.create_plant(request_id, plant), store.create_plant(request_id, plant)
    )
    assert first == second
    assert len(await store.list_entities("plant")) == 1
    with pytest.raises(ValueError, match="different input"):
        await store.create_plant(request_id, PlantState(name="Other"))
    assert len(await store.list_entities("plant", query="JADE")) == 1


async def test_backdated_corrections_and_latest_actions(store: GardenStore) -> None:
    plant = await store.create_plant(uuid4(), PlantState(name="Crassula"))
    plant_id = UUID(plant["entity_id"])
    now = datetime.now(UTC)
    recent = await store.append_plant_event(
        uuid4(),
        plant_id,
        PlantEvent(
            event_type="watering",
            occurred_at=now - timedelta(days=1),
            note="Watered thoroughly",
        ),
    )
    older = await store.append_plant_event(
        uuid4(),
        plant_id,
        PlantEvent(
            event_type="watering",
            occurred_at=now - timedelta(days=7),
            note="Earlier watering",
        ),
    )
    context = await store.get_plant_context(plant_id)
    assert context["latest_actions"]["watering"]["event_id"] == recent["event_id"]
    await store.append_plant_event(
        uuid4(),
        plant_id,
        PlantEvent(
            event_type="void",
            occurred_at=now,
            note="Actually watered the other plant",
            supersedes_event_id=UUID(recent["event_id"]),
        ),
    )
    context = await store.get_plant_context(plant_id, history_limit=1)
    assert context["latest_actions"]["watering"]["event_id"] == older["event_id"]
    assert context["total_events"] == 4
    assert context["history_truncated"] is True
    async with store.pool.connection() as conn:
        with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
            await conn.execute("DELETE FROM events")


async def test_location_and_patch_projection(store: GardenStore) -> None:
    location = await store.create_location(uuid4(), "North balcony")
    same = await store.create_location(uuid4(), "north balcony")
    assert location["entity_id"] == same["entity_id"]
    plant = await store.create_plant(uuid4(), PlantState(name="Crassula", species="Crassula ovata"))
    plant_id = UUID(plant["entity_id"])
    now = datetime.now(UTC)
    await store.append_plant_event(
        uuid4(),
        plant_id,
        PlantEvent(
            event_type="moving",
            occurred_at=now,
            note="Moved to balcony",
            changes=PlantState(location_id=UUID(location["entity_id"])),
        ),
    )
    await store.append_plant_event(
        uuid4(),
        plant_id,
        PlantEvent(
            event_type="update",
            occurred_at=now - timedelta(days=2),
            note="Earlier name",
            changes=PlantState(name="Jade", species=None),
        ),
    )
    context = await store.get_plant_context(plant_id)
    assert context["state"]["name"] == "Jade"
    assert context["state"]["species"] is None
    assert len(await store.list_entities("plant", location_id=UUID(location["entity_id"]))) == 1
    with pytest.raises(ValueError, match="Unknown location"):
        await store.append_plant_event(
            uuid4(),
            plant_id,
            PlantEvent(
                event_type="moving",
                occurred_at=now,
                note="Bad ID",
                changes=PlantState(location_id=uuid4()),
            ),
        )
    assert (await store.get_plant_context(plant_id))["total_events"] == 3


async def test_cross_plant_correction_rejected(store: GardenStore) -> None:
    a = await store.create_plant(uuid4(), PlantState(name="A"))
    b = await store.create_plant(uuid4(), PlantState(name="B"))
    event = await store.append_plant_event(
        uuid4(),
        UUID(a["entity_id"]),
        PlantEvent(
            event_type="observation",
            occurred_at=datetime.now(UTC),
            note="Yellow lower leaf",
        ),
    )
    with pytest.raises(ValueError, match="Correction"):
        await store.append_plant_event(
            uuid4(),
            UUID(b["entity_id"]),
            PlantEvent(
                event_type="void",
                occurred_at=datetime.now(UTC),
                note="Wrong plant",
                supersedes_event_id=UUID(event["event_id"]),
            ),
        )


async def test_fuzzy_inventory_never_invents_arithmetic(store: GardenStore) -> None:
    item = await store.append_inventory_event(
        uuid4(),
        None,
        InventoryEvent(
            event_type="purchase",
            occurred_at=datetime.now(UTC),
            name="Perlite",
            category="substrate",
            remaining="one bag",
            note="Bought a bag",
        ),
    )
    await store.append_inventory_event(
        uuid4(),
        UUID(item["entity_id"]),
        InventoryEvent(
            event_type="usage",
            occurred_at=datetime.now(UTC),
            note="Used some while repotting",
        ),
    )
    assert (await store.list_entities("inventory"))[0]["remaining"] is None
    await store.append_inventory_event(
        uuid4(),
        UUID(item["entity_id"]),
        InventoryEvent(
            event_type="observation",
            occurred_at=datetime.now(UTC),
            remaining="about half a bag",
            note="Checked the cupboard",
        ),
    )
    assert (await store.list_entities("inventory"))[0]["remaining"] == "about half a bag"


@pytest.mark.parametrize(
    "data",
    [
        {"event_type": "watering", "occurred_at": "2100-01-01T12:00:00Z", "note": "Plan"},
        {"event_type": "watering", "occurred_at": "2026-01-01T12:00:00", "note": "No timezone"},
        {"event_type": "moving", "occurred_at": "2026-01-01T12:00:00Z", "note": "Missing location"},
        {
            "event_type": "observation",
            "occurred_at": "2026-01-01T12:00:00Z",
            "note": "Leaves",
            "changes": {"status": "dead"},
        },
    ],
)
def test_invalid_facts_rejected(data: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        PlantEvent.model_validate(data)
