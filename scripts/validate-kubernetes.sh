#!/usr/bin/env bash
# Offline validation of kubernetes/: render every folder, check it against the
# Kubernetes and Flux schemas, then apply the rules in check-kubernetes.py.
# Reads no kubeconfig and uses no Docker.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
if [[ "${KUBERNETES_TOOLS_READY:-}" != 1 ]]; then
  exec bash scripts/with-kubernetes-tools.sh env KUBERNETES_TOOLS_READY=1 bash scripts/validate-kubernetes.sh
fi

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
placeholder_git_url='ssh://git@github.com/example/homelab-infra.git'
# Kubernetes schemas are downloaded once, then reused offline.
schema_cache="${XDG_CACHE_HOME:-$HOME/.cache}/homelab-infra/kubernetes/schemas"
mkdir -p "$schema_cache"

render() {
  kustomize build "$1" | sed "s|\${GIT_REPOSITORY_URL}|${placeholder_git_url}|g"
}

check_schemas() {
  kubeconform -strict -summary -kubernetes-version 1.37.0 -cache "$schema_cache" \
    -skip CustomResourceDefinition \
    -schema-location "$work/schemas/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json" \
    -schema-location default \
    "$1"
}

mkdir "$work/schemas"
python3 scripts/check-kubernetes.py --export-schemas "$work/schemas"

for folder in kubernetes/infrastructure/* kubernetes/clusters/* kubernetes/databases/* kubernetes/storage/* kubernetes/apps/*; do
  rendered="$work/$(basename "$folder").yaml"
  render "$folder" > "$rendered"
  check_schemas "$rendered"
done

python3 scripts/check-kubernetes.py
bash scripts/check-private.sh
