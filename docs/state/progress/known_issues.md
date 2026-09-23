# Known limits and next steps

- Telegram Bot API cannot initiate a private chat. Each allowed username must open the bot and
  send `/start` before delivery is possible. Authorization uses the current Telegram username;
  usernames can change or be reassigned, so the allowlist should be reviewed when access changes.
- Notification jobs survive restarts and stale buttons are rejected. Telegram delivery and the
  database commit are separate operations, so a crash between sending and marking a job sent can
  produce a duplicate reminder. If the bot is offline past a due date, it groups overdue plants
  into one catch-up message per chat when it resumes rather than replaying every missed occurrence.
  A newly edited plan can become due in a later scan on the same day; it then forms a new group.
- Reminder plans do not infer completed watering or reset themselves when a watering event is
  recorded. A one-time postponement can supersede a nearby regular date; the next ordinary
  reminder resumes the anchored cadence.
- Plants have locations but no tag field. The Telegram picker groups by location and paginates
  larger groups; dedicated tags would need a separate plant-data design and editing controls.

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
- The lifecycle implementation passes local static checks, PostgreSQL integration tests, the
  executed demo and a live Streamable HTTP smoke flow. It has not been deployed.
- Local backups do not survive server loss. They are retained until explicitly removed;
  monitor disk space. No off-server destination was requested.
- App rollback does not undo migrations. Future migrations must preserve compatibility with
  the preceding image; destructive schema changes need a separate reviewed recovery plan.
- CI deploys app images. Changes to Compose, nginx, or the host deployment script require an
  explicit host configuration update; a code deployment does not silently replace host files.
- Before broadening access, replace the household provider with managed identity, add scoped
  per-user authorization, and review rate limits and retention.
