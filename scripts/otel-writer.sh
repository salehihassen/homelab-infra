#!/usr/bin/env bash
# Manage OTLP writer accounts for the observability stack's OTEL_DOMAIN site.
# Run on the Caddy host. Each account is one untracked file,
# /opt/observability/otel-writers/<name>.caddy, holding `<name> <bcrypt hash>`.
#
#   bash scripts/otel-writer.sh add <name>     # prints `<name>:<password>` once
#   bash scripts/otel-writer.sh remove <name>
#   bash scripts/otel-writer.sh list
#
# The plaintext password is shown only once; put it in the client's
# ~/.config/agent-otel/credentials (see docs/agent-telemetry.md).
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."
host_dir="hosts/${INFRA_HOST:-server-1}"
writers=/opt/observability/otel-writers
command="${1:-}"
name="${2:-}"

reload_caddy() {
  # Same reload as RUNBOOK.md: re-render site imports, then reload in place.
  docker compose --project-directory "$host_dir" --file "$host_dir/compose.yaml" \
    exec -T caddy /bin/sh /etc/caddy/render-sites.sh caddy reload --config /etc/caddy/Caddyfile
}

case "$command" in
  add)
    [[ "$name" =~ ^[a-z0-9][a-z0-9-]{0,62}$ ]] || { echo "Name must match [a-z0-9-]" >&2; exit 2; }
    [[ ! -e "$writers/$name.caddy" ]] || { echo "$name already exists; remove it first" >&2; exit 1; }
    password="$(openssl rand -hex 24)"
    image="$(docker inspect --format '{{.Config.Image}}' \
      "$(docker compose --project-directory "$host_dir" --file "$host_dir/compose.yaml" ps -q caddy)")"
    hash="$(docker run --rm --network none --entrypoint caddy "$image" hash-password --plaintext "$password")"
    (umask 027; printf '%s %s\n' "$name" "$hash" > "$writers/$name.caddy")
    reload_caddy >/dev/null
    echo "Added writer '$name'. Credentials (shown once):" >&2
    printf '%s:%s\n' "$name" "$password"
    ;;
  remove)
    [[ -n "$name" && -e "$writers/$name.caddy" ]] || { echo "No writer '$name'" >&2; exit 1; }
    rm -- "$writers/$name.caddy"
    reload_caddy >/dev/null
    echo "Removed writer '$name'." >&2
    ;;
  list)
    for file in "$writers"/*.caddy; do
      [[ -e "$file" ]] && basename "$file" .caddy
    done
    ;;
  *)
    echo "Usage: bash scripts/otel-writer.sh add|remove <name> | list" >&2
    exit 2
    ;;
esac
