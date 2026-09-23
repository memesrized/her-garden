# Changes

## 2026-09-23 — Location picker and Telegram command menu

- Change: Replace the long `/plants` button list with location groups, 12-plant pages, and back
  navigation. Publish a native Telegram command menu and put direct buttons on `/start`.
- Reasoning: Existing location IDs organize the current plants without introducing a new tag
  field. The picker reads current projections so moves and renames appear on the next open.
- Verification: Bot navigation tests cover grouping, the unassigned group, pagination and
  callback back links; the executed public-fixture notebook shows the menu and a second page.
- Files: `src/her_garden/bot.py`, `tests/test_bot.py`, `data/demo_garden.json`,
  `notebooks/demos/telegram_navigation.ipynb`, `README.md`, `docs/STATE.md`,
  `docs/state/system/data_flow.md`, `docs/state/system/operations.md`,
  `docs/state/architecture/decisions.md`, `docs/state/progress/known_issues.md`,
  `docs/state/changes/CHANGELOG_001.md`.

## 2026-09-23 — Group plants due together into one Telegram reminder

- Change: Send one message per enrolled chat for plants due in the same scan, with one button
  applying the selected delay or series shift to every listed plant atomically.
- Reasoning: A shared cycle ID reuses the existing durable notification table and leaves earlier
  one-plant messages valid. Individual MCP adjustments now use whole days so ordinary reminders
  keep the household-wide clock time.
- Verification: Thirty PostgreSQL and bot tests pass, including all seven grouped buttons,
  multi-recipient stale protection, and atomic rejection; the executed public-fixture notebook
  shows two plants in one message.
- Files: `src/her_garden/watering.py`, `src/her_garden/bot.py`, `src/her_garden/server.py`,
  `tests/test_watering.py`, `tests/test_bot.py`, `data/demo_garden.json`,
  `notebooks/demos/watering_reminders.ipynb`, `README.md`, `docs/STATE.md`,
  `docs/state/system/data_flow.md`, `docs/state/system/operations.md`,
  `docs/state/architecture/decisions.md`, `docs/state/progress/known_issues.md`,
  `docs/state/changes/CHANGELOG_001.md`.

## 2026-09-23 — Optional watering reminders and Telegram controls

- Change: Add anchored per-plant watering schedules, a shared local reminder clock time,
  MCP read/write tools, and an optional private Telegram bot with seven postponement and
  series-shift buttons.
- Reasoning: Plans stay separate from completed care events. The bot shares PostgreSQL state
  with MCP but runs as a separate opt-in Compose service, so absent Telegram configuration
  does not affect MCP availability.
- Safety: Username allowlisting is checked for private bot updates and again before delivery;
  notification state and retry IDs are durable. Credentials remain outside source and images.
- Verification: Strict typing, lint, PostgreSQL integration tests, bot access tests, Compose
  validation, and the executed public-fixture demo cover the new behavior.
- Files: `src/her_garden/watering.py`, `src/her_garden/bot.py`,
  `src/her_garden/migrations/002_watering.sql`, `src/her_garden/server.py`,
  `src/her_garden/config.py`, `compose.yaml`, `scripts/remote-deploy.sh`, `pyproject.toml`,
  `uv.lock`, `tests/test_watering.py`, `tests/test_bot.py`, `tests/test_auth.py`,
  `notebooks/demos/watering_reminders.ipynb`, `README.md`, `.env.example`,
  `docs/plant_memory_mcp_spec.md`, and current project-state files.


## 2026-09-15 — Append-only rename, archive and restore events

- Change: Add `append_location_event`; extend plant and inventory event tools with archive and
  restore; and let location events rename without changing the stable entity ID. Normal lists and
  search hide archived rows unless `include_archived=true`, while exact plant context remains
  readable.
- Safety: Events remain immutable, creation facts are preserved, archived names continue to map
  to the original location, and a location cannot be archived while active plants reference it.
  Existing tables and the production PostgreSQL volume require no migration or replacement.
- Performance: Writes continue to replay only the affected entity. Lists and search filter the
  stored JSONB projection, so no full-history scan or snapshot subsystem was added.
