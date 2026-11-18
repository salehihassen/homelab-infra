# Public K3s / Flux pilot

The public cluster on server-2 runs shared PostgreSQL and Kaneo. Other
services run in Compose while the fleet migrates to K3s. The private cluster
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
- `storage/garage`: shared Garage object store in namespace `garage`, one node
  with one bucket per app (today Kaneo's attachments).
  Setup: [garage.md](../docs/kubernetes/garage.md).
- `apps/kaneo`: Kaneo namespace, Deployment, Service and policies. Runtime files use
  temporary `emptyDir` storage and attachments live in Garage; its Flux
  reconciliation depends on postgres and garage.
  Setup: [kaneo.md](../docs/kubernetes/kaneo.md).
- `apps/twenty`: multi-workspace CRM with one server, worker and Redis replica.
  [Required inputs](#twenty-inputs).
- `infrastructure/flux-operator`: rendered, pinned Flux Operator 0.62.1 chart
  with its read-only Mission Control UI and private-access policy.
- `clusters/public-edge/flux-instance.yaml`: Flux 2.10.0 controller selection,
  generated Git/root sync and controller/policy customizations.
- `schemas`: validation-only Flux CRD snapshot, never applied to a cluster.

Each new app or database adds its entry here with a link to its setup doc.
A new folder also goes in the sparse checkout list in `flux-instance.yaml`.
Right after the merge, its Kustomization can report "path not found" until
Flux fetches again; `flux reconcile source git flux-system` speeds that up.
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

## Twenty inputs

Before merging, create `twenty-db-password` using the [PostgreSQL password step](../docs/kubernetes/postgres.md#add-an-app) with `app=twenty`;
its database already has a separate Flux inventory. Create a dedicated key for
`twenty-uploads` using [Garage's bucket setup](../docs/kubernetes/garage.md#add-a-bucket-for-another-app).
In namespace `twenty`, provision:

| Resource | Keys |
|---|---|
| Secret `twenty-credentials` | `PG_DATABASE_URL` (user/database `twenty`, shared `postgres-rw` host), independent `APP_SECRET` and `ENCRYPTION_KEY` |
| Secret `twenty-storage` | `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY` |
| ConfigMap `twenty-settings` | `SERVER_URL` |

Set `SERVER_URL` to the base domain, e.g. `https://crm.private.example`;
login is at `app.crm.private.example`. Both the base and wildcard subdomains need
DNS/TLS routing to Twenty. Claim the first workspace privately and promote only
the operator's verified `core."user"` row with `"canAccessFullAdminPanel"=true`;
multi-workspace mode does not grant instance admin automatically.
Worker probes check process identity only; Redis queue state is outside the existing backups.
