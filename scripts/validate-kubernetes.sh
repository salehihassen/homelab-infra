#!/usr/bin/env bash
# Render all roots AND child Flux paths. Does not read kubeconfig or use Docker.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
if [[ "${KUBERNETES_TOOLS_READY:-}" != 1 ]]; then
  exec bash scripts/with-kubernetes-tools.sh env KUBERNETES_TOOLS_READY=1 bash scripts/validate-kubernetes.sh
fi
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
mkdir "$work/schemas"
python3 scripts/check-kubernetes.py --schemas "$work/schemas"
for path in kubernetes/clusters/* kubernetes/databases/* kubernetes/apps/*; do
  name="$(basename "$path")"
  kustomize build "$path" > "$work/$name.yaml"
  # Strict substitution is also enforced by the in-cluster controller. Render
  # the only bootstrap input using an inert URL, never any host-local settings.
  python3 - "$work/$name.yaml" <<'PY'
from pathlib import Path
import sys
import yaml
p = Path(sys.argv[1])
docs = list(yaml.safe_load_all(p.read_text()))
for doc in docs:
    if doc.get('metadata', {}).get('annotations', {}).get('kustomize.toolkit.fluxcd.io/substitute') == 'disabled':
        continue
    s = yaml.safe_dump(doc).replace('${GIT_REPOSITORY_URL}', 'ssh://git@github.com/example/homelab-infra.git')
    assert '${' not in s, f'Unresolved variable in {p}'
    doc.clear()
    doc.update(yaml.safe_load(s))
p.write_text(yaml.safe_dump_all(docs))
PY
  # Generated upstream CRD definitions are API-checked in the runtime rehearsal;
  # custom resources themselves are validated against their vendored schemas.
  kubeconform -strict -summary -skip CustomResourceDefinition -kubernetes-version 1.37.0 \
    -schema-location default \
    -schema-location "$work/schemas/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json" \
    "$work/$name.yaml"
done
python3 scripts/check-kubernetes.py
bash scripts/check-private.sh
