# Decisions

**Decision: PostgreSQL events plus transactional projections**
One schema and one write transaction keep durable history and current state consistent.
Replaying one entity's events is sufficient for a household and makes corrections predictable.

- **Alternative**: Background projection worker
- **Description**: Queue events and update current state asynchronously.
- **Rejection reason**: Extra process and stale reads without a household-scale benefit.

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
