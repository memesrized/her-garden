#!/bin/sh
# Run from the deployed project directory; backups stay on this server.
set -eu
umask 077
mkdir -p backups
backup_path="backups/garden-$(date -u +%Y%m%dT%H%M%SZ).dump"
docker compose exec -T db pg_dump -U garden -d garden -Fc > "$backup_path.partial"
mv "$backup_path.partial" "$backup_path"
# Deliberately retain backups; deletion is an explicit operator choice.
