#!/usr/bin/env bash
# Run validate-stack.sh against exactly what is being committed or pushed,
# not the working tree, with the Compose version CI pins.
#
#   bash scripts/validate-snapshot.sh --index [suite]    # staged files (pre-commit)
#   bash scripts/validate-snapshot.sh <commit> [suite]   # a commit (pre-push)
#
# When the host's Compose differs from the pin, a checksum-verified copy of
# the pinned release is cached under ~/.cache and used for this run only.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."
source_rev="${1:?usage: validate-snapshot.sh --index|<commit> [suite]}"
suite="${2:-all}"

compose_version=5.5.1
compose_sha256=db1889184726840f75c4f9c001048430d4f25b3be3cb084d3ddd762bc0aed576

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

if [[ "$(docker compose version --short)" != "$compose_version" ]]; then
  [[ "$(uname -sm)" == "Linux x86_64" ]] || {
    echo "validate-snapshot: install Docker Compose v$compose_version" >&2; exit 1; }
  cache="${XDG_CACHE_HOME:-$HOME/.cache}/homelab-infra/compose-$compose_version"
  if ! echo "$compose_sha256  $cache/docker-compose" | sha256sum --check --status 2>/dev/null; then
    mkdir -p "$cache"
    curl -fsSL -o "$cache/docker-compose.tmp" \
      "https://github.com/docker/compose/releases/download/v$compose_version/docker-compose-linux-x86_64"
    echo "$compose_sha256  $cache/docker-compose.tmp" | sha256sum --check --strict --quiet
    chmod 755 "$cache/docker-compose.tmp"
    mv "$cache/docker-compose.tmp" "$cache/docker-compose"
  fi
  # `docker compose ...` goes to the pinned binary; everything else to docker.
  real_docker="$(command -v docker)"
  mkdir "$work/bin"
  cat > "$work/bin/docker" <<SH
#!/usr/bin/env bash
if [[ "\${1:-}" == compose ]]; then shift; exec "$cache/docker-compose" "\$@"; fi
exec "$real_docker" "\$@"
SH
  chmod 755 "$work/bin/docker"
  PATH="$work/bin:$PATH"
fi

mkdir "$work/tree"
if [[ "$source_rev" == --index ]]; then
  git checkout-index --all --prefix="$work/tree/"
else
  git archive "$source_rev" | tar -x -C "$work/tree"
fi
# The private-identifier patterns are untracked; the hooks run that check on
# the repository itself, so the snapshot's own `private` step skips quietly.
bash "$work/tree/scripts/validate-stack.sh" "$suite" 2> >(grep -v '^check-private: no ' >&2)
