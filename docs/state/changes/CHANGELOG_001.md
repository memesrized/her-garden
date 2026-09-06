# Changes

## 2026-09-06 — Local MCP client OAuth testing

- Change: Permit dynamic OAuth clients to register an exact `/callback` URL on the
  `127.0.0.1` or IPv6 loopback address, with any ephemeral port.
- Reasoning: Codex uses a loopback OAuth callback for remote MCP login. Keeping the host and
  path allowlist narrow enables direct integration testing without weakening household consent.
- Deliberately unchanged: Existing ChatGPT callbacks, PKCE, password consent, token storage,
  and the deployed public MCP URL remain unchanged.

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
