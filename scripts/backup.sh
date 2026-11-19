#!/usr/bin/env bash
set -euo pipefail

project="${AGENTCI_COMPOSE_PROJECT:-agentci}"
output="${1:-./backups/agentci-$(date +%Y%m%d-%H%M%S)}"

mkdir -p "$output"

docker compose --project-name "$project" exec -T postgres \
  pg_dump -U agentci -d agentci | gzip > "$output/postgres.sql.gz"

docker run --rm \
  -v "${project}_agentci-artifacts:/data:ro" \
  -v "$output:/backup" \
  alpine:3.20 \
  tar czf /backup/artifacts.tar.gz -C /data .

echo "Backup written to $output"
