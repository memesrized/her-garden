"""PostgreSQL event storage with small, synchronous-in-transaction projections."""

from datetime import UTC, datetime
from importlib.resources import files
from typing import Any
from uuid import UUID, uuid4

from psycopg import AsyncConnection
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from her_garden.models import InventoryEvent, PlantEvent, PlantState

Record = dict[str, Any]


class GardenStore:
    """Store one household's events and current state atomically."""

    def __init__(self, database_url: str) -> None:
        self.database_url = database_url
        self.pool = AsyncConnectionPool[AsyncConnection[Record]](
            database_url,
            connection_class=AsyncConnection[Record],
            open=False,
            min_size=1,
            max_size=5,
            kwargs={"row_factory": dict_row},
        )

    async def open(self) -> None:
        """Connect and apply pending migrations under a database lock."""
        await self.pool.open(wait=True)
        async with self.pool.connection() as conn:
            await conn.execute("SELECT pg_advisory_xact_lock(731952)")
            await conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations (version text PRIMARY KEY)"
            )
            for path in sorted(
                files("her_garden").joinpath("migrations").iterdir(), key=lambda path: path.name
            ):
                if not path.name[0].isdigit() or not path.name.endswith(".sql"):
                    continue
                row = await (
                    await conn.execute(
                        "SELECT 1 FROM schema_migrations WHERE version = %s", (path.name,)
                    )
                ).fetchone()
                if row is None:
                    await conn.execute(path.read_text())
                    await conn.execute("INSERT INTO schema_migrations VALUES (%s)", (path.name,))

    async def close(self) -> None:
        """Release connections on shutdown."""
        await self.pool.close()

    async def create_location(self, request_id: UUID, name: str) -> Record:
        """Create a stable location, reusing a case-insensitive existing name."""
        return await self._write(request_id, None, "location", "created", {"name": name})

    async def create_plant(self, request_id: UUID, plant: PlantState) -> Record:
        """Create a plant with an immutable identity and initial event."""
        if not plant.name:
            raise ValueError("A plant name is required")
        payload = plant.model_dump(mode="json", exclude_unset=True)
        payload.setdefault("status", "active")
        payload.setdefault("aliases", [])
        return await self._write(request_id, None, "plant", "created", payload)

    async def append_plant_event(
        self, request_id: UUID, plant_id: UUID, event: PlantEvent
    ) -> Record:
        """Append a completed plant fact and rebuild its projection."""
        return await self._write(
            request_id,
            plant_id,
            "plant",
            event.event_type,
            event.model_dump(mode="json", exclude_unset=True),
            event.occurred_at,
            event.supersedes_event_id,
        )

    async def append_inventory_event(
        self, request_id: UUID, item_id: UUID | None, event: InventoryEvent
    ) -> Record:
        """Create or update supplies; new items require a name and category."""
        if item_id is None and (not event.name or not event.category):
            raise ValueError("New inventory needs name and category; reuse item_id afterward")
        if item_id is None and event.supersedes_event_id:
            raise ValueError("Corrections require the existing item_id")
        return await self._write(
            request_id,
            item_id,
            "inventory",
            event.event_type,
            event.model_dump(mode="json", exclude_unset=True),
            event.occurred_at,
            event.supersedes_event_id,
        )

    async def list_entities(
        self,
        kind: str,
        *,
        location_id: UUID | None = None,
        status: str | None = None,
        category: str | None = None,
        query: str | None = None,
    ) -> list[Record]:
        """Return compact current records with optional exact filters and text search."""
        async with self.pool.connection() as conn:
            rows = await (
                await conn.execute(
                    "SELECT id, state FROM entities WHERE kind = %s "
                    "AND (%s::text IS NULL OR state->>'location_id' = %s) "
                    "AND (%s::text IS NULL OR state->>'status' = %s) "
                    "AND (%s::text IS NULL OR state->>'category' = %s) "
                    "ORDER BY lower(state->>'name'), id",
                    (
                        kind,
                        str(location_id) if location_id else None,
                        str(location_id) if location_id else None,
                        status,
                        status,
                        category,
                        category,
                    ),
                )
            ).fetchall()
        result = []
        for row in rows:
            state = row["state"]
            if (
                query
                and query.casefold()
                not in " ".join(
                    [state.get("name", ""), state.get("species") or "", *state.get("aliases", [])]
                ).casefold()
            ):
                continue
            keys = (
                ("name", "species", "aliases", "location_id", "status")
                if kind == "plant"
                else ("name", "category", "remaining")
            )
            result.append({"id": str(row["id"]), **{k: state[k] for k in keys if k in state}})
        return result

    async def get_plant_context(self, plant_id: UUID, history_limit: int = 20) -> Record:
        """Return current state, recent history, and latest effective care actions."""
        async with self.pool.connection() as conn:
            # A single snapshot prevents state/history mismatch during concurrent writes.
            await conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            entity = await self._entity(conn, plant_id, "plant")
            events = await self._events(conn, plant_id)
        active = effective_events(events)
        latest: Record = {}
        for event in active:
            if event["event_type"] in {
                "watering",
                "fertilizing",
                "repotting",
                "moving",
                "pruning",
                "treatment",
                "propagation",
            }:
                latest[event["event_type"]] = public_event(event)
        return {
            "plant_id": str(plant_id),
            "state": entity["state"],
            "latest_actions": latest,
            "recent_events": [
                {**public_event(e), "is_effective": e in active}
                for e in reversed(events[-history_limit:])
            ],
            "total_events": len(events),
            "history_truncated": len(events) > history_limit,
        }

    async def _write(
        self,
        request_id: UUID,
        entity_id: UUID | None,
        kind: str,
        event_type: str,
        payload: Record,
        occurred_at: datetime | None = None,
        supersedes: UUID | None = None,
    ) -> Record:
        request = {
            "entity_id": str(entity_id) if entity_id else None,
            "kind": kind,
            "event_type": event_type,
            "payload": payload,
        }
        async with self.pool.connection() as conn:
            # One household: serialize writes rather than introduce queues or retry machinery.
            await conn.execute("SELECT pg_advisory_xact_lock(731953)")
            existing = await (
                await conn.execute("SELECT * FROM events WHERE request_id = %s", (request_id,))
            ).fetchone()
            if existing:
                if existing["request"] != request:
                    raise ValueError("request_id was already used with different input")
                return {"entity_id": str(existing["entity_id"]), "event_id": str(existing["id"])}
            if entity_id is None and kind == "location":
                existing_location = await (
                    await conn.execute(
                        "SELECT id FROM entities WHERE kind = 'location' "
                        "AND lower(state->>'name') = lower(%s)",
                        (payload["name"],),
                    )
                ).fetchone()
                if existing_location:
                    entity_id = existing_location["id"]
            if entity_id:
                await self._entity(conn, entity_id, kind)
            else:
                entity_id = uuid4()
                await conn.execute(
                    "INSERT INTO entities(id, kind) VALUES (%s, %s)", (entity_id, kind)
                )
            location = payload.get("changes", payload).get("location_id")
            if location:
                await self._entity(conn, UUID(location), "location")
            events = await self._events(conn, entity_id)
            if supersedes:
                target = next((e for e in effective_events(events) if e["id"] == supersedes), None)
                if target is None or target["event_type"] == "created":
                    raise ValueError(
                        "Correction must replace an active non-creation event on this entity"
                    )
            event_id = uuid4()
            await conn.execute(
                "INSERT INTO events(id, entity_id, request_id, request, event_type, "
                "occurred_at, payload, supersedes_event_id) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    event_id,
                    entity_id,
                    request_id,
                    Jsonb(request),
                    event_type,
                    occurred_at or datetime.now(UTC),
                    Jsonb(payload),
                    supersedes,
                ),
            )
            events = await self._events(conn, entity_id)
            state = project(kind, events)
            if kind == "inventory" and not state.get("name"):
                raise ValueError(
                    "Correction would remove the inventory identity; include name/category"
                )
            await conn.execute(
                "UPDATE entities SET state = %s WHERE id = %s", (Jsonb(state), entity_id)
            )
        return {"entity_id": str(entity_id), "event_id": str(event_id)}

    @staticmethod
    async def _entity(conn: AsyncConnection[Record], entity_id: UUID, kind: str) -> Record:
        row = await (
            await conn.execute(
                "SELECT * FROM entities WHERE id = %s AND kind = %s", (entity_id, kind)
            )
        ).fetchone()
        if row is None:
            raise ValueError(f"Unknown {kind} ID")
        return row

    @staticmethod
    async def _events(conn: AsyncConnection[Record], entity_id: UUID) -> list[Record]:
        return await (
            await conn.execute(
                "SELECT * FROM events WHERE entity_id = %s ORDER BY occurred_at, sequence",
                (entity_id,),
            )
        ).fetchall()