- Verification: Ruff and strict mypy pass; all 12 PostgreSQL integration tests pass; and the
  executed demo covers archive visibility and recovery. A live local Streamable HTTP smoke test
  passed health, initialize, discovery of all ten tools, create, archive, hidden lookup, exact
  archived context and restore. The branch has not been deployed.

## 2026-09-13 — Advertise ChatGPT tool security at both descriptor levels

- Finding: ChatGPT completed OAuth and made authenticated MCP requests, but displayed no app
  actions. The server returned all nine tools with security schemes only in `_meta`, while the
  OpenAI descriptor format expects a top-level `securitySchemes` field and treats `_meta` as a
  compatibility mirror.
- Change: Extend the SDK tool listing in one compatibility class so every descriptor carries the
  same security declaration at both levels. Tool implementations and authorization enforcement
  are unchanged.
- Verification: Static checks pass and a serialized local tool listing contains all nine tools
  with matching top-level and `_meta` declarations. A fresh ChatGPT tool scan remains required.

## 2026-09-13 — Allow the validated callback in form CSP

- Finding: Removing CSP from the redirect response did not let the browser leave the garden
  origin because the policy loaded with the original login document governs redirected form
  navigation too.
- Change: Allow `https://chatgpt.com` alongside `'self'` in the login page's `form-action` policy.
  The form still submits only to itself, registered callbacks are already restricted to that exact
  HTTPS origin, and framing remains forbidden.
- Verification: The browser must now be observed leaving the garden origin and ChatGPT must call
  the token endpoint; automated validation covers the CSP and complete OAuth exchange.

## 2026-09-13 — Permit the browser callback navigation

- Finding: With the stable callback enabled, the browser submitted the password form and received
  HTTP 303 but remained on the garden hostname. ChatGPT therefore never received the authorization
  code and never called the token endpoint.
- Change: Keep the strict CSP on the rendered password form, but omit its `form-action 'self'`
  policy from the intentional redirect to the already validated ChatGPT callback.
- Verification: The integration test checks both the callback issuer and absence of the form-page
  CSP on the redirect response; a fresh browser connection remains the final validation.

## 2026-09-13 — ChatGPT stable OAuth callback

- Finding: ChatGPT registered a valid callback, the household password was accepted and an
  authorization code was issued, but ChatGPT never called the token endpoint. Removing the login
  response CSP from the redirect did not change that boundary, so that speculative change was
  reverted.
- Change: Advertise RFC 9207 authorization-response issuer identification and append the exact
  issuer to every successful or error redirect sent to a validated ChatGPT callback. This makes a
  newly registered connection eligible for ChatGPT's stable callback path.
- Verification: Static checks pass; the full PostgreSQL OAuth integration test and deployment
  verification remain required before asking the owner to reconnect.

## 2026-09-13 — Restore OAuth after hostname validation

- Deployment: Set `AUTH_ENABLED=true` in both the remote private environment and the GitHub
  `production` environment variable, then recreate only the application container.
- Reasoning: Anonymous mode established that ChatGPT accepts the DNS hostname, so the temporary
  access relaxation is no longer needed.
- Verification: Anonymous initialize returns HTTP 401 with protected-resource discovery; metadata
  advertises the hostname issuer, dynamic client registration, `garden` scope and PKCE `S256`.
  The application is healthy and the retained tunnel remains disabled.

## 2026-09-13 — Dedicated DNS hostname

- Deployment: Add a dedicated automatic-DNS hostname and publicly trusted Let's Encrypt
  certificate in host-managed nginx, while keeping the exact hostname out of the repository.
- Isolation: Serve only Her Garden routes on that hostname and return 404 at `/`; the existing
  IP-hosted apps and port 8443 configuration remain unchanged.
- Verification: The hostname resolves to the server, TLS validates, `GET /garden/mcp` opens an
  SSE stream, and anonymous `POST initialize` returns HTTP 200 with MCP protocol `2025-11-25`.
- Host finding: Port 5432 belongs to a separate orphaned `archivist-postgres` container and volume,
  not the internal Her Garden PostgreSQL service. It was inspected but not modified.

## 2026-09-06 — Return to direct anonymous access

