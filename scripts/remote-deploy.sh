#!/bin/bash
# Install as a forced SSH command. Only a Git SHA and optional auth boolean are accepted.
set -euo pipefail
cd /home/codex/apps/her-garden
exec 9>.deploy.lock
flock -w 300 9
deployment=${SSH_ORIGINAL_COMMAND:-}
if [[ ! "$deployment" =~ ^([0-9a-f]{40})(\ (true|false))?$ ]]; then
    echo 'Expected a full commit SHA and optional true/false auth flag' >&2
    exit 2
fi
revision=${BASH_REMATCH[1]}
auth_enabled=${BASH_REMATCH[3]:-true}
image="her-garden:$revision"
previous=$(docker inspect --format '{{.Image}}' "$(docker compose ps -q app)")
previous_auth=$(sed -n 's/^AUTH_ENABLED=//p' .env | tail -1)
previous_auth=${previous_auth:-true}
services=(app)
compose_options=()
bot_running=false
if [[ -n "$(docker compose --profile telegram ps -q bot)" ]]; then
    bot_running=true
fi
if grep -Eq '^BOT_TOKEN=.+$' .env && grep -Eq '^TG_USERNAMES=.+$' .env; then
    services+=(bot)
    compose_options=(--profile telegram)
fi
restore_previous() {
    if [[ -n "$previous" ]]; then
        GARDEN_IMAGE="$previous" AUTH_ENABLED="$previous_auth" \
            docker compose up -d --no-build --wait app
    fi
    if [[ "$bot_running" == true && -n "$previous" ]]; then
        GARDEN_IMAGE="$previous" AUTH_ENABLED="$previous_auth" \
            docker compose --profile telegram up -d --no-build --wait bot
    else
        docker compose --profile telegram stop bot >/dev/null 2>&1 || true
    fi
}
gunzip | docker load
docker image inspect "$image" >/dev/null
sh scripts/backup.sh
GARDEN_IMAGE="$image" AUTH_ENABLED="$auth_enabled" \
    docker compose "${compose_options[@]}" up -d --no-build --wait --wait-timeout 120 "${services[@]}" || {
    restore_previous
    echo 'Deployment failed; previous application image restored when available.' >&2
    exit 1
}
if [[ "${services[*]}" == "app bot" ]]; then
    sleep 5
    bot_container=$(docker compose --profile telegram ps -q bot)
    if [[ -z "$bot_container" || "$(docker inspect --format '{{.State.Running}}' "$bot_container")" != true ]]; then
        restore_previous
        echo 'Bot did not stay running; previous services restored.' >&2
        exit 1
    fi
elif [[ "$bot_running" == true ]]; then
    docker compose --profile telegram stop bot
fi
printf 'GARDEN_IMAGE=%s\n' "$image" > .image.env
# Preserve the chosen image and auth mode for ordinary docker compose restarts.
python3 - "$image" "$auth_enabled" <<'PY'
import pathlib
import sys
path = pathlib.Path('.env')
lines = [
    line for line in path.read_text().splitlines()
    if not line.startswith(('GARDEN_IMAGE=', 'AUTH_ENABLED='))
]
path.write_text(
    '\n'.join([*lines, f'GARDEN_IMAGE={sys.argv[1]}', f'AUTH_ENABLED={sys.argv[2]}']) + '\n'
)
PY
curl --fail --silent http://127.0.0.1:8002/garden/health
