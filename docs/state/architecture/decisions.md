# Decisions

**Decision: Watering plans are separate from completed plant events**
Schedules and temporary postponements are durable configuration, while a watering event remains
a report of completed care. This prevents a reminder or button press from becoming a false fact.

- **Alternative**: Store future plans as ordinary plant events
- **Description**: Add scheduled and snoozed event types to the existing append-only history.
- **Rejection reason**: Existing event validation and projection deliberately represent completed
  facts; mixing plans into that stream would make care history ambiguous.

**Decision: Optional polling bot with a shared PostgreSQL schedule store**
The bot runs in its own Compose profile and uses username allowlisting for private chat actions.
Chat IDs are retained only as delivery addresses. MCP tools remain available without bot secrets.

- **Alternative**: Run the bot inside the MCP web process
- **Description**: Start Telegram polling in the MCP application lifespan.
- **Rejection reason**: Telegram credentials or a polling failure would couple bot availability to
  the authenticated MCP endpoint and complicate deployment rollback.


**Decision: PostgreSQL events plus transactional projections**
One schema and one write transaction keep durable history and current state consistent.
Replaying one entity's events is sufficient for a household and makes corrections predictable.

- **Alternative**: Background projection worker
- **Description**: Queue events and update current state asynchronously.
- **Rejection reason**: Extra process and stale reads without a household-scale benefit.

**Decision: Append-only entity lifecycle events**
Plants, locations and inventory items are archived and restored by new events. Their UUIDs and
complete histories remain intact, while the current projection makes normal list and search
queries omit archived entities. Locations can also be renamed by event and cannot be archived
while active plants still refer to them.

- **Alternative**: Physically delete entities or events
- **Description**: Remove mistaken or retired records directly from PostgreSQL.
- **Rejection reason**: Loses the audit trail and conflicts with the existing immutable-event
  database guard.

- **Alternative**: Add snapshots or an asynchronous incremental projector
- **Description**: Store replay checkpoints or update projections outside the write transaction.
- **Rejection reason**: Current writes replay only one entity and ordinary reads already use the
  projection; household-scale history does not justify extra state or recovery machinery.

**Decision: One household, SDK OAuth and a password consent page**
ChatGPT requires compatible OAuth for private remote access. There is no user registry or
signup. The SDK handles registration, client authentication, PKCE, tokens and discovery;
our provider supplies household consent and durable credential storage. Only ChatGPT callback
URLs can register. A client registration itself grants no access.

- **Alternative**: External identity provider
- **Description**: Delegate login and token issuance to a hosted OAuth provider.
- **Rejection reason**: Adds another account and service for a single shared garden.

**Decision: Descriptive inventory quantities**
Record what remains when reported; otherwise usage/purchase clears the quantity to unknown.

- **Alternative**: Quantity parsing and automatic arithmetic
- **Description**: Convert bags, handfuls and approximate statements into numeric stock.
- **Rejection reason**: Would invent precision and requires unit-conversion rules outside scope.

**Decision: Reuse server HTTPS and transfer release images over constrained SSH**
Compose manages only the app and private database; the existing proxy and IP certificate stay
host-managed. CI builds an image tagged by Git SHA and streams it to a forced deployment
command, avoiding a separate registry credential or public package configuration.

- **Alternative**: Self-hosted GitHub runner on production
- **Description**: Execute CI jobs directly on the persistent server.
- **Rejection reason**: Public PR code should not execute with production host access.