- Deployment: Set the remote private `AUTH_ENABLED` value and GitHub production variable to
  `false`. External MCP initialize now returns HTTP 200 without credentials, using protocol
  `2025-11-25`; OAuth discovery is inactive.
- Tunnel: Keep the verified OpenAI tunnel-client installation, profile and private credential,
  but stop and disable its systemd service after the ChatGPT tunnel attempt failed.
- Reasoning: The owner chose the working, directly reachable Claude-compatible endpoint while
  retaining both OAuth and tunnel configurations for a later revisit.

## 2026-09-06 — ChatGPT literal-IP diagnosis

- Finding: A connector attempt using a fresh query-suffixed URL produced no request at nginx,
  even with an unfiltered method/header-only diagnostic log. ChatGPT rejected the connection
  before HTTP reached the service.
- Verification: The raw-IP endpoint remains externally reachable, its Let's Encrypt certificate
  validates for the exact IP, and MCP initialize plus tool listing succeed. Temporary diagnostic
  configuration and logs were removed immediately after capture.
- Conclusion: OpenAI's published MCP requirements specify a publicly accessible domain. A real
  hostname or OpenAI Secure MCP Tunnel is required for the next ChatGPT test; further MCP protocol
  changes cannot affect a request that never reaches the server.

## 2026-09-06 — ChatGPT no-auth tool descriptors

- Change: Advertise `_meta.securitySchemes: [{"type": "noauth"}]` on every tool while
  `AUTH_ENABLED=false`, matching the OpenAI tool descriptor format instead of returning empty
  metadata. OAuth descriptors and the complete OAuth implementation remain unchanged.
- Reasoning: Temporary request logging showed that ChatGPT reached `POST /garden/mcp` twice and
  received HTTP 200 with MCP `2025-11-25`, so transport, GET probing and protocol negotiation were
  ruled out. ChatGPT then stopped while Claude continued; tool discovery was the next boundary.
- Verification: The no-auth integration test now initializes the server, lists all nine tools and
  checks the documented no-auth security declaration on each descriptor.

## 2026-09-06 — Deployment authentication switch

- Change: Add an `AUTH_ENABLED` setting that defaults to `true`; when false, the MCP transport
  permits anonymous requests while the OAuth implementation and stored grants remain intact.
- Deployment: Read the setting from the GitHub `production` environment and pass it through the
  constrained SSH deployment command as a validated boolean.
- Reasoning: This permits a temporary anonymous ChatGPT connectivity test and ensures later CI/CD
  deployments reproduce the selected mode instead of relying on undocumented server state.
- Limitation: The public IP receives automated scans, so anonymous mode is intended only for a
  short test window.
- Verification: External anonymous initialization returns HTTP 200 using MCP protocol
  `2025-11-25`, and Claude connects successfully. ChatGPT still fails to connect, establishing
  that its current failure is independent of OAuth.

## 2026-09-06 — OAuth discovery compatibility

- Change: Add proxy aliases for origin and issuer-relative OAuth discovery.
- Reasoning: ChatGPT reported missing S256 even though canonical metadata advertised it;
  alternate discovery URLs returned 404. All aliases serve the same canonical metadata.
- Files: `deploy/nginx-location.conf`, `docs/state/system/operations.md`.

## 2026-09-06

- Change: Implement the single-household Plant Memory MCP MVP.
- What: Nine typed tools, PostgreSQL event/projection transactions, retry-safe writes,
  corrections, fuzzy inventory, OAuth household consent, Docker Compose and deployment workflows.
- Reasoning: Keep facts durable and retrieval compact without adding plant-care intelligence
  or asynchronous infrastructure. See the architecture decisions for alternatives.
- Validation: Integration tests exercise real PostgreSQL and SDK OAuth/HTTP routes; the notebook
  records a retry, backdated action, correction and approximate inventory using public fixtures.
- Next steps: Complete the first user login in ChatGPT; improve only from actual household use.
- Files: `src/her_garden/`, `tests/`, `compose.yaml`, `Dockerfile`, `.github/workflows/`,
  `scripts/`, `deploy/`, `notebooks/demos/plant_memory.ipynb`, `docs/state/`.
