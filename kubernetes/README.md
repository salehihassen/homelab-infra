# K3s / Flux pilot

Start with [the architecture RFC](../docs/kubernetes/architecture.md), then
[the bootstrap runbook](../docs/kubernetes/bootstrap.md) and
[Kaneo migration and recovery](../docs/kubernetes/kaneo.md).

- `clusters/private-home`: independent Flux root; disposable Podinfo pilot.
- `clusters/public-edge`: independent Flux root; Kaneo reconciliation paused.
- `apps/`: reusable workload manifests; no host identities or real secrets.
- `operations/`: manual recovery helpers, excluded from Flux reconciliation.
- `infrastructure/flux-system`: vendored Flux 2.10.0 controllers, shared by
  both independent installations. Per-cluster `flux-system/` adds Git sync.
- No cluster has been bootstrapped. Follow the runbook to supply each cluster's
  separate credentials and Git URL; these are intentionally absent from Git.

Validation requires Python 3 with PyYAML (activate the local validation venv if
using one). It downloads checksum-pinned Linux x86_64 Kustomize/Kubeconform into
the user cache and fetches versioned schemas. It uses no kubeconfig or cluster
command. From the repository root:

```bash
bash scripts/validate-kubernetes.sh
```

Kaneo has no ingress policy allowing web traffic yet. First use loopback-only
`kubectl port-forward`, then design/review the real Caddy path. No real service
has migrated until the cutover and restore acceptance checks pass.
