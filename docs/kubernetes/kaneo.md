# New public Kaneo: inputs, HTTPS and recovery

This is an independent, empty instance. Existing private Compose Kaneo and every
other Compose service keep their current data, credentials and routes. Do not
import personal projects or reuse private Kaneo's database/auth secret for this
pilot. SMTP, OAuth, Redis, uploads/S3 integrations are not added.

Kaneo runs as a Deployment in the `kaneo` namespace. Its `/var/lib/kaneo` and
`/tmp` mounts use `emptyDir`: recreated with each Pod and not backed up. Its
durable records live in the `kaneo` database on the
[shared PostgreSQL](postgres.md), reached at
`postgres-rw.postgres.svc.cluster.local`. Its role and database are declared
in `kubernetes/databases/postgres/app-databases/kaneo.yaml`. Kaneo's Flux Kustomization depends on
`postgres` being Ready.

## Prepare the new instance

Complete this after installing Flux Operator but **before applying the
FluxInstance** in [bootstrap](bootstrap.md), and after the PostgreSQL
[first-time setup](postgres.md#first-time-setup). Commands use the workstation's
public kubeconfig and PUBLIC_CONTEXT, from the repo root. Nothing is printed.

1. **Database password.** Follow [Add an app](postgres.md#add-an-app) step 2
   with `app=kaneo`. This writes `local/public-edge-inputs/kaneo/db-password`
   and creates Secret `kaneo-db-password` in the `postgres` namespace.
2. **Kaneo's own inputs.** Create the namespace, then Kaneo's Secret (the same
   database password plus a new auth secret) and its settings:

   ```bash
   umask 077
   dir=local/public-edge-inputs/kaneo
   export KANEO_CREDENTIALS_FILE="$dir/credentials.env" KANEO_SETTINGS_FILE="$dir/settings.env"
   if [ -e "$KANEO_CREDENTIALS_FILE" ]; then
     echo "Inputs exist; inspect them, do not regenerate." >&2
   else
     printf 'POSTGRES_PASSWORD=%s\nAUTH_SECRET=%s\n' "$(cat "$dir/db-password")" \
       "$(openssl rand -hex 32)" > "$KANEO_CREDENTIALS_FILE"
     printf 'KANEO_CLIENT_URL=http://127.0.0.1:8911\n' > "$KANEO_SETTINGS_FILE"
   fi
   kubectl --context "$PUBLIC_CONTEXT" apply -f kubernetes/apps/kaneo/namespace.yaml
   kubectl --context "$PUBLIC_CONTEXT" -n kaneo create secret generic kaneo-credentials \
     --from-env-file="$KANEO_CREDENTIALS_FILE"
   kubectl --context "$PUBLIC_CONTEXT" -n kaneo create configmap kaneo-settings \
     --from-env-file="$KANEO_SETTINGS_FILE"
   ```

   The initial origin matches the loopback test port. To rotate the database
   password, change it in both Secrets, then restart Kaneo. Changing
   `AUTH_SECRET` signs everyone out.
3. **Save** `local/public-edge-inputs/` and the Flux/age keys in independently
   recoverable storage. Plan native database backup into the existing
   Restic/B2 system and prove a restore with test data before accepting
   valuable writes. Compose backup declarations do not capture Kubernetes state.
4. **Start reconciliation** (bootstrap §4). Flux installs the CloudNativePG
   operator, then PostgreSQL with Kaneo's role and database, then Kaneo. Then
   work through [testing.md](testing.md): claim the administrator,
   verify auth, CRUD, restart survival and Flux drift repair before public
   exposure.

Inputs initially stay operator-managed. To adopt SOPS later, add encrypted
resources to their owning app/database Kustomize path and encrypt only to
the public cluster age recipient. Both reconcilers reference sops-age. Protect
both data/stringData; ConfigMap hostnames are private deployment inputs too.
Never commit private keys or plaintext runtime resources.

## Add only the new public HTTPS route

After local acceptance and a restore drill:

1. Select a new public hostname. Set KANEO_CLIENT_URL in the private settings
   file to its final HTTPS origin (no trailing slash). Apply the updated
   ConfigMap and restart Kaneo so the new origin takes effect:

   ```bash
   kubectl --context "$PUBLIC_CONTEXT" -n kaneo create configmap kaneo-settings \
     --from-env-file="$KANEO_SETTINGS_FILE" --dry-run=client -o yaml \
     | kubectl --context "$PUBLIC_CONTEXT" apply -f -
   kubectl --context "$PUBLIC_CONTEXT" -n kaneo rollout restart deployment/kaneo
   kubectl --context "$PUBLIC_CONTEXT" -n kaneo rollout status deployment/kaneo --timeout=300s
   kubectl --context "$PUBLIC_CONTEXT" -n kaneo get service kaneo
   ```

   Login now needs that HTTPS origin. Loopback remains useful for health checks.
2. The public Caddy container uses host networking. On this same public node it
   can reach Kaneo's actual ClusterIP:5173 through K3s service rules. Set private
   KANEO_K3S_UPSTREAM to that address/port and KANEO_PUBLIC_DOMAIN to the chosen
   hostname in `/opt/caddy/routing.env`, already loaded by Compose Caddy.
   From the VPS and the actual Caddy container, request the upstream's
   `/api/health` before changing routes. The node-to-local-Pod path is permitted
   by Kubernetes policy semantics; this host remains an administrative trust
   boundary. Do not add an allow-all policy to mask a failed path test.
3. The Kaneo site is already in `hosts/server-2/caddy/Caddyfile`. Add both
   variables to `/opt/caddy/routing.env` **before** recreating Caddy: with an
   empty KANEO_PUBLIC_DOMAIN the Caddyfile is invalid and Caddy, with every
   public site, fails to start. Point DNS at the host so Caddy can obtain the
   certificate.
4. Recreate **only Caddy** to load added environment values; reload alone does
   not refresh its process environment. Follow the existing server-2 playbook.
   From the repository root on that host, the targeted Compose operation is:

   ```bash
   docker compose --project-directory hosts/server-2 -f hosts/server-2/compose.yaml up -d --no-deps caddy
   ```

   Run the checks in testing.md against public HTTPS and every pre-existing
   public service. No direct 5173/5432 listener should be exposed.

Do not assume the ClusterIP stays constant after deleting/recreating its Service
or cluster. Update the private upstream during recovery. This same-host bridge
is for the single-node pilot. Redundant ingress and external traffic failover
belong to a later change; Caddy here is a single entry point.

## Capture one coherent recovery set

Use test content for the first rehearsal. Before public use, define RPO/RTO,
retention, backup scheduling and failure alerts. Keep the matching database,
stable auth secret, password and origin settings together. A raw live PostgreSQL
data-directory copy is not a native backup.

Set RECOVERY_DIR to a new private directory **outside tracked source**, and
export the input file paths from preparation. The following workstation
subshell stops only Kaneo, leaving PostgreSQL, its other databases and Compose
services running. Its cleanup resumes reconciliation even if capture fails.
Treat a failing capture as incomplete and never upload it as a valid backup.

```bash
(
  set -euo pipefail
  : "${KUBECONFIG:?}" "${PUBLIC_CONTEXT:?}" "${RECOVERY_DIR:?}" "${KANEO_CREDENTIALS_FILE:?}" "${KANEO_SETTINGS_FILE:?}"
  umask 077
  mkdir -m 0700 "$RECOVERY_DIR"
  cleanup_capture() {
    flux --context "$PUBLIC_CONTEXT" resume kustomization postgres || true
    flux --context "$PUBLIC_CONTEXT" resume kustomization kaneo || true
    flux --context "$PUBLIC_CONTEXT" resume kustomization flux-system || true
    kubectl --context "$PUBLIC_CONTEXT" -n flux-system annotate fluxinstance/flux \
      fluxcd.controlplane.io/reconcile- || true
  }
  trap cleanup_capture EXIT
  kubectl --context "$PUBLIC_CONTEXT" -n flux-system annotate fluxinstance/flux \
    fluxcd.controlplane.io/reconcile=disabled --overwrite
  flux --context "$PUBLIC_CONTEXT" suspend kustomization flux-system
  flux --context "$PUBLIC_CONTEXT" suspend kustomization kaneo
  flux --context "$PUBLIC_CONTEXT" suspend kustomization postgres
  kubectl --context "$PUBLIC_CONTEXT" -n kaneo scale deployment/kaneo --replicas=0
  kubectl --context "$PUBLIC_CONTEXT" -n kaneo wait --for=delete pod \
    -l app.kubernetes.io/name=kaneo --timeout=120s
  primary="$(kubectl --context "$PUBLIC_CONTEXT" -n postgres get pod -o name \
    -l cnpg.io/cluster=postgres,cnpg.io/instanceRole=primary)"
  kubectl --context "$PUBLIC_CONTEXT" -n postgres exec "$primary" -c postgres -- \
    pg_dump -d kaneo --format=custom > "$RECOVERY_DIR/kaneo.dump"
  cp "$KANEO_CREDENTIALS_FILE" "$RECOVERY_DIR/credentials.env"
  cp "$KANEO_SETTINGS_FILE" "$RECOVERY_DIR/settings.env"
  cp "$(dirname "$KANEO_CREDENTIALS_FILE")/db-password" "$RECOVERY_DIR/db-password"
)
flux --context "$PUBLIC_CONTEXT" reconcile kustomization postgres --with-source
flux --context "$PUBLIC_CONTEXT" reconcile kustomization kaneo --with-source
kubectl --context "$PUBLIC_CONTEXT" -n kaneo rollout status deployment/kaneo --timeout=300s
```

Keep the private input files synchronized with any credential/origin changes
before capture. Check capture success and resumed application health explicitly; cleanup errors
must be repaired. Include the complete set in encrypted Restic storage using a
separately configured host capture/transfer job. No scheduler or backup account
is silently installed by this branch. Capture K3s state separately as described
in bootstrap.md; Git alone does not preserve credentials or application data.

## Restore drill and retirement

Restore to an isolated fresh candidate with **no public route**, never over the
running instance or existing Compose Kaneo. Use the same pinned app/PostgreSQL
release and matching recovery credentials. Pause FluxInstance reconciliation
first as in the capture procedure, then suspend root, app and database
reconciliation, scale candidate Kaneo to zero, and wait for its Pod to disappear. Keep candidate
PostgreSQL running.

Review the dump with pg_restore --list. The target database must be empty:
Kaneo creates schema on startup. In the isolated candidate only, create
`kaneo-db-password` from the recovered `db-password`
([Add an app](postgres.md#add-an-app) step 2) and let Flux create the empty
`kaneo` database, then import the custom-format dump as objects owned by
`kaneo`:

```bash
primary="$(kubectl --context "$RESTORE_CONTEXT" -n postgres get pod -o name \
  -l cnpg.io/cluster=postgres,cnpg.io/instanceRole=primary)"
kubectl --context "$RESTORE_CONTEXT" -n postgres exec -i "$primary" -c postgres -- \
  pg_restore -d kaneo --role=kaneo --no-owner --no-acl --exit-on-error < "$RECOVERY_DIR/kaneo.dump"
```

There is no application-file archive to restore. Kaneo regenerates its runtime
files on startup. If S3 uploads are added later, include the object store and
its matching database records in a revised recovery procedure.

RESTORE_CONTEXT must select an isolated candidate kubeconfig; export its
KUBECONFIG explicitly. Never mount or edit a live PostgreSQL volume. Restore the
matching auth secret/password, adapt only the candidate browser origin for
local access, resume database, app and root reconciliation and verify login,
test project, task and restart survival. A successful SQL import alone is not an application
restore drill.

To withdraw the new service, remove only its new Caddy route and preserve its
`kaneo` database, credentials and recovery set. Keep existing private Compose Kaneo
running as its own instance. After public writes, any redeployment needs the
new instance's current data, not an old private database. Never delete PVCs to roll back an
image; retained PVCs do not survive disk failure or careless K3s uninstall.
