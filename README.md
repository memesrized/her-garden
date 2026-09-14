<p align="center">
  <img src="docs/assets/her-garden.png" alt="Her Garden plant and care checklist" width="420">
</p>

# Her Garden

A small authenticated Python MCP server giving ChatGPT persistent memory of one household's
plants and plant-care supplies. PostgreSQL keeps immutable events and current state together.
There is no recommendation engine, image storage, or scheduling system.

> [!WARNING]
> This project was purely vibe-coded and has not received a proper independent code review.
> It is provided as-is, without warranty. Review the code and deployment configuration before
> trusting it with important data or exposing it to the internet.

## Run

Requires Docker Compose. Python development uses Python 3.14 and uv.

```sh
uv sync --group dev
uv run python scripts/configure.py --public-url https://YOUR_PUBLIC_HOST/garden
docker compose up -d --build --wait
```

The setup prompts for a household password and creates `.env` with mode 0600. Never commit it.
PostgreSQL has a persistent Docker volume and no host port. The app binds to host loopback
port 8002. Put `deploy/nginx-location.conf` inside your existing **trusted HTTPS** nginx server
block. A valid public IP certificate works; self-signed certificates are not sufficient.
Local development can use `http://localhost:8002/garden`.

## Deployment

```mermaid
flowchart LR
  source["Python app + uv"] --> image["Docker image"]
  image --> compose["Docker Compose"]
  compose --> app["MCP app on 127.0.0.1:8002"]
  compose --> db[("Persistent PostgreSQL volume")]
  app --> nginx["nginx + trusted HTTPS"]
  nginx --> ip["Public IP<br/>Claude / ChatGPT Work"]
  nginx --> dns["DNS name<br/>ChatGPT Plugin"]
```

The DNS route is optional for clients that accept a public IP endpoint. ChatGPT Plugins require
a DNS hostname that resolves to the server and is covered by the HTTPS certificate.

## MCP client connection

The MCP endpoint is `https://YOUR_PUBLIC_HOST/garden/mcp` and uses Streamable HTTP.

- **Claude and ChatGPT Work:** the endpoint works with either a DNS hostname or a public IP,
  provided HTTPS is trusted and the certificate is valid for the address used.
- **ChatGPT Plugin:** use a DNS hostname. In testing, ChatGPT did not send any request to the MCP
  server when configured with the raw IP URL, even though the same endpoint worked in other
  clients. After adding a DNS name, ChatGPT connected and discovered the tools normally.

For a ChatGPT Plugin, enable developer mode where available, then create a custom MCP connection
with the DNS-based endpoint URL and **OAuth** authentication.

Authentication is enabled by default. Production deployments read `AUTH_ENABLED` from the
GitHub `production` environment, defaulting to `true`. Setting it to `false` temporarily
serves the MCP endpoint without OAuth while leaving the OAuth implementation and stored grants
intact.
Leave client ID/secret empty to use dynamic registration. Follow the login page and enter
the household password to grant garden access. There is no public signup.
ChatGPT account/workspace availability and write-tool permissions are controlled by ChatGPT.
See [OpenAI authentication documentation](https://developers.openai.com/plugins/build/auth).

Try: “Create a location called north balcony, and track my Crassula there.”
Then: “I watered the Crassula yesterday.” Use the actual local timezone in event timestamps.
The first real ChatGPT connection must be completed in your own account.

## Tools

| Tool | Purpose |
|---|---|
| `list_locations` / `create_location` | Stable location IDs; duplicate names reuse IDs |
| `list_plants` / `find_plants` | Compact lookup by location/status or name/alias/species |
| `get_plant_context` | Current state, bounded history and latest effective care actions |
| `create_plant` | New physical plant with stable ID |
| `append_plant_event` | Completed action, observation, attribute update or correction |
| `get_inventory` / `append_inventory_event` | Supply memory with approximate remaining amounts |

Every write requires a UUID `request_id`: reuse it unchanged for technical retries.
Using the same ID with different input fails. Separate requests with equivalent meaning are
not automatically deduplicated; the client should resolve plants and inspect context first.
`occurred_at` must include an offset and cannot be in the future. Observations never become
diagnoses. `changes` patches only supplied fields; explicit null clears optional attributes.

Correct an event by passing its ID as `supersedes_event_id` with a complete replacement event
and its corrected occurrence time. The original remains visible in history, marked ineffective.
Use event type `void` to retract a mistaken event. Creation events cannot be retracted;
plant attributes are corrected with `update`. Correct a replacement by referring to its ID.

Inventory `remaining` is an absolute description after the event, not a delta. “Half a bag”
is valid. Usage/purchase with no remaining amount makes the amount unknown rather than
inventing arithmetic. Existing items use `item_id`; new items require `name` and `category`.

## Development and demo

```sh
uv sync --group dev
docker compose -f compose.test.yaml up -d --wait
export TEST_DATABASE_URL=postgresql://garden_test:test-only-password@localhost:55432/garden_test
make check
uv run python scripts/build_demo.py
```

Tests and the demo create isolated schemas in that **test database**. Do not point them at
production. The [executed demo](notebooks/demos/plant_memory.ipynb) uses public examples from
`data/demo_garden.json`, not real household data. Docker test storage is temporary.

Read [project state](docs/STATE.md) for architecture and limitations, and
[operations](docs/state/system/operations.md) for backups, recovery and CI/CD.
