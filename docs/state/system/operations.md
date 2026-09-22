# Operations

## Deployment

Production runs as Compose project `her-garden` in `/home/codex/apps/her-garden` on the
configured SSH alias `remote-machine`. Nginx serves the app through a dedicated automatic-DNS
hostname and includes `deploy/nginx-location.conf`. The hostname and certificate are host-private
deployment configuration; certificate renewal is managed by Certbot. The app uses `/garden/mcp`,
consent is `/garden/login`, and readiness is `/garden/health`. OAuth discovery uses the standard
well-known paths. The hostname returns 404 at `/`, while the existing IP website remains separate.
Her Garden's PostgreSQL is accessible only inside the Compose network.

`docker compose up -d --build --wait` performs a manual deployment. Startup applies numbered
SQL migrations under a transaction advisory lock. Never edit an already-applied migration.
Use a new numbered SQL file and keep it compatible with the preceding application release.

## Optional Telegram reminders

The `telegram` Compose profile runs a separate bot process using the same private database.
The MCP application starts and remains healthy without this profile or bot credentials. The bot
requires `BOT_TOKEN` and a comma-separated `TG_USERNAMES` allowlist in the private server
environment. `WATERING_TIMEZONE` selects the local calendar used for the shared reminder time;
use an IANA timezone name. Do not put real values in the repository, build image, command line,
or logs. The release workflow transfers an image; the server retains its private environment.

To enable the bot after setting its private environment, run
`docker compose --profile telegram up -d --no-build bot`. The bot uses outbound polling and does
not need a public port or proxy route. Every permitted username must initiate a private chat
with the bot before it can receive reminders. Check the bot container state and bounded logs
after enabling it. A failed Telegram connection does not stop MCP.

Later image deployments update the bot only while both bot settings are present. Removing those
settings and deploying again stops the bot. A bot started manually without the profile is not
part of the normal deployment path.

## Authentication and secrets

Generate `.env` with `scripts/configure.py`. Keep it mode 0600, outside Git. The initial
production household credential is in `household-access.txt` on the server, mode 0600.
This credential file, local `secrets/`, backups, SSH keys and environment files must never
enter Git or Docker build contexts. The Docker context is an allowlist of application files.
Never log OAuth query strings, passwords, tokens, or database connection strings.

Connect ChatGPT with OAuth and dynamic client registration. There is no signup. Anonymous MCP
access is disabled by default and can be enabled temporarily with `AUTH_ENABLED=false`. The
shared password is entered only on the server's consent page. Public metadata
and client registration do not expose plant data. Access tokens expire after one hour;
refresh tokens rotate and expire after 30 days. Each refresh invalidates the previous pair.
The consent form has CSRF protection and a ten-attempt-per-minute household login limit.

The current deployment sets `AUTH_ENABLED=true`, so the internet-facing MCP endpoint requires
OAuth. The remote private environment and GitHub `production` environment variable both record
this setting so later CI/CD deployments preserve it.

Changing the household password does not revoke existing OAuth grants. To revoke access,
use the OAuth revocation endpoint with the registered client credentials, or perform an
explicitly authorized deletion of the relevant `oauth_records` token records. Do not erase
plant events. Password changes and revocations should be deliberate operator actions.

## Backups and restore

The systemd timer `her-garden-backup.timer` invokes `scripts/backup.sh` daily. Backups are
custom-format `pg_dump` files in the project's `backups/` directory. The deployment command
also creates a backup before replacing the application. Failed dumps stay `.partial`.
Backups are retained without automatic deletion; monitor available disk space.
Server loss also loses these backups, as explicitly accepted by the owner.

```sh
ssh remote-machine 'cd /home/codex/apps/her-garden && sh scripts/backup.sh'
ssh remote-machine 'systemctl status her-garden-backup.timer --no-pager'
```

First verify restore into a **new empty database**, not over production:

```sh
docker compose exec -T db createdb -U garden garden_restore_check
docker compose exec -T db pg_restore -U garden -d garden_restore_check --exit-on-error < backups/CHOSEN.dump
```

The restored database includes OAuth records. Keep it private and do not serve it as another
public instance. Inspect its schema/row counts. Replacing production data or deleting restore
check databases is a separate explicit operator decision.

## GitHub CI/CD

`master` remains the default branch. PR CI runs lint, strict typing, real PostgreSQL tests,
format checking and a Docker build on a GitHub-hosted runner. PR jobs get no production secrets.
After a successful push CI run on `master`, Deploy checks that the SHA is still current,
builds a SHA-tagged image and streams it over SSH to `scripts/remote-deploy.sh`.

The production environment contains `DEPLOY_HOST`, `DEPLOY_SSH_KEY`, and `DEPLOY_KNOWN_HOSTS`
secrets plus the non-secret `AUTH_ENABLED` deployment variable. An absent auth variable defaults
to `true`. The constrained deployment command accepts only a full image SHA and `true` or `false`.
Use a dedicated SSH key with `restrict,command="/bin/bash /home/codex/apps/her-garden/scripts/remote-deploy.sh"`
in `authorized_keys`. This allows only deployment of an image with a full commit SHA, without
interactive shell, PTY or port forwarding. Host identity is pinned, not accepted blindly.

The deployment script serializes runs with flock, loads the image, makes a backup, replaces
the app container and an enabled bot, and waits for readiness. On failed readiness it restores
the preceding image; it never deletes database volumes or automatically rewinds migrations. Successful
image and auth-mode selection is saved in private `.env`. Compose/nginx/host-script changes are
maintained separately from ordinary application-image releases.

PRs are merged manually after required CI passes; neither workflow merges PRs automatically.

## Secure MCP Tunnel

OpenAI `tunnel-client` 0.0.14 is installed under `/opt/openai/tunnel-client/` with profile
`her-garden`. The root-managed runtime credential is outside the repository in
`/etc/her-garden/tunnel.env`. The `her-garden-tunnel.service` unit and configuration are retained,
but the service is disabled and inactive because the ChatGPT tunnel experiment did not work and
the owner selected direct anonymous HTTPS access. Do not start the tunnel unless that decision is
revisited; never print or commit its runtime credential.

### Discovery compatibility

Nginx also serves authorization metadata at the origin well-known URL and at
`/garden/.well-known/oauth-authorization-server`, forwarding both to the canonical
RFC 8414 path `/.well-known/oauth-authorization-server/garden`. All return the same
issuer and `code_challenge_methods_supported: ["S256"]`. Origin-level protected-resource
discovery similarly forwards to the garden MCP metadata. These aliases avoid 404s when
clients probe a different discovery convention; they do not change authentication or PKCE.
Authorization metadata also advertises RFC 9207 issuer identification, and every redirect to a
validated ChatGPT callback includes the same exact issuer. This lets ChatGPT select its stable
callback and reject authorization responses from a mismatched issuer.
