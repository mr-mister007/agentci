#!/usr/bin/env bash
set -euo pipefail

backup_dir="${1:?usage: scripts/restore.sh <backup-dir>}"
project="${AGENTCI_COMPOSE_PROJECT:-agentci}"

read -r -p "Restore AgentCI from $backup_dir? Type 'yes' to continue: " confirmation
if [[ "$confirmation" != "yes" ]]; then
  exit 1
fi

docker compose --project-name "$project" stop api worker

gunzip -c "$backup_dir/postgres.sql.gz" | docker compose --project-name "$project" exec -T postgres \
  psql -U agentci -d agentci

docker run --rm \
  -v "${project}_agentci-artifacts:/data" \
  -v "$backup_dir:/backup:ro" \
  alpine:3.20 \
  sh -c 'find /data -mindepth 1 -delete && tar xzf /backup/artifacts.tar.gz -C /data'

docker compose --project-name "$project" start api worker

echo "Restore complete"
