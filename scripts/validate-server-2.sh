#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
host_dir="$PWD/hosts/server-2"
compose() {
  STACK_ENV_FILE=.env.example STACK_ROUTING_ENV_FILE=routing.env.example \
    docker compose --env-file "$host_dir/.env.example" --project-directory "$host_dir" -f "$host_dir/compose.yaml" "$@"
}
image() { compose config --no-env-resolution --format json | python3 -c 'import sys,json;print(json.load(sys.stdin)["services"][sys.argv[1]]["image"])' "$1"; }
validate_compose() {
  compose config --quiet --no-env-resolution
  python3 scripts/check-stacks.py
  compose config --no-env-resolution --format json | python3 scripts/check-networks.py
  compose config --no-env-resolution --format json | python3 scripts/check-server-2.py compose
  python3 -m py_compile hosts/server-2/backup/*.py
}
validate_caddy() (
  settings="$(mktemp)"; trap 'rm -f "$settings"' EXIT
  python3 - "$host_dir" "$settings" <<'PY'
from pathlib import Path
import sys
lines=[]
for p in sorted(Path(sys.argv[1]).glob('*/routing.env.example')):
    lines.extend(l for l in p.read_text().splitlines() if l and not l.startswith('#'))
Path(sys.argv[2]).write_text('\n'.join(lines)+'\n')
PY
  docker run --rm --network none --read-only --tmpfs /tmp --entrypoint caddy --env-file "$settings" \
    --mount "type=bind,src=$host_dir/caddy/Caddyfile,dst=/etc/caddy/Caddyfile,readonly" \
    "$(image caddy)" validate --config /etc/caddy/Caddyfile
  docker run --rm --network none --read-only --tmpfs /tmp --entrypoint caddy --env-file "$settings" \
    --mount "type=bind,src=$host_dir/caddy/Caddyfile,dst=/etc/caddy/Caddyfile,readonly" \
    "$(image caddy)" adapt --config /etc/caddy/Caddyfile | python3 scripts/check-server-2.py routing
)
validate_alloy() {
  docker run --rm --network none --read-only \
    --mount "type=bind,src=$host_dir/observability/alloy-config.alloy,dst=/etc/alloy/config.alloy,readonly" \
    "$(image alloy)" fmt --test /etc/alloy/config.alloy
  docker run --rm --network none --read-only --env HOST_LABEL=server-2 \
    --env LOKI_PUSH_URL=https://logs.private.example/loki/api/v1/push --env LOKI_USERNAME=logshipper \
    --mount "type=bind,src=$host_dir/observability/alloy-config.alloy,dst=/etc/alloy/config.alloy,readonly" \
    "$(image alloy)" validate /etc/alloy/config.alloy
}
case "${1:-all}" in
  compose) validate_compose ;;
  caddy-build) docker pull "$(image caddy)" ;;
  caddy) validate_caddy ;;
  alloy) validate_alloy ;;
  private) bash scripts/check-private.sh ;;
  all) validate_compose; validate_caddy; validate_alloy; bash scripts/check-private.sh ;;
  *) echo 'Usage: INFRA_HOST=server-2 bash scripts/validate-stack.sh [all|compose|caddy|alloy|private]' >&2; exit 2 ;;
esac
