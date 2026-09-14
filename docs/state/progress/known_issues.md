# Known limits and next steps

- One shared household password and scope; no separate users, roles or tenant isolation.
- The deployed Streamable HTTP endpoint works in ChatGPT and Claude through its publicly trusted
  DNS hostname. ChatGPT now completes OAuth and reaches authenticated MCP requests. Its first
  successful connection displayed no actions because tool security was present only in `_meta`;
  the server now mirrors it to the top-level OpenAI descriptor field, pending a fresh production
  deployment and ChatGPT tool scan. The raw-IP URL and OpenAI Secure MCP Tunnel did not work, and
  the tunnel remains installed but stopped.
- The host has an unrelated, orphaned `archivist-postgres` container publishing PostgreSQL on
  port 5432. It has a persistent Docker volume but no Compose ownership or restart policy. Her
  Garden does not use that listener; retirement requires a separate data-retention decision.
- Production contains household plant data and must retain its PostgreSQL volume across deploys.
  The executed public demo remains isolated and uses explicit fictional examples from the spec.
- Search is case-insensitive substring matching, not typo-tolerant or semantic search.
- Lists return all matches. Per-entity replay and full-history reads are appropriate for a
  small household; add pagination/indexed projections only if volume requires them.
- Models must distinguish facts from plans and observations from diagnoses. Typed validation
  rejects future completions but cannot infer the truth or meaning of a free-text report.
- Idempotency prevents technical retries, not semantic duplicates with new request UUIDs.
- Creation events remain immutable. Entities can be archived and restored through later events;
  there is no physical deletion. Inventory and location history browsing is not exposed as a
  dedicated tool, although inventory corrections are supported by event ID.
- The lifecycle implementation has not been tested or deployed; validation was deliberately
  deferred at the owner's request.
- Local backups do not survive server loss. They are retained until explicitly removed;
  monitor disk space. No off-server destination was requested.
- App rollback does not undo migrations. Future migrations must preserve compatibility with
  the preceding image; destructive schema changes need a separate reviewed recovery plan.
- CI deploys app images. Changes to Compose, nginx, or the host deployment script require an
  explicit host configuration update; a code deployment does not silently replace host files.
- Before broadening access, replace the household provider with managed identity, add scoped
  per-user authorization, and review rate limits and retention.
