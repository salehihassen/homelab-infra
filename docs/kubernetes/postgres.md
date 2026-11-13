# Shared public PostgreSQL (CloudNativePG)

The public cluster runs one PostgreSQL 18 instance, managed by the
[CloudNativePG](https://cloudnative-pg.io) operator, for all of its apps. Each
app gets its own role and database; apps never share credentials, and there is
no network superuser login.

| Git path | What it holds |
|---|---|
| `kubernetes/infrastructure/cloudnative-pg` | The operator (CloudNativePG 1.30.1, vendored by `scripts/render-cloudnative-pg.sh`) |
| `kubernetes/databases/postgres/cluster.yaml` | The `Cluster`: 1 instance, image pinned by digest, 10 GiB on `local-retain` |
| `kubernetes/databases/postgres/app-databases/<app>.yaml` | One file per app: its `DatabaseRole` and `Database` |
| `kubernetes/databases/postgres/network-policy.yaml` | Default deny; 5432 only from Pods labelled `homelab.example/postgres-client: "true"` |

Flux applies these in order: Kustomization `cloudnative-pg` (the operator),
then `postgres` (which `dependsOn` it and waits until the `Cluster` is Ready and
every role and database is applied), then each app (which `dependsOn: postgres`).

The operator turns the `Cluster` into Pod `postgres-1`, its data volume
`postgres-1`, and Services `postgres-rw` (read-write, what apps use),
`postgres-ro` and `postgres-r`. Apps connect to
`postgres-rw.postgres.svc.cluster.local:5432`. Pod and volume names can change
(for example `postgres-2` after the operator recreates an instance), so select
the primary by label: `-l cnpg.io/cluster=postgres,cnpg.io/instanceRole=primary`.

CloudNativePG also creates a bootstrap database and role named `app` with a
Secret `postgres-app`. Nothing uses them.

## First-time setup

During [setup](bootstrap.md) step 5, label the one node whose disk holds the
data (`/var/lib/rancher/k3s/storage`). Volume sizes are not quotas, so watch
that disk's free space:

```bash
kubectl --context "$PUBLIC_CONTEXT" label node public-server homelab.example/local-storage=true
kubectl --context "$PUBLIC_CONTEXT" get nodes -l homelab.example/local-storage=true
```

Losing that node takes the database, and every app using it, offline. There is
no superuser password: administer through `kubectl exec` into the primary Pod.

## Add an app

1. **Git:** add `app-databases/<app>.yaml` (copy `kaneo.yaml`, change the
   names) and list it in `kubernetes/databases/postgres/kustomization.yaml`.
2. **Password Secret in the `postgres` namespace.** CloudNativePG needs the
   `kubernetes.io/basic-auth` type, and only watches Secrets labelled
   `cnpg.io/reload=true` for later password changes:

   ```bash
   app=kaneo
   umask 077; mkdir -p "local/public-edge-inputs/$app"
   password="local/public-edge-inputs/$app/db-password"
   [ -e "$password" ] || openssl rand -hex 32 | tr -d '\n' > "$password"
   kubectl --context "$PUBLIC_CONTEXT" apply -f kubernetes/databases/postgres/namespace.yaml
   kubectl --context "$PUBLIC_CONTEXT" -n postgres create secret generic "$app-db-password" \
     --type=kubernetes.io/basic-auth --from-literal=username="$app" --from-file=password="$password"
   kubectl --context "$PUBLIC_CONTEXT" -n postgres label secret "$app-db-password" cnpg.io/reload=true
   ```

3. **The same password in the app's own Secret**, in the app's namespace
   (Kubernetes Secrets cannot be read across namespaces). The app's setup doc
   covers this.
4. **The app's manifests:** label its Pods `homelab.example/postgres-client: "true"`,
   allow egress to namespace `postgres`, Pods `cnpg.io/cluster: postgres`, TCP
   5432, connect to `postgres-rw.postgres.svc.cluster.local` as `<app>`, and add
   `dependsOn: [{name: postgres}]` to its Flux Kustomization.

After merge, Flux applies the files and the operator creates the role and
database. Check them with:

```bash
kubectl --context "$PUBLIC_CONTEXT" -n postgres get cluster,databaserole,database
```

**Rotate a password** by changing it in both Secrets, then restarting the app.
The operator applies the new password within seconds.

Removing an app's file from Git does not drop its role or database (both use
`retain` reclaim policies, which `check-kubernetes.py` enforces). Retire an
app's data deliberately, after a backup.

## Backups

Back up each app's database natively, together with that app's credentials and
settings from the same point in time. See [Kaneo recovery](kaneo.md) for the
worked example. A copy of the data directory is not a backup.

CloudNativePG can also stream continuous backups with point-in-time recovery
to S3-compatible storage such as B2, through its barman-cloud plugin. That is a
separate component with its own memory cost, deferred for now.

## Versions and upgrades

- **Operator:** change the version and checksum in
  `scripts/render-cloudnative-pg.sh`, rerun it, update the image digest in
  `kubernetes/infrastructure/cloudnative-pg/kustomization.yaml`, then run
  `bash scripts/validate-kubernetes.sh`. Read the release notes first.
- **PostgreSQL minor versions** (18.6 to 18.7): change `imageName` (tag and
  digest). The operator restarts the instance on the new image.
- **Major versions** (18 to 19) do not upgrade in place by changing the image.
  Follow CloudNativePG's major-upgrade procedure or a tested dump and restore,
  and keep the old volume until the new one passes acceptance.

One instance is intentional while there is one storage node. More instances
add streaming replicas and automatic failover, but they need a second node
with its own storage; see the [roadmap](roadmap.md).

## Limits of this design

- `postgres-from-clients` allows any Pod that carries the label. A Pod still
  needs a role's password to log in.
- New databases keep PostgreSQL's default `CONNECT` grant to `PUBLIC`, so any
  role can connect to any database, but cannot read tables it does not own.
- The operator's namespace, `cnpg-system`, has no NetworkPolicies: it must
  reach the Kubernetes API and receive admission webhook calls from it.

References: [CloudNativePG docs](https://cloudnative-pg.io/documentation/current/),
[declarative database management](https://cloudnative-pg.io/documentation/current/declarative_database_management/),
[PostgreSQL major upgrades](https://www.postgresql.org/docs/18/upgrading.html).
