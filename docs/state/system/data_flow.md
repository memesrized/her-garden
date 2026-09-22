# Data flow

```mermaid
flowchart TD
    config["Private environment configuration"] --> auth["OAuth and household consent"]
    config --> store["GardenStore"]
    config --> bot["Optional Telegram polling process"]
    chatgpt["ChatGPT tool request"] --> tls["Existing HTTPS proxy"]
    tls --> auth
    auth --> tools["FastMCP and typed payload validation"]
    tools --> store
    store --> transaction["PostgreSQL transaction and household write lock"]
    transaction --> events["Append-only events with request UUIDs"]
    events --> projection["Replay one entity's effective facts and lifecycle events"]
    projection --> state["Current entity state as JSONB"]
    state --> results["Compact indexes or detailed plant context"]
    events --> results
    results --> chatgpt
    tools --> plans["WateringStore schedule operations"]
    bot --> plans
    plans --> schedule[("Watering schedules and common clock time")]
    bot --> jobs["Due reminder scan and delivery jobs"]
    jobs --> schedule
    jobs --> delivery[("Recipients and notification state")]
    delivery --> bot
    bot --> telegram["Private Telegram chats"]
```

`server.py` registers the plant-memory and watering tools plus OAuth routes. `models.py`
validates reported facts and lifecycle changes. `watering.py` owns anchored schedules, the
common clock time and durable notification transitions. The optional `bot.py` process checks
private-chat usernames, handles plant selection and buttons, and polls pending notifications.
Both processes use the same database; MCP never requires bot credentials.
`store.py` uses a five-connection async pool. One transaction checks retry identity, validates
references, inserts an event, replays that entity and updates its projection. A single
transaction advisory lock serializes household writes. Reads do not acquire that lock.
Detailed context uses a repeatable-read snapshot to keep state and history consistent.

Entities have immutable UUIDs and a kind: plant, location or inventory. Event ordering is
`occurred_at, sequence`; plant/location creation supplies baseline state before later patches,
even when a reported event predates registration. Correction chains suppress all superseded
records. Recent history includes `is_effective`; latest actions use effective facts only.
History is bounded to 20 by default and 100 maximum, but latest care actions cover all history.
Archive and restore are ordinary append-only events. The projection stores their current result;
normal lists and search omit archived entities without replaying history. Callers can opt into
archived rows, and exact plant context remains readable for recovery and auditing.

`auth.py` delegates OAuth/PKCE protocol validation to the MCP SDK and persists OAuth records
in the same database. Access tokens last one hour; refresh tokens rotate and last 30 days.
Household password hashes use salted scrypt. Opaque tokens and authorization codes are stored
by SHA-256 lookup hashes; OAuth client secrets remain private database records for SDK validation.
