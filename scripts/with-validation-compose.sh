#!/usr/bin/env bash
# Run a validation command with CI's Compose, isolated from the host installation.
set -euo pipefail

(($#)) || { echo 'Usage: with-validation-compose.sh <command> [args...]' >&2; exit 2; }
compose_version=5.5.1
compose_sha256=db1889184726840f75c4f9c001048430d4f25b3be3cb084d3ddd762bc0aed576
real_docker="$(command -v docker)" || { echo 'Validation needs the Docker CLI.' >&2; exit 1; }
installed_version="$(docker compose version --short 2>/dev/null || true)"
if [[ "${installed_version#v}" == "$compose_version" ]]; then
  "$@"
  exit
fi

[[ "$(uname -sm)" == 'Linux x86_64' ]] || {
  echo "Automatic validation Compose is available for Linux x86_64; run validation in CI on this platform." >&2
  exit 1
}
cache="${XDG_CACHE_HOME:-$HOME/.cache}/homelab-infra/compose-$compose_version"
work="$(mktemp -d)"
download=''
trap 'rm -rf "$work"; if [[ -n "$download" ]]; then rm -f "$download"; fi' EXIT
if ! printf '%s  %s\n' "$compose_sha256" "$cache/docker-compose" | sha256sum --check --status 2>/dev/null; then
  mkdir -p "$cache"
  download="$(mktemp "$cache/download.XXXXXX")"
  curl -fsSL -o "$download" \
    "https://github.com/docker/compose/releases/download/v$compose_version/docker-compose-linux-x86_64"
  printf '%s  %s\n' "$compose_sha256" "$download" | sha256sum --check --strict --quiet
  chmod 755 "$download"
  mv "$download" "$cache/docker-compose"
  download=''
fi

# Only this command and its children see the shim. Other Docker operations
# pass through unchanged; no Docker plugin or shell configuration is installed.
mkdir "$work/bin"
{
  printf '#!/usr/bin/env bash\n'
  printf 'if [[ "${1:-}" == compose ]]; then shift; exec %q "$@"; fi\n' "$cache/docker-compose"
  printf 'exec %q "$@"\n' "$real_docker"
} > "$work/bin/docker"
chmod 755 "$work/bin/docker"
printf 'Validation: using cached Compose v%s (host: %s); host installation unchanged.\n' \
  "$compose_version" "${installed_version:-unavailable}" >&2
PATH="$work/bin:$PATH" "$@"
