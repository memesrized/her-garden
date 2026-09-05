# Known limits and next steps

- One shared household password and scope; no separate users, roles or tenant isolation.
- First connection from the user's actual ChatGPT account remains a human verification step.
  SDK integration tests verify OAuth and tools, not ChatGPT account eligibility or UI behavior.
- No actual plant data was supplied. The executed public demo uses explicit fictional examples
  from the spec; production starts empty.
- Search is case-insensitive substring matching, not typo-tolerant or semantic search.
- Lists return all matches. Per-entity replay and full-history reads are appropriate for a
  small household; add pagination/indexed projections only if volume requires them.
- Models must distinguish facts from plans and observations from diagnoses. Typed validation
  rejects future completions but cannot infer the truth or meaning of a free-text report.
- Idempotency prevents technical retries, not semantic duplicates with new request UUIDs.
- Creation events cannot be retracted. Location renaming and inventory history browsing are
  not exposed as tools in this MVP. Inventory corrections are supported by event ID.
- Local backups do not survive server loss. They are retained until explicitly removed;
  monitor disk space. No off-server destination was requested.
- App rollback does not undo migrations. Future migrations must preserve compatibility with
  the preceding image; destructive schema changes need a separate reviewed recovery plan.
- CI deploys app images. Changes to Compose, nginx, or the host deployment script require an
  explicit host configuration update; a code deployment does not silently replace host files.
- Before broadening access, replace the household provider with managed identity, add scoped
  per-user authorization, and review rate limits and retention.