def effective_events(events: list[Record]) -> list[Record]:
    """Exclude superseded facts while preserving corrections as effective replacements."""
    superseded = {e["supersedes_event_id"] for e in events if e["supersedes_event_id"]}
    return [e for e in events if e["id"] not in superseded and e["event_type"] != "void"]


def project(kind: str, events: list[Record]) -> Record:
    """Replay effective facts in occurrence order; creation is always the initial state."""
    state: Record = {}
    active = effective_events(events)
    for event in sorted(
        active, key=lambda e: (e["event_type"] != "created", e["occurred_at"], e["sequence"])
    ):
        payload = event["payload"]
        if kind == "plant":
            state.update(
                payload if event["event_type"] == "created" else payload.get("changes", {})
            )
        elif kind == "inventory":
            for key in ("name", "category"):
                if payload.get(key) is not None:
                    state[key] = payload[key]
            if event["event_type"] in {"purchase", "usage"}:
                state["remaining"] = payload.get("remaining")
            elif "remaining" in payload:
                state["remaining"] = payload["remaining"]
        else:
            state.update(payload)
    return state


def public_event(event: Record) -> Record:
    """Expose historical facts without internal idempotency requests."""
    return {
        "event_id": str(event["id"]),
        "event_type": event["event_type"],
        "occurred_at": event["occurred_at"].isoformat(),
        "recorded_at": event["recorded_at"].isoformat(),
        "details": event["payload"],
        "supersedes_event_id": str(event["supersedes_event_id"])
        if event["supersedes_event_id"]
        else None,
    }
