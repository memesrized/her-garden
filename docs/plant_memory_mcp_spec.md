# Plant Memory MCP: MVP Specification

## Goal

Build a small MCP server that gives an LLM persistent, reliable memory about a user's plants.

The MCP should **not** implement plant-care expertise, diagnosis, recommendations, or botanical knowledge. The LLM already handles that.

Its job is to provide:
- what plants exist;
- where they are;
- their current state;
- what happened to them over time;
- what plant-care supplies are currently available.

Primary use case: the user can ask natural follow-up questions such as “should I water this one already?” without having to repeat when it was last watered, repotted, fertilized, moved, etc.

The design should also work reasonably well with smaller/local models, so tools should minimize ambiguity and unnecessary context.

---

## Core data model

The database should represent the same information in two forms:

### 1. Event history

Append-only history of relevant facts/actions, for example:
- watering;
- fertilizing;
- repotting;
- moving;
- pruning;
- treatment;
- propagation;
- observations/symptoms;
- inventory purchases/usage;
- corrections to earlier information.

Historical information should not normally be overwritten.

### 2. Current state

Materialized/current state derived from the event history.

At minimum:
- current state of each plant;
- current inventory state;
- current plant locations.

The exact storage/projection implementation is intentionally left open for later technical planning.

---

## Main entities

### Plants

Each physical plant should have a stable `plant_id`.

Names, species identification, location, pot, substrate, status, etc. may change, but historical events should remain associated with the same plant.

### Locations

Named places where plants can live, for example:
- north balcony;
- bedroom shelf;
- kitchen windowsill.

Locations should have stable IDs so models do not need to guess exact free-text names.

### Inventory

Plant-care supplies, for example:
- soil;
- perlite;
- Seramis;
- coconut substrate;
- fertilizer;
- treatments;
- pots.

Inventory does not need precise quantities when the user only knows approximate amounts.

---

## MCP read tools

### `list_locations()`

Returns the available locations with stable IDs and short human-readable names.

Intended workflow for location-specific queries:

1. call `list_locations()`;
2. select the correct location ID;
3. call `list_plants(location_id=...)`.

This is preferred over making the model guess a location string.

### `list_plants(filters...)`

Returns a **compact list/index**, not full history.

Should support optional filters, especially:
- `location_id`;
- status;
- potentially tags/groups later.

Without filters, returns all plants.

Purpose:
- “What plants do I have?”
- “What is on the balcony?”
- resolve which plant(s) are relevant before loading detailed context.

### `find_plants(query)`

Searches plants by human-friendly name, alias, species, etc.

Used when the user refers to a plant naturally, for example “the crassula”.

Returns candidate plant IDs rather than full plant histories.

### `get_plant_context(plant_id)`

Returns the detailed context needed for reasoning about one plant.

Conceptually includes:
- current plant state;
- recent/relevant event history;
- latest important actions such as watering, fertilizing and repotting.

This should be the main detailed read tool.

### `get_inventory(filters...)`

Returns current inventory, optionally filtered by category/type.

---

## MCP write tools

### `append_plant_event(...)`

Adds a new plant event.

This is the main write operation.

Important behavior:
- event history is append-only;
- actions explicitly reported by the user can be stored as facts;
- observations should be stored as observations rather than converted into diagnoses;
- hypothetical/planned actions should not be stored as completed actions.

Technical retries should not accidentally create duplicate events. Exact idempotency design can be decided later.

### `create_plant(...)`

Creates a new tracked plant and stable ID.

### `append_inventory_event(...)`

Records inventory changes, for example:
- bought a bag of soil;
- used some perlite;
- only half a bag remains.

Inventory should support approximate/fuzzy amounts.

### Optional administration tools

May be useful later for correcting names, aliases, locations, etc., but are not required for the first MVP if equivalent state changes can be represented through events.

---

## Expected LLM usage pattern

For a specific plant:

```text
User mentions a plant
→ find_plants(...)
→ get_plant_context(plant_id)
→ answer
```

For a location/group:

```text
User asks about plants on the balcony
→ list_locations()
→ list_plants(location_id=...)
→ get_plant_context(...) only for plants that need detailed reasoning
```

For a new fact/action:

```text
User says they watered / repotted / fertilized / moved a plant
→ resolve plant
→ optionally inspect recent context to avoid semantic duplicates
→ append_plant_event(...)
```

The skill/instructions for the LLM can define this orchestration separately from the MCP implementation.

---

## Design requirements

- Prefer stable IDs over free-text references between tools.
- Keep list/search responses compact.
- Load detailed history only for plants relevant to the current discussion.
- Optimize for models weaker than frontier ChatGPT as well.
- Keep the server mostly CRUD/event-storage logic, not plant-care intelligence.
- Preserve history rather than mutating past facts.
- Corrections should ideally be represented without silently destroying historical records.
- Do not require exact measurements when users naturally provide approximate information.
- Photos/images are explicitly out of MVP scope, but should remain a possible future extension.

---

## Out of scope for MVP

Do **not** build:
- plant-care recommendation engine;
- diagnosis engine;
- botanical knowledge base;
- fixed watering/fertilizing schedules;
- reminders;
- weather integration;
- automatic image storage/analysis;
- complex symptom ontology.

These can be reconsidered later if real usage shows a need.

---

## MVP summary

Core concepts:

```text
Plants
Locations
Inventory
Event history
Current-state projections
```

Core tools:

```text
list_locations
list_plants
find_plants
get_plant_context
get_inventory

create_plant
append_plant_event
append_inventory_event
```

The main purpose of the MCP is simple: **give the LLM durable episodic memory about specific plants and household plant-care inventory, while keeping reasoning and recommendations on the LLM side.**
