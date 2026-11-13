#!/usr/bin/env bash
# Validation-only tools. Never replaces host binaries or contacts a cluster.
set -euo pipefail
[[ "$(uname -s)/$(uname -m)" == Linux/x86_64 ]] || {
  echo 'Validation tool cache currently supports Linux x86_64.' >&2; exit 1;
}
cache="${XDG_CACHE_HOME:-$HOME/.cache}/homelab-infra/kubernetes"
mkdir -p "$cache"
fetch_tool() {
  local name="$1" version="$2" url="$3" checksum="$4"
  local dest="$cache/$name-$version" work
  if [[ ! -x "$dest/$name" ]]; then
    work="$(mktemp -d "$cache/download.XXXXXX")"
    curl --fail --silent --show-error --location "$url" -o "$work/archive.tar.gz"
    printf '%s  %s\n' "$checksum" "$work/archive.tar.gz" | sha256sum --check --strict
    tar -xzf "$work/archive.tar.gz" -C "$work" "$name"
    mkdir -p "$dest"
    mv "$work/$name" "$dest/$name"
    rm -rf "$work"
  fi
  export PATH="$dest:$PATH"
}
fetch_tool kustomize 5.8.1 \
  https://github.com/kubernetes-sigs/kustomize/releases/download/kustomize/v5.8.1/kustomize_v5.8.1_linux_amd64.tar.gz \
  029a7f0f4e1932c52a0476cf02a0fd855c0bb85694b82c338fc648dcb53a819d
fetch_tool kubeconform 0.8.0 \
  https://github.com/yannh/kubeconform/releases/download/v0.8.0/kubeconform-linux-amd64.tar.gz \
  9bc2bffbf71f261128533edaf912153948b7ff238f9a531ae6d34466ec287883
exec "$@"
