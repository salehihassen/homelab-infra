# Public K3s / Flux pilot

This first step adds one public cluster and a fresh Kaneo instance with its own
PostgreSQL database. Existing services stay in Compose. The private cluster
and its pilot belong to a later MR.

The [migration roadmap](../docs/kubernetes/roadmap.md) keeps both supplied plans
as the target: independent public/private clusters, then tested public serving
continuity with one control-plane server. It records the current staging choices
and the checks required before progressing.

Read the [architecture](../docs/kubernetes/architecture.md), follow
[bootstrap](../docs/kubernetes/bootstrap.md) and
[new Kaneo setup](../docs/kubernetes/kaneo.md), then work through the
[public-cluster tests](../docs/kubernetes/testing.md).

- `clusters/public-edge`: public Flux root, tracking protected main.
- `databases/postgres`: PostgreSQL 18 StatefulSet, retained database claim,
  database Services, namespace and baseline/database policies.
- `apps/kaneo`: Kaneo Deployment, Service and egress policy. Runtime files use
  temporary `emptyDir` storage; its Flux reconciliation depends on postgres.
- `infrastructure/flux-system`: vendored Flux 2.10.0 source/Kustomize controllers.

Prepare credentials, storage ownership and the initial local URL before starting
Git reconciliation. PostgreSQL and Kaneo are enabled in Git; applying the root
sync deploys the database before Kaneo. See [database ownership and upgrades](../docs/kubernetes/postgres.md).
No host is installed and no ingress changes merely by merging these files.
No private cluster configuration is included.

Offline validation requires Python 3 with PyYAML and downloads checksum-pinned
Linux x86_64 Kustomize/Kubeconform plus schemas into the user cache. It reads no
kubeconfig and issues no cluster commands. From the repository root:

```bash
bash scripts/validate-kubernetes.sh
```

First access uses loopback-only kubectl port-forward. Public HTTPS is a separate
operator step through the existing Compose Caddy; claim the administrator and
prove backups before enabling it.
