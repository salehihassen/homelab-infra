#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."
repo_root="$PWD"
host_dir="$repo_root/hosts/${INFRA_HOST:-server-1}"

# Match CI: older Compose versions still load service env files even with
# --no-env-resolution. Never load the deployment host's private env files.
compose_version="$(docker compose version --short)"
if [[ "${compose_version#v}" != "5.5.1" ]]; then
  echo "Docker Compose v5.5.1 is required to match CI (found $compose_version)." >&2
  exit 1
fi

# The host project with example settings only. `--env-file` replaces the host's
# .env symlink; each include selects its stack-local examples.
host_compose() {
  HOST_LABEL=docker-host STACK_ENV_FILE=.env.example \
    STACK_ROUTING_ENV_FILE=routing.env.example docker compose \
    --env-file "$host_dir/.env.example" \
    --project-directory "$host_dir" --file "$host_dir/compose.yaml" "$@"
}

service_image() {
  host_compose config --no-env-resolution --format json | python3 -c \
    'import json, sys; print(json.load(sys.stdin)["services"][sys.argv[1]]["image"])' "$1"
}

validate_compose() {
  host_compose --profile '*' config --quiet --no-env-resolution
  python3 scripts/check-stacks.py
  host_compose config --no-env-resolution --format json | python3 scripts/check-networks.py
  python3 -m unittest discover -s scripts/tests
}

build_caddy() {
  docker build --tag services-caddy-ci --file "$host_dir/caddy/Dockerfile" "$host_dir/caddy"
}

