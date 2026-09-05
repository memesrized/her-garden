#!/bin/bash
# Install as a forced SSH command. Only an immutable Git SHA is accepted.
set -euo pipefail
cd /home/codex/apps/her-garden
exec 9>.deploy.lock
flock -w 300 9
revision=${SSH_ORIGINAL_COMMAND:-}
if [[ ! "$revision" =~ ^[0-9a-f]{40}$ ]]; then
    echo 'Expected a full commit SHA' >&2
    exit 2
fi
image="her-garden:$revision"
previous=$(docker inspect --format '{{.Image}}' "$(docker compose ps -q app)")
gunzip | docker load
docker image inspect "$image" >/dev/null
sh scripts/backup.sh
GARDEN_IMAGE="$image" docker compose up -d --no-build --wait --wait-timeout 120 app || {
    if [[ -n "$previous" ]]; then
        GARDEN_IMAGE="$previous" docker compose up -d --no-build --wait app
    fi
    echo 'Deployment failed; previous application image restored when available.' >&2
    exit 1
}
printf 'GARDEN_IMAGE=%s\n' "$image" > .image.env
# Preserve the chosen image for ordinary docker compose restarts.
python3 - "$image" <<'PY'
import pathlib
import sys
path = pathlib.Path('.env')
lines = [line for line in path.read_text().splitlines() if not line.startswith('GARDEN_IMAGE=')]
path.write_text('\n'.join([*lines, f'GARDEN_IMAGE={sys.argv[1]}']) + '\n')
PY
curl --fail --silent http://127.0.0.1:8002/garden/health
