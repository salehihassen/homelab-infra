# Public K3s / Flux pilot

This first step adds one public cluster, a shared PostgreSQL instance and a
fresh Kaneo instance with its own database there. Existing services stay in Compose. The private cluster
and its pilot belong to a later MR.

The [migration roadmap](../docs/kubernetes/roadmap.md) keeps both supplied plans
as the target: independent public/private clusters, then tested public serving
continuity with one control-plane server. It records the current staging choices
and the checks required before progressing.

Read the [architecture](../docs/kubernetes/architecture.md), follow
[bootstrap](../docs/kubernetes/bootstrap.md) and
[new Kaneo setup](../docs/kubernetes/kaneo.md), then work through the
[public-cluster tests](../docs/kubernetes/testing.md).
See [GitOps ownership and dashboard access](../docs/kubernetes/gitops.md) for
how Flux Operator, Flux and Mission Control fit together.

- `clusters/public-edge`: public Flux root, tracking protected main.
- `infrastructure/cloudnative-pg`: rendered, pinned CloudNativePG 1.30.1 operator.
- `databases/postgres`: shared PostgreSQL 18 `Cluster` in namespace `postgres`,
  its StorageClass and policies, and one file per app under `app-databases/`.
  Setup: [postgres.md](../docs/kubernetes/postgres.md).
- `apps/kaneo`: Kaneo namespace, Deployment, Service and policies. Runtime files use
  temporary `emptyDir` storage; its Flux reconciliation depends on postgres.
  Setup: [kaneo.md](../docs/kubernetes/kaneo.md).
- `infrastructure/flux-operator`: rendered, pinned Flux Operator 0.62.1 chart
  with its read-only Mission Control UI and private-access policy.
- `clusters/public-edge/flux-instance.yaml`: Flux 2.10.0 controller selection,
  generated Git/root sync and controller/policy customizations.
- `schemas`: validation-only Flux CRD snapshot, never applied to a cluster.

Each new app or database adds its entry here with a link to its setup doc.
Bootstrap points to this list instead of naming workloads.

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
Mission Control separately uses `service/flux-operator 9080:9080`, or the
tailnet-only Caddy port; it remains an administrative tool with no public route.
