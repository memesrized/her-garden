# Changes

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