validate_caddy() (
  ci_secrets="$(mktemp -d)"
  trap 'rm -rf "$ci_secrets"' EXIT
  printf '%s\n' 'ci-placeholder' > "$ci_secrets/porkbun_api_key"
  printf '%s\n' 'ci-placeholder' > "$ci_secrets/porkbun_api_secret_key"
  # Exercise a real low-port listener with Caddy's production security settings.
  # Docker's isolated networks normally allow unprivileged low ports, unlike
  # the host network; enforce the host's 1024 threshold for this regression.
  cat > "$ci_secrets/bind-probe.json" <<'JSON'
{"admin":{"disabled":true},"apps":{"http":{"servers":{"probe":{"listen":["127.0.0.1:443"],"automatic_https":{"disable":true},"routes":[{"handle":[{"handler":"static_response","body":"bind-probe"}]}]}}}}}
JSON
  docker run --rm --network none --read-only --user 1000:1000 \
    --cap-drop ALL --cap-add NET_BIND_SERVICE \
    --security-opt no-new-privileges:true \
    --sysctl net.ipv4.ip_unprivileged_port_start=1024 --tmpfs /tmp \
    --mount "type=bind,src=$ci_secrets/bind-probe.json,dst=/etc/caddy/bind-probe.json,readonly" \
    --entrypoint /usr/local/libexec/caddy/sh services-caddy-ci -ec '
      caddy run --config /etc/caddy/bind-probe.json &
      caddy_pid=$!
      trap '\''kill "$caddy_pid" 2>/dev/null || true; wait "$caddy_pid" || true'\'' EXIT
      for attempt in 1 2 3 4 5 6 7 8 9 10; do
        if [ "$(wget -q -O - http://127.0.0.1:443/ 2>/dev/null)" = bind-probe ]; then
          echo "Non-root Caddy low-port listener verified."
          exit 0
        fi
        kill -0 "$caddy_pid" || exit 1
        sleep 0.1
      done
      exit 1
    '
  # Resolve only Caddy using tracked examples, including dotenv interpolation.
  STACK_ROUTING_ENV_FILE=routing.env.example python3 - "$host_dir" "$ci_secrets/caddy.env" <<'PY'
import json
from pathlib import Path
import subprocess
import sys

import yaml

host_dir = Path(sys.argv[1])
doc = yaml.safe_load((host_dir / "caddy/compose.yaml").read_text())
doc["services"]["caddy"].pop("depends_on", None)
result = subprocess.run(
    ["docker", "compose", "--project-directory", str(host_dir / "caddy"),
     "--env-file", str(host_dir / ".env.example"),
     "--env-file", str(host_dir / "caddy/.env.example"), "--file", "-",
     "config", "--format", "json"],
    input=yaml.safe_dump(doc), text=True, capture_output=True, check=True,
)
environment = json.loads(result.stdout)["services"]["caddy"]["environment"]
assert all("\n" not in value for value in environment.values())
Path(sys.argv[2]).write_text("".join(f"{key}={value}\n" for key, value in environment.items()))
PY
  # The real basic_auth hash lives in observability/routing.env; use a throwaway.
  ci_password_hash="$(docker run --rm --network none --read-only --entrypoint caddy \
    services-caddy-ci hash-password --plaintext ci-placeholder | base64 -w0)"
  ci_routing_overrides=()
  ci_caddy() {
    docker run --rm --network none --read-only \
      --tmpfs /tmp:rw,noexec,nosuid,size=16m \
      --env-file "$ci_secrets/caddy.env" \
      --env ACME_CA=https://acme-staging-v02.api.letsencrypt.org/directory \
      --env "CADDY_BIND_ADDRESSES=100.64.0.10 [fd7a:115c:a1e0::10]" \
      --env "LOKI_WRITER_PASSWORD_HASH=$ci_password_hash" \
      "${ci_routing_overrides[@]}" \
      --mount "type=bind,src=$host_dir/caddy/Caddyfile,dst=/etc/caddy/Caddyfile,readonly" \
      --mount "type=bind,src=$host_dir/caddy/render-sites.sh,dst=/etc/caddy/render-sites.sh,readonly" \
      --mount "type=bind,src=$host_dir,dst=/etc/caddy/stacks,readonly" \
      --mount "type=bind,src=$ci_secrets/porkbun_api_key,dst=/run/secrets/porkbun_api_key,readonly" \
      --mount "type=bind,src=$ci_secrets/porkbun_api_secret_key,dst=/run/secrets/porkbun_api_secret_key,readonly" \
      --entrypoint /bin/sh services-caddy-ci /etc/caddy/render-sites.sh \
      caddy "$@" --config /etc/caddy/Caddyfile
  }
  ci_caddy validate
  ci_caddy adapt | python3 scripts/check-routing.py
  # Missing app routing variables must omit that site while keeping ingress valid.
  ci_routing_overrides=(--env SPEAK_DOMAIN=)
  ci_caddy validate
  ci_caddy adapt | python3 scripts/check-routing.py --absent-domain SPEAK_DOMAIN
  # Caddy and the host identity route must also validate without AI gateway routes.
  ci_routing_overrides=(--env CPA_DOMAIN=)
  ci_caddy validate
  ci_caddy adapt | python3 scripts/check-routing.py --absent-domain CPA_DOMAIN
)

validate_alloy() {
  docker run --rm --network none --read-only \
    --mount "type=bind,src=$host_dir/observability/alloy-config.alloy,dst=/etc/alloy/config.alloy,readonly" \
    "$(service_image alloy)" fmt --test /etc/alloy/config.alloy
}

validate_loki() {
  docker run --rm --network none --read-only \
    --mount "type=bind,src=$host_dir/observability/loki-config.yaml,dst=/etc/loki/config.yaml,readonly" \
    "$(service_image loki)" -verify-config=true -config.file=/etc/loki/config.yaml
}

validate_private() {
  bash scripts/check-private.sh
}

case "${1:-all}" in
  compose) validate_compose ;;
  caddy-build) build_caddy ;;
  caddy) validate_caddy ;;
  alloy) validate_alloy ;;
  loki) validate_loki ;;
  private) validate_private ;;
  all)
    validate_compose
    build_caddy
    validate_caddy
    validate_alloy
    validate_loki
    validate_private
    ;;
  *) echo "Usage: bash scripts/validate-stack.sh [all|compose|caddy-build|caddy|alloy|loki|private]" >&2; exit 2 ;;
esac
